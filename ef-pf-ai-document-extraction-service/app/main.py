import os
import threading
import asyncio
from contextlib import asynccontextmanager
from typing import Dict

from fastapi import FastAPI, status, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from app.middleware.tracing import W3CTracingMiddleware

from app.api.v1.schemas import ProcessingRequest, JobStatus
from app.services.processing_service import process_documents
from app.core.logging_config import log, setup_logging
from app.services.llm_client import llm_client
from app.core.config import settings

from app.extract_pubsub import main as pubsub_main, shutdown_event

from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Histogram

# autodynatrace removed: OneAgent SDK fails to initialise in this pod
# (AgentState: 2 — see startup logs) and the import can shadow our manual
# OTel spans. The OTLP exporter wired up in app.otel_config is the source
# of truth for traces -> Dynatrace.

# Custom Prometheus metrics
DOCS_PROCESSED_TOTAL = Counter("docs_processed_total", "Total number of documents processed.", ["status"])
JOB_PROCESSING_TIME = Histogram("job_processing_duration_seconds", "Time spent processing a job.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown events."""
    # Order matters: setup_logging() must run BEFORE configure_opentelemetry()
    # so that LoggingInstrumentor can inject otelTraceID/otelSpanID into our
    # formatter. The previous order (configure first, then setup_logging in
    # lifespan) caused dictConfig to overwrite the trace-aware format, which
    # is why extraction logs had no trace IDs while classification did.
    setup_logging()
    configure_opentelemetry(app)

    log.info("Application starting up...", extra={"app_version": app.version, "log_level": settings.LOG_LEVEL, "log_format": settings.LOG_FORMAT})

    os.makedirs(settings.TEMP_DIR, exist_ok=True)

    pubsub_thread = None
    if settings.ENABLE_PUBSUB:
        log.info("Starting Pub/Sub consumer in a background thread...")
        loop = asyncio.get_running_loop()
        pubsub_thread = threading.Thread(target=pubsub_main, args=(loop,), daemon=True)
        pubsub_thread.start()
    else:
        log.info("Pub/Sub consumer is disabled.")

    yield
    
    log.info("Application shutting down...")
    if pubsub_thread and pubsub_thread.is_alive():
        log.info("Signaling Pub/Sub consumer to shut down...")
        shutdown_event.set()
        pubsub_thread.join(timeout=20)
        if pubsub_thread.is_alive():
            log.warning("Pub/Sub thread did not shut down gracefully.")
    
    await llm_client.close()
    log.info("Application shutdown complete.")

from app.otel_config import configure_opentelemetry

app = FastAPI(
    title="Document Extraction Service",
    version="1.7.24",
    docs_url="/extract/v1/docs",
    redoc_url="/extract/v1/redoc",
    lifespan=lifespan
)

# OpenTelemetry is configured in lifespan AFTER setup_logging so the
# LoggingInstrumentor format isn't wiped out by dictConfig.

app.add_middleware(W3CTracingMiddleware)

# Instrumentator instance
instrumentator = Instrumentator()
instrumentator.instrument(app).expose(app, endpoint="/extract/v1/metrics")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/extract/v1/health")
async def health_check():
    """
    Provides a simple health check endpoint.
    """
    return {
        "message": "Welcome to the Document Extraction API",
        "version": app.version,
    }

@app.post("/extract/v1/process",
          response_model=JobStatus,
          status_code=status.HTTP_200_OK,
          tags=["Processing"])
async def create_processing_job(request: ProcessingRequest, fastapi_request: Request):
    """
    Synchronously processes documents. If the job ID exists, it returns
    the cached status. Otherwise, it processes, caches, and returns the result.
    """
    job_id = request.job_id
    log_extra = {"job_id": job_id, "doc_count": len(request.documents)}
   
    log.info("Received new synchronous processing request.", extra=log_extra)

    with JOB_PROCESSING_TIME.time():
        try:
            final_status = await process_documents(request, request.request.folder_path, fastapi_request=fastapi_request)
            log.info("Synchronous processing finished.", extra={**log_extra, "final_status": final_status.status})
            DOCS_PROCESSED_TOTAL.labels(status='success').inc(len(request.documents))
            return final_status

        except Exception as e:
            DOCS_PROCESSED_TOTAL.labels(status='failure').inc(len(request.documents))
            log.error(f"An unhandled error occurred in the processing endpoint.", extra=log_extra, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"An unexpected server error occurred: {str(e)}"
            )
