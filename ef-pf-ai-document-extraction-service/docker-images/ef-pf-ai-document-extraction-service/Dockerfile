FROM enterprisequay.hbctxdom.com/efblr_infra/mcp/python:3.12-bookworm
#FROM enterprisequay.hbctxdom.com/efblr_infra/mcp/python:3.12.11-slim
ARG ARTIFACTORY_USERNAME
ARG ARTIFACTORY_PASSWORD

USER root
#RUN useradd -u 1001 hdfcbank
RUN groupadd -g 1002 hdfcbank || true && \
    useradd -u 1001 -g 1002 -m hdfcbank || true

RUN mkdir -p /app /app/temp_processing
RUN chown -R hdfcbank:hdfcbank /app /app/temp_processing

WORKDIR /app
 
COPY ./requirements.txt .
 
RUN pip install --upgrade pip setuptools wheel \
    -i https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_PASSWORD}@artifactory.hdfcbank.com/artifactory/api/pypi/EF_PF_ENGG_PyPi_Virtual/simple
    
RUN pip install --no-cache-dir -r requirements.txt \
-i https://${ARTIFACTORY_USERNAME}:${ARTIFACTORY_PASSWORD}@artifactory.hdfcbank.com/artifactory/api/pypi/EF_PF_ENGG_PyPi_Virtual/simple
 
COPY . .
 
#RUN mkdir -p /app/temp_processing && \
    #chown -R 1001:1002 /app
USER 1001:1002
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]


