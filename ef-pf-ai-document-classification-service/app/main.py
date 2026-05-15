from fastapi import FastAPI, Depends, HTTPException, Request as FastAPIRequest
import logging
import uuid
import time
import os
import atexit
import threading

from .config import settings
from .schemas import ProcessFolderRequest, ClassifiedDocumentsResponse
from .services.workflow_service import WorkflowService
from .services.ai_provider_interface import AIProviderInterface
from .services.openai_provider import OpenAIProvider
from .logging_config import setup_logging
from .otel_config import configure_opentelemetry
from fastapi.middleware.cors import CORSMiddleware

from app.classify_pubsub import main as pubsub_main

from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Histogram


DOCUMENTS_CLASSIFIED_TOTAL = Counter("documents_classified_total", "Total number of documents classified", ["document_type", "confidence_bucket"])
CLASSIFICATION_TIME = Histogram("classification_duration_seconds", "Time spent performing classification.")

# Setup logging once on application startup
setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="AI Trade Document Classification Service",
    description="A service to cluster, classify, and sequence documents from a trade case folder.",
    version="1.3.4",
    docs_url="/classify/v1/docs",
    redoc_url="/classify/v1/redoc"
)

# Initialize OpenTelemetry
configure_opentelemetry(app)

# Prometheus instrumentation
instrumentator = Instrumentator()
instrumentator.instrument(app).expose(app, endpoint="/classify/v1/metrics")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global variables for Pub/Sub management
pubsub_thread = None

if settings.ENABLE_PUBSUB:
    logger.info("Starting Pub/Sub consumer in background")
    pubsub_thread = threading.Thread(target=pubsub_main, daemon=True)
    pubsub_thread.start()
    logger.info("Pub/Sub consumer started.")
else:
    logger.info("Pub/Sub consumer is disabled.")

def cleanup():
    logger.info("Application shutting down...")
atexit.register(cleanup)

# --- Middleware for Request ID and Latency Logging ---
@app.middleware("http")
async def log_requests_middleware(request: FastAPIRequest, call_next):
    job_id = str(uuid.uuid4())
    start_time = time.perf_counter()

    # Make job_id available to the rest of the application
    request.state.job_id = job_id

    log_extra = {
        'job_id': job_id,
        'path': request.url.path,
        'method': request.method,
        'client_ip': request.client.host if request.client else "N/A"
    }
    logger.info("request_started", extra=log_extra)

    response = await call_next(request)

    process_time_ms = (time.perf_counter() - start_time) * 1000
    latency_rounded = round(process_time_ms, 2)
    log_extra['total_latency_ms'] = latency_rounded
    log_extra['status_code'] = response.status_code
    
    log_message = f"Request finished. Status: {response.status_code}, Total Latency: {latency_rounded}ms"
    logger.info(log_message, extra=log_extra)
    
    return response

# --- Dependency Injection ---
def get_ai_provider() -> AIProviderInterface:
    if settings.AI_PROVIDER.lower() == "openai":
        return OpenAIProvider()
    raise ValueError(f"Unsupported AI_PROVIDER configured: {settings.AI_PROVIDER}")

# --- API Endpoints ---
@app.post("/classify/v1/process", response_model=ClassifiedDocumentsResponse, tags=["Document Processing"])
async def process_document_folder(
    request: ProcessFolderRequest,
    fastapi_req: FastAPIRequest,
    ai_provider: AIProviderInterface = Depends(get_ai_provider)
):
    """
    Accepts a folder path, processes all documents within it to cluster,
    classify, and sequence them, and returns a structured JSON result.
    """
    job_id = request.srn_no
    log_extra = {'job_id': job_id}
    logger.info("Initiating processing for folder: %s", request.folder_path, extra=log_extra)
    
    with CLASSIFICATION_TIME.time():
        try:
            workflow = WorkflowService(ai_provider)
            result = workflow.process_folder(request, job_id)
            for doc in result.documents:
                confidence_bucket = "high" if doc.confidence_score >= 90 else "medium" if doc.confidence_score >= 50 else "low"
                DOCUMENTS_CLASSIFIED_TOTAL.labels(document_type=doc.document_type, confidence_bucket=confidence_bucket).inc()
            logger.info("Classification processing finished successfully.", extra=log_extra)
            return result
        except FileNotFoundError as e:
            logger.error("File or folder not found during processing", extra=log_extra, exc_info=True)
            raise HTTPException(status_code=404, detail=str(e))
        except Exception:
            logger.critical("An unhandled exception occurred during document processing.", extra=log_extra, exc_info=True)
            raise HTTPException(status_code=500, detail=f"An internal server error occurred. Please check logs for Request ID: {job_id}")

@app.post("/classify/v1/health", tags=["Health"])
async def health_check():
    """
    Provides a simple health check endpoint.
    """
    return {
        "message": "Welcome to the Document Classification API",
        "version": app.version,
    }
