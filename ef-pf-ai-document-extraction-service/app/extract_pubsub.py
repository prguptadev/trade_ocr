from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.types import FlowControl
from google.cloud.pubsub_v1.subscriber.scheduler import ThreadScheduler
from concurrent.futures import ThreadPoolExecutor
from pydantic import ValidationError
import json
import logging
import os
import asyncio
import threading
import time
import gc
import concurrent.futures
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Optional

from opentelemetry import trace, propagate, context as otel_context
from opentelemetry.trace import SpanKind

# This variable will hold the running event loop from the main thread.
_main_loop: Optional[asyncio.AbstractEventLoop] = None

from app.api.v1.schemas import ProcessingRequest, JobStatus
from app.services.processing_service import process_documents
from app.core.config import settings
from app.core.logging_config import log
from app.services.llm_client import llm_client

# --- Global Shutdown Event ---
shutdown_event = threading.Event()

tracer = trace.get_tracer(__name__)

ist_tz = ZoneInfo("Asia/Kolkata")

def _sanitize_iso_timestamp(ts_str: str) -> str:
    """
    Truncates nanoseconds to microseconds so Python's datetime can parse it.
    Input: '2026-03-02T16:05:00.123456789+05:30'
    Output: '2026-03-02T16:05:00.123456+05:30'
    """
    if not ts_str or "." not in ts_str:
        return ts_str
    
    # Split into part before decimal and the rest
    base, rest = ts_str.split(".", 1)
    
    # Find where the timezone (+ or - or Z) begins in 'rest'
    tz_match = re.search(r'[+-Z]', rest)
    if tz_match:
        tz_pos = tz_match.start()
        # Take only the first 6 digits of the fractional part
        fractional = rest[:tz_pos][:6]
        tz_part = rest[tz_pos:]
        return f"{base}.{fractional}{tz_part}"
    
    return ts_str

# Pub Sub Configuration

if settings.ENABLE_PUBSUB:

    PROJECT_ID = settings.PROJECT_ID
    EXTRACT_SUB_ID = settings.EXTRACT_SUB_ID
    OUTPUT_TOPIC_ID = settings.OUTPUT_TOPIC_ID
    DLQ_TOPIC_ID = settings.DLQ_TOPIC_ID

    subscriber = pubsub_v1.SubscriberClient()
    publisher = pubsub_v1.PublisherClient()

    extract_sub_path = subscriber.subscription_path(PROJECT_ID, EXTRACT_SUB_ID)
    output_topic_path = publisher.topic_path(PROJECT_ID, OUTPUT_TOPIC_ID)
    dlq_topic_path = publisher.topic_path(PROJECT_ID, DLQ_TOPIC_ID)

else:
    log.warning("Pub/Sub functionality is disabled in this environment.")

