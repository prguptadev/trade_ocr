import os
import logging
from opentelemetry import trace, metrics
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor

def configure_opentelemetry(app=None):
    service_name = os.getenv("OTEL_SERVICE_NAME", "ef-pf-ai-document-extraction-service")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")

    resource = Resource.create(attributes={
        SERVICE_NAME: service_name
    })

    # 1. Traces Setup
    trace_provider = TracerProvider(resource=resource)
    trace_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    trace_processor = BatchSpanProcessor(trace_exporter)
    trace_provider.add_span_processor(trace_processor)
    trace.set_tracer_provider(trace_provider)

    # 2. Logs Setup
    logger_provider = LoggerProvider(resource=resource)
    log_exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")
    log_processor = BatchLogRecordProcessor(log_exporter)
    logger_provider.add_log_record_processor(log_processor)
    set_logger_provider(logger_provider)

    # Attach OTel logging handler to root logger
    otel_handler = LoggingHandler(logger_provider=logger_provider, level=logging.NOTSET)
    logging.getLogger().addHandler(otel_handler)

    # 3. Metrics Setup
    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"))
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # 4. Auto-Instrumentations
    if app:
        FastAPIInstrumentor.instrument_app(app)
        
    HTTPXClientInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)
