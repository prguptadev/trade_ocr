import os
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
os.environ["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4317"
exporter = OTLPLogExporter()
print("Endpoint:", exporter._endpoint)
print("Insecure:", exporter._insecure)