def callback(message):
    # 1. Extract context from incoming message attributes
    ctx = propagate.extract(message.attributes)

    # Consumer span linked to the extracted context. Naming/kind/attributes match
    # classify_pubsub.py so the trace reads consistently with the architecture
    # diagram (pt 2 = "pubsub process >> <sub>"). Parent stitching from the Java
    # PRODUCER works via the traceparent in message.attributes, not via SpanKind.
    with tracer.start_as_current_span(
        f"pubsub process >> {EXTRACT_SUB_ID}",
        context=ctx,
        kind=SpanKind.INTERNAL,
        attributes={
            "messaging.system": "gcp_pubsub",
            "messaging.destination.name": EXTRACT_SUB_ID,
            "messaging.operation": "process",
            "messaging.message.id": message.message_id,
        }
    ) as span:
        # Snapshot the OTel context AFTER the consumer span is current so we can
        # re-attach it inside the coroutine that runs on the main asyncio loop.
        consumer_ctx = otel_context.get_current()

        log_extra = {"pubsub_message_id": message.message_id}
        log.info("Consumed message from Pub/Sub subscription.", extra=log_extra)

        job_id = None  # define up-front so the ack-extension thread closure sees it
        stop_ack_extension = threading.Event()

        def extend_ack_deadline():
            # Periodically extend acknowledgement deadline until processing completes
            while not stop_ack_extension.is_set():
                try:
                    message.modify_ack_deadline(60)
                    log.debug(f"Extended ack deadline for message {job_id}", extra=log_extra)
                except Exception as e:
                    log.warning(f"Failed to extend ack deadline: {e}", extra=log_extra)
                stop_ack_extension.wait(30)

        ack_thread = threading.Thread(target=extend_ack_deadline, daemon=True)
        ack_thread.start()

        raw_message = None
        try:
            # Decode and parse the input message as JSON ---
            raw_message = message.data.decode('utf-8')
            log.debug(f"Raw message received: {raw_message}...", extra=log_extra)
            payload = json.loads(raw_message)
            
            decode_count = 1

            while isinstance(payload, str):
                payload = json.loads(payload)
                decode_count += 1
            if decode_count > 1:
                log.debug(f"Message fully decoded in {decode_count} layer(s).", extra=log_extra)

            data = payload.get("data", {})
            job_id = data.get("request").get("srn_no")
            log.info(f"data: ${data}")
            if not job_id:
                log.error("job_id not found in the message payload. Nacking message.", extra=log_extra)
                message.nack()
                span.set_status(trace.Status(trace.StatusCode.ERROR, "job_id not found in message payload"))
                return
            
            log_extra['job_id'] = job_id
            metadata = payload.get("metadata", {})
            log_extra['request_metadata'] = metadata
            published_str = metadata.get('PublishTimeStamp')

            if published_str:
                try:
                    sanitized_ts = _sanitize_iso_timestamp(published_str)
                    published_time = datetime.fromisoformat(sanitized_ts)  # Parses sanitized string correctly
                    now_ist = datetime.now(ist_tz)  # Current IST time explicitly
                    
                    age_seconds = int((now_ist - published_time).total_seconds())
                    max_age = settings.MESSAGE_MAX_AGE_SECONDS
                    
                    log.debug(f"Time check - Published: {published_time}, Now IST: {now_ist}, Age: {age_seconds}s", extra=log_extra)
                    
                    if age_seconds > max_age:
                        skip_status = {
                            'status': 'Skipped',
                            'details': f'Case more than {max_age//60} mins old so not processed',
                            'age_seconds': age_seconds,
                            'max_allowed_seconds': max_age,
                            'published_ist': published_time.isoformat(),
                            'checked_ist': now_ist.isoformat()
                        }
                        output_message = {
                            'metadata': metadata,
                            'data': data,
                            'response': skip_status
                        }
                        output_data = json.dumps(output_message).encode('utf-8')
                        
                        with tracer.start_as_current_span(
                                f"pubsub publish >> {OUTPUT_TOPIC_ID}",
                                kind=SpanKind.PRODUCER,
                                attributes={
                                    "messaging.system": "gcp_pubsub",
                                    "messaging.destination.name": OUTPUT_TOPIC_ID,
                                    "messaging.operation": "publish",
                                }):
                            publish_attributes = {}
                            propagate.inject(publish_attributes)  # injects PRODUCER span context
                            publisher.publish(output_topic_path, output_data, **publish_attributes)

                        message.ack()
                        log.info(f"Skipped old message (age: {age_seconds}s > {max_age}s), job: {job_id}", extra=log_extra)
                        span.set_status(trace.Status(trace.StatusCode.OK, "Message skipped due to age"))
                        return  # Early exit: no LLM
                except ValueError as ve:
                    log.warning(f"Invalid PublishTimeStamp '{published_str}': {ve}, proceeding to LLM", extra=log_extra)
                    span.record_exception(ve)
                    span.set_status(trace.Status(trace.StatusCode.ERROR, str(ve)))
            else:
                log.debug("No PublishTimeStamp found, proceeding to LLM", extra=log_extra)

            log.info(f"Starting processing for job.", extra=log_extra)

            # Validate/process using only the 'data' dict part as per ProcessingRequest schema
            processing_request = ProcessingRequest(**data)
            log.info(f"Processing Request: {processing_request}")
            folder_path = processing_request.request.folder_path

            if not (_main_loop and _main_loop.is_running()):
                error_msg = "The main asyncio event loop is not running or not accessible."
                log.error(error_msg, extra=log_extra)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise RuntimeError(error_msg)

            async def _run_with_context():
                # asyncio.run_coroutine_threadsafe does NOT carry OTel context to
                # the target loop. Re-attach the consumer span context so every
                # LLM/HTTP span underneath becomes a child of the consumer trace
                # instead of starting a new root.
                token = otel_context.attach(consumer_ctx)
                try:
                    return await process_documents(processing_request, folder_path, fastapi_request=None)
                finally:
                    otel_context.detach(token)

            future = asyncio.run_coroutine_threadsafe(_run_with_context(), _main_loop)
            
            try:
                job_status = future.result(timeout=settings.API_TIMEOUT)
            except (concurrent.futures.TimeoutError, asyncio.TimeoutError) as e:
                log.error(f"Job {job_id} timed out after {settings.API_TIMEOUT} seconds. Cancelling task.", extra=log_extra)
                future.cancel()
                span.record_exception(e)
                span.set_status(trace.Status(trace.StatusCode.ERROR, f"Job timed out after {settings.API_TIMEOUT} seconds."))
                raise Exception(f"Job timed out after {settings.API_TIMEOUT} seconds.")
            
            log.info(f"Document processing completed with status: {job_status.status}", extra={**log_extra, "status": job_status.status})

            if job_status.status == "Failed":
                error_msg = f"Document processing failed: {job_status.details}"
                log.error(error_msg, extra=log_extra)
                span.set_status(trace.Status(trace.StatusCode.ERROR, error_msg))
                raise Exception(error_msg)

            # Construct output message with metadata and nested data ---
            output_message = {
                "metadata": metadata,
                "data": data,
                "response": job_status.model_dump()
            }

            output_data = json.dumps(output_message).encode("utf-8")

            with tracer.start_as_current_span(
                    f"pubsub publish >> {OUTPUT_TOPIC_ID}",
                    kind=SpanKind.PRODUCER,
                    attributes={
                        "messaging.system": "gcp_pubsub",
                        "messaging.destination.name": OUTPUT_TOPIC_ID,
                        "messaging.operation": "publish",
                    }):
                publish_attributes = {}
                propagate.inject(publish_attributes)
                publisher.publish(output_topic_path, output_data, **publish_attributes)

            message.ack()
            log.info(f"Extraction message processed successfully.", extra=log_extra)
            span.set_status(trace.Status(trace.StatusCode.OK))

        except (json.JSONDecodeError, ValidationError) as e:

            log.error(f"Invalid message format. Moving to DLQ. Error: {e}", extra=log_extra, exc_info=True)
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))

            dlq_message = {
                "metadata": metadata,
                "data": data,
                "original_message": raw_message,
                "error": str(e),
                "error_type": type(e).__name__,
                "pubsub_message_id": job_id if 'job_id' in locals() else None
            }
            
            dlq_data = json.dumps(dlq_message).encode('utf-8')

            with tracer.start_as_current_span(
                    f"pubsub publish >> {DLQ_TOPIC_ID}",
                    kind=SpanKind.PRODUCER,
                    attributes={
                        "messaging.system": "gcp_pubsub",
                        "messaging.destination.name": DLQ_TOPIC_ID,
                        "messaging.operation": "publish",
                    }):
                publish_attributes = {}
                propagate.inject(publish_attributes)
                publisher.publish(dlq_topic_path, dlq_data, **publish_attributes)
            message.ack()

        except Exception as e:

            log.error(f"Error processing extraction message. Moving to DLQ. Error: {e}", extra=log_extra, exc_info=True)
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))

            dlq_message = {
                "metadata": metadata,
                "data": data,
                "original_message": raw_message,
                "error": str(e),
                "error_type": type(e).__name__,
                "pubsub_message_id": job_id if 'job_id' in locals() else None
            }
                
            dlq_data = json.dumps(dlq_message).encode('utf-8')

            with tracer.start_as_current_span(
                    f"pubsub publish >> {DLQ_TOPIC_ID}",
                    kind=SpanKind.PRODUCER,
                    attributes={
                        "messaging.system": "gcp_pubsub",
                        "messaging.destination.name": DLQ_TOPIC_ID,
                        "messaging.operation": "publish",
                    }):
                publish_attributes = {}
                propagate.inject(publish_attributes)
                publisher.publish(dlq_topic_path, dlq_data, **publish_attributes)
            message.ack()

        finally:
            if not stop_ack_extension.is_set():
                stop_ack_extension.set()
            
            if ack_thread.is_alive():
                ack_thread.join()
            
            # Explicitly trigger garbage collection after processing each message
            collected = gc.collect()
            safe_job_id = locals().get('job_id', 'N/A')
            log.info(f"Garbage collector: collected {collected} objects for job {safe_job_id}.", extra=log_extra if 'log_extra' in locals() else {})

