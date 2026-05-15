from google.cloud import pubsub_v1
from google.cloud.pubsub_v1.types import FlowControl
from google.cloud.pubsub_v1.subscriber.scheduler import ThreadScheduler
from concurrent.futures import ThreadPoolExecutor
from pydantic import ValidationError

import os
import json
import logging

from app.schemas import ProcessFolderRequest, ClassifiedDocumentsResponse
from app.services.ai_provider_interface import AIProviderInterface
from app.services.workflow_service import WorkflowService
from app.services.openai_provider import OpenAIProvider
from app.logging_config import setup_logging
from app.config import settings

# OpenTelemetry imports
from opentelemetry import trace, propagate
from opentelemetry.trace import SpanKind

tracer = trace.get_tracer(__name__)

if settings.ENABLE_PUBSUB:

    PROJECT_ID = settings.PROJECT_ID
    CLASSIFY_SUB_ID = settings.CLASSIFY_SUB_ID
    OUTPUT_TOPIC_ID = settings.OUTPUT_TOPIC_ID
    DLQ_TOPIC_ID = settings.DLQ_TOPIC_ID

    subscriber = pubsub_v1.SubscriberClient()
    publisher = pubsub_v1.PublisherClient()

    classify_sub_path = subscriber.subscription_path(PROJECT_ID, CLASSIFY_SUB_ID)
    output_topic_path = publisher.topic_path(PROJECT_ID, OUTPUT_TOPIC_ID)
    dlq_topic_path = publisher.topic_path(PROJECT_ID, DLQ_TOPIC_ID)

else:
    logging.warning("Pub/Sub functionality is disabled in this environment.")

def get_ai_provider() -> AIProviderInterface:
    """Creates an AI provider instance based on the configuration."""
    if settings.AI_PROVIDER.lower() == "openai":
        return OpenAIProvider()
    raise ValueError(f"Unsupported AI_PROVIDER configured: {settings.AI_PROVIDER}")

ai_provider = get_ai_provider()
workflow_service = WorkflowService(ai_provider)

