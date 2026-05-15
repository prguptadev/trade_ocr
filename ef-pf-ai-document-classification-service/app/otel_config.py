import os
import logging
from opentelemetry import trace, metrics
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

def configure_opentelemetry(app):
    """
    Configures OpenTelemetry for the application.
    Instruments FastAPI, HTTPX, and Logging.
    """
    # 1. Define Resource
    service_name = os.getenv("OTEL_SERVICE_NAME", "document-classification-service")
    resource = Resource.create({"service.name": service_name})

    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    # 2. Configure Tracer Provider
    provider = TracerProvider(resource=resource)
    
    # 3. Configure OTLP Exporter
    # Expects OTEL_EXPORTER_OTLP_ENDPOINT to be set (e.g., "http://otel-collector:4318")
    otlp_exporter = OTLPSpanExporter()
    
    # 4. Add Processor to Provider
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)
    
    # 5. Set Global Tracer Provider
    trace.set_tracer_provider(provider)

    # --- Configure OTLP Log Exporter ---
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)
    otlp_log_exporter = OTLPLogExporter()
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(otlp_log_exporter))
    
    # Attach OTLP handler to root logger
    handler = LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
    logging.getLogger().addHandler(handler)

    # --- Configure OTLP Metric Exporter ---
    otlp_metric_exporter = OTLPMetricExporter()
    metric_reader = PeriodicExportingMetricReader(otlp_metric_exporter)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # 6. Instrument Libraries
    # Instrument FastAPI
    FastAPIInstrumentor.instrument_app(app)
    
    # Instrument HTTPX (for outgoing OpenAI/API calls)
    HTTPXClientInstrumentor().instrument()
    
    # Instrument Logging (adds trace_id to logs)
    LoggingInstrumentor().instrument(set_logging_format=True)