def main(main_loop: asyncio.AbstractEventLoop):
    """Starts the Pub/Sub subscriber and blocks until a shutdown signal is received."""

    global _main_loop
    _main_loop = main_loop

    if not settings.ENABLE_PUBSUB:
        log.info("Pub/Sub is disabled. Exiting.")
        return
    
    flow_control = FlowControl(settings.API_CONCURRENCY_LIMIT)
    executor = ThreadPoolExecutor(settings.API_CONCURRENCY_LIMIT)
    scheduler = ThreadScheduler(executor)
        
    log.info("--- Pub/Sub Consumer Configuration ---", extra={
        "project_id": PROJECT_ID, "subscription_id": EXTRACT_SUB_ID,
        "output_topic_id": OUTPUT_TOPIC_ID, "dlq_topic_id": DLQ_TOPIC_ID,
        "concurrency_limit": settings.API_CONCURRENCY_LIMIT
    })

    streaming_pull_future = subscriber.subscribe(
    extract_sub_path,
    callback=callback,
    flow_control=flow_control,
    scheduler=scheduler
    )
        
    log.info(f"Listening for messages on {extract_sub_path}")

    try:
        while not shutdown_event.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt caught, initiating shutdown...")
        shutdown_event.set()
    finally:
        log.info("Starting graceful shutdown of Pub/Sub consumer...")
        streaming_pull_future.cancel()
        log.info(f"Subscriber pull for {extract_sub_path} cancelled.")
        executor.shutdown(wait=True)
        log.info("Executor shut down.")
        log.info("Pub/Sub consumer shutdown complete.")