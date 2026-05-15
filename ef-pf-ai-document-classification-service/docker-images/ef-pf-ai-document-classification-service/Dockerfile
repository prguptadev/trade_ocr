FROM enterprisequay.hbctxdom.com/efblr_infra/mcp/python:3.11-slim

ARG ARTIFACTORY_USERNAME
ARG ARTIFACTORY_PASSWORD

USER root
RUN useradd -u 1001 hdfcbank
 
WORKDIR /app
 
COPY ./requirements.txt .

#RUN pip install opentelemetry-distro opentelemetry-exporter-otlp
 
#RUN opentelemetry-bootstrap -a install

RUN pip install --upgrade pip setuptools wheel \
    -i https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_PASSWORD}@artifactory.hdfcbank.com/artifactory/api/pypi/EF_PF_ENGG_PyPi_Virtual/simple
    
RUN pip install --no-cache-dir -r requirements.txt \
-i https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_PASSWORD}@artifactory.hdfcbank.com/artifactory/api/pypi/EF_PF_ENGG_PyPi_Virtual/simple
 
COPY . .

#dynatrace test
ENV AUTOWRAPT_BOOTSTRAP=autodynatrace
ENV AUTODYNATRACE_FORKABLE=True
ENV DT_HOME=/opt/dynatrace/oneagent-paas
ENV LD_PRELOAD=/opt/dynatrace/oneagent-paas/agent/lib64/liboneagentproc.so

#ENV OTEL_EXPORTER_OTLP_ENDPOINT="https://aiopsduat.hdfcbankuat.com/e/4781d889-e844-40ab-aa03-f06b75a08408/api/v2/otlp"
#ENV OTEL_EXPORTER_OTLP_HEADERS="Authorization=
#ENV OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
#ENV OTEL_SERVICE_NAME="python-quickstart"


RUN mkdir -p /app/temp_processing && \
    chown -R 1001:1002 /app

USER hdfcbank
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