def callback(message):
    """Processes a single message from the Pub/Sub subscription."""
    # Extract trace context from the incoming message attributes
    ctx = propagate.extract(message.attributes)

    # Start a consumer span using the extracted context
    with tracer.start_as_current_span(
        f"pubsub process >> {settings.CLASSIFY_SUB_ID}",
        context=ctx,
        kind=SpanKind.INTERNAL,
        attributes={
            "messaging.system": "gcp_pubsub",
            "messaging.destination.name": settings.CLASSIFY_SUB_ID,
            "messaging.operation": "process",
            "messaging.message.id": message.message_id,
        }
    ) as span:
        # Message data is raw bytes arriving from pub/sub subscription
        job_id = message.message_id
        log_extra = {'job_id': job_id}

        logging.info("Consumed message from Pub/Sub subscription.", extra=log_extra)

        raw_message = None
        try:
            # Decode and parse the input message as JSON ---
            raw_message = message.data.decode('utf-8')
            logging.debug(f"Raw message received: {raw_message}", extra=log_extra)
            payload = json.loads(raw_message) 

            # Extract and retain metadata -----
            metadata = payload.get("metadata", {})
            data = payload.get("data", {})
            
            logging.info(f"Starting document processing for job.", extra=log_extra)

            request_model = ProcessFolderRequest(**data)  
            response: ClassifiedDocumentsResponse = workflow_service.process_folder(request_model, job_id)

            logging.info(f"Completed document processing for job.", extra=log_extra)

            output_message = {
                "metadata": metadata,                      
                "data": response.model_dump()              
            }
            output_data = json.dumps(output_message).encode("utf-8")

            # Start a producer span before publishing to the output topic
            publish_attributes = {}
            with tracer.start_as_current_span(
                f"pubsub publish >> {settings.OUTPUT_TOPIC_ID}",
                kind=SpanKind.PRODUCER,
                attributes={
                    "messaging.system": "gcp_pubsub",
                    "messaging.destination.name": settings.OUTPUT_TOPIC_ID,
                    "messaging.operation": "publish",
                }
            ):
                # Inject trace context into the outgoing message attributes
                propagate.inject(publish_attributes)
                publisher.publish(output_topic_path, output_data, **publish_attributes)
            
            message.ack()
            logging.info("Classify message processed successfully.", extra=log_extra)

        except (json.JSONDecodeError, ValidationError) as e: 
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))

            dlq_message = {
                "metadata": metadata if 'metadata' in locals() else {},
                "data": data if 'data' in locals() else {},
                "original_message": raw_message,
                "error": str(e),
                "error_type": type(e).__name__,
                "pubsub_message_id": job_id
            }
            dlq_data = json.dumps(dlq_message).encode("utf-8")

            publish_attributes = {}
            with tracer.start_as_current_span(
                f"pubsub publish >> {settings.DLQ_TOPIC_ID}",
                kind=SpanKind.PRODUCER,
                attributes={
                    "messaging.system": "gcp_pubsub",
                    "messaging.destination.name": settings.DLQ_TOPIC_ID,
                    "messaging.operation": "publish",
                }
            ):
                propagate.inject(publish_attributes)
                logging.error(f"Invalid message format. Moving to DLQ. Error: {e}", extra=log_extra, exc_info=True)
                publisher.publish(dlq_topic_path, dlq_data, **publish_attributes)
                
            message.ack()

        except Exception as e: 
            span.record_exception(e)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))

            dlq_message = {
                "metadata": metadata if 'metadata' in locals() else {},
                "data": data if 'data' in locals() else {},
                "original_message": raw_message,
                "error": str(e),
                "error_type": type(e).__name__,
                "pubsub_message_id": job_id
            }
            dlq_data = json.dumps(dlq_message).encode("utf-8")

            publish_attributes = {}
            with tracer.start_as_current_span(
                f"pubsub publish >> {settings.DLQ_TOPIC_ID}",
                kind=SpanKind.PRODUCER,
                attributes={
                    "messaging.system": "gcp_pubsub",
                    "messaging.destination.name": settings.DLQ_TOPIC_ID,
                    "messaging.operation": "publish",
                }
            ):
                propagate.inject(publish_attributes)
                logging.error(f"Error processing classification message. Moving to DLQ. Error: {e}", extra=log_extra, exc_info=True)
                publisher.publish(dlq_topic_path, dlq_data, **publish_attributes)
                
            message.ack()

def main():
    """Starts the Pub/Sub subscriber."""
   
    # Setup structured logging
    setup_logging()

    if not settings.ENABLE_PUBSUB:
        logging.info("Pub/Sub is disabled. Exiting.")
        return

    flow_control = FlowControl(max_messages=1)
    executor = ThreadPoolExecutor(max_workers=1)
    scheduler = ThreadScheduler(executor)

    logging.info("--- Pub/Sub Consumer Configuration ---", extra={
        "project_id": PROJECT_ID, "subscription_id": CLASSIFY_SUB_ID,
        "output_topic_id": OUTPUT_TOPIC_ID, "dlq_topic_id": DLQ_TOPIC_ID
    })
    
    streaming_pull_future = subscriber.subscribe(
        classify_sub_path, 
        callback=callback,
        flow_control=flow_control,
        scheduler=scheduler
    )

    logging.info(f"Listening for messages on {classify_sub_path}")
    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt caught, initiating shutdown...")
    except Exception as e:
        logging.exception("Exception occurred during Pub/Sub startup.")
    finally:
        logging.info("Starting graceful shutdown...")
        streaming_pull_future.cancel()
        logging.info(f"Subscriber pull for {classify_sub_path} cancelled.")
        
        logging.info("Shutting down the executor...")
        executor.shutdown(wait=True)
        logging.info("Executor shut down.")
        logging.info("Pub/Sub consumer shutdown complete.")

if __name__ == "__main__":
    main()