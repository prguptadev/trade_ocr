# OpenTelemetry & Dynatrace — End-to-End Guide

This document explains:

1. What OpenTelemetry (OTel) is and why we use it.
2. What Dynatrace is, and how it consumes and visualises our data.
3. The full data path: Python app → OTel SDK → OTel Collector → Dynatrace.
4. Every code change we made in both services (classification + extraction), with file paths and line numbers.
5. The tricky bits we hit (asyncio context loss, lifespan ordering, span shape collisions) and how we fixed them.

Target audience: a new engineer who has never touched OTel before but needs to understand, debug, and extend the instrumentation in this repo.

---

## 1. What is OpenTelemetry?

OpenTelemetry (OTel) is a **vendor-neutral observability framework**. It is a CNCF project that gives you:

- **A standard data model** for three "signals":
  - **Traces** — the path a request takes across services (a tree of spans).
  - **Metrics** — numeric measurements over time (counters, histograms).
  - **Logs** — structured event records, correlated with traces via `trace_id` / `span_id`.
- **A standard wire protocol — OTLP** (OpenTelemetry Protocol) — for shipping that data out of your process.
- **SDKs** in every major language that produce OTLP.
- **Auto-instrumentations** that hook into common libraries (FastAPI, HTTPX, requests, gRPC, etc.) without you writing any code.
- **An optional collector** — a standalone process that sits between your apps and your backend (Dynatrace, Jaeger, Tempo, Honeycomb, etc.) and does routing / transformation / fan-out.

### Why not just use Dynatrace OneAgent / autodynatrace?

We deliberately moved off the `autodynatrace` SDK and OneAgent in the extraction service. Two reasons:

1. **It kept failing to initialise in the pod** (`AgentState: 2` in the startup logs). Importing it would shadow our manual OTel spans, so spans we explicitly created would disappear from traces.
2. **OTel is vendor-neutral** — if we ever swap Dynatrace for Tempo / Honeycomb / Grafana Cloud, we change the *exporter endpoint*, not 200 files of instrumentation. That decoupling is the whole reason OTel exists.

The note in `app/main.py:22-25` of the extraction service is the canonical reference for this decision:

```python
# autodynatrace removed: OneAgent SDK fails to initialise in this pod
# (AgentState: 2 — see startup logs) and the import can shadow our manual
# OTel spans. The OTLP exporter wired up in app.otel_config is the source
# of truth for traces -> Dynatrace.
```

### Key concepts you need to know

| Concept | What it means |
|---|---|
| **Tracer** | The object you ask for spans from. One per module: `tracer = trace.get_tracer(__name__)`. |
| **Span** | One unit of work — a function call, an HTTP request, a Pub/Sub publish. Has a name, start/end time, attributes, and a parent. |
| **Trace** | The tree of spans that share a `trace_id`. Represents one logical request flowing across processes. |
| **SpanKind** | The role of the span: `INTERNAL` (in-process work), `SERVER` (handling an inbound RPC), `CLIENT` (making an outbound RPC), `PRODUCER` (publishing to a queue), `CONSUMER` (receiving from a queue). Dynatrace uses this to draw the icon and orient arrows in the service flow. |
| **Context propagation** | Carrying `trace_id` + `parent_span_id` across process boundaries so that the receiver's span becomes a child of the sender's span. We use the W3C `traceparent` header for HTTP and Pub/Sub message attributes for async. |
| **OTLP** | The wire format. Two transports: gRPC (port 4317) and HTTP/protobuf (port 4318). We use **HTTP/protobuf**. |
| **Instrumentor** | Library glue that auto-creates spans. We use `FastAPIInstrumentor`, `HTTPXClientInstrumentor`, `LoggingInstrumentor`. |
| **Resource** | Metadata attached to every span/metric/log from this process. Most important key: `service.name`. |

---

## 2. What is Dynatrace?

Dynatrace is the commercial APM (Application Performance Monitoring) backend our org uses. For our purposes it does three things:

1. **Ingests OTLP** over HTTP at a tenant-specific endpoint (`/api/v2/otlp/v1/{traces,logs,metrics}`).
2. **Stitches traces** by `trace_id` + `parent_span_id` — spans from Java, Python, etc. land in the same trace tree as long as the IDs line up.
3. **Renders** that data as service flow diagrams, distributed traces ("PurePaths"), service-level dashboards, and log search.

It does *not* require us to use the OneAgent or the Dynatrace SDK. As long as we send valid OTLP, Dynatrace will accept it. That is exactly the contract OTel was designed around.

### The trace tree Dynatrace will draw for one trade case

```
[Java orchestrator: pubsub publish >> classify-topic]   (PRODUCER)
        |
        v
[Python classification: pubsub process >> CLASSIFY_SUB] (INTERNAL)
        |
        +-- [HTTPX: POST to OpenAI]                     (CLIENT, auto)
        |
        +-- [pubsub publish >> output-topic]            (PRODUCER)
                |
                v
[Java orchestrator: pubsub publish >> extract-topic]    (PRODUCER)
        |
        v
[Python extraction: pubsub process >> EXTRACT_SUB]      (INTERNAL)
        |
        +-- [HTTPX: POST to Neev LLM]                   (CLIENT, auto)
        |
        +-- [pubsub publish >> output-topic]            (PRODUCER)
                |
                v
[Java orchestrator: pubsub consume << output-topic]     (CONSUMER)
```

All of this lives under one `trace_id`, and Dynatrace renders it as one PurePath. The "magic" that makes this work is the `traceparent` field travelling on every Pub/Sub message — see Section 5.

---

## 3. The full data path

```
+-------------------------+         OTLP/HTTP        +-------------------------+         OTLP/HTTP        +--------------+
|  Python service         | -----------------------> |  OTel Collector         | -----------------------> |  Dynatrace   |
|  (classify / extract)   |   POST /v1/traces        |  (dynatrace namespace)  |   POST .../api/v2/otlp   |   tenant     |
|  - SDK + Instrumentors  |   POST /v1/logs          |  - receivers: otlp,     |                          |              |
|  - OTLP HTTP exporter   |   POST /v1/metrics       |    prometheus           |                          |              |
+-------------------------+                          |  - exporters:           |                          |              |
                                                     |    otlphttp/dynatrace   |                          |              |
                                                     +-------------------------+                          +--------------+
```

- The app **does not talk to Dynatrace directly.** It talks to the in-cluster OTel Collector.
- The collector is configured by `ef-pf-ai-document-extraction-service/ConfigMap.yaml`.
- The collector terminates TLS to Dynatrace and adds the `Authorization` header from a secret — so the app never sees the Dynatrace API token.
- For Istio meshes, the path `app → collector` is **not** wrapped in mTLS — see `destination-rule-otel.yaml` for the DR that disables mTLS to the collector host, and `otel-service-entry.yaml` for the ServiceEntry that lets the mesh resolve it.

### Where the endpoint comes from

The app reads `OTEL_EXPORTER_OTLP_ENDPOINT` from the environment. Default in code is `http://localhost:4318`, but in the cluster it is set (via Helm values / ConfigMap) to the in-cluster collector URL. The collector itself reads `DT_ENDPOINT` and `DT_API_TOKEN` from env and forwards to Dynatrace:

```yaml
# ConfigMap.yaml:39-47
exporters:
  otlphttp/dynatrace:
     endpoint: "${env:DT_ENDPOINT}"
     headers:
       Authorization: "${env:DT_API_TOKEN}"
```

---

## 4. Per-file code changes

### 4.1 `otel_config.py` — the SDK wiring (both services)

This file does the same job in both services with slightly different style. It is the only place that talks to the OTel SDK directly.

**Classification: `app/otel_config.py` (lines 1-69)**

```python
def configure_opentelemetry(app):
    service_name = os.getenv("OTEL_SERVICE_NAME", "document-classification-service")
    resource = Resource.create({"service.name": service_name})

    # Traces
    provider = TracerProvider(resource=resource)
    otlp_exporter = OTLPSpanExporter()                 # reads OTEL_EXPORTER_OTLP_ENDPOINT
    provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    trace.set_tracer_provider(provider)

    # Logs (OTLP) — ships log records as their own signal
    logger_provider = LoggerProvider(resource=resource)
    set_logger_provider(logger_provider)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter()))
    logging.getLogger().addHandler(LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider))

    # Metrics
    metric_reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[metric_reader]))

    # Auto-instrumentation
    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()
    LoggingInstrumentor().instrument(set_logging_format=True)  # injects otelTraceID / otelSpanID into log records
```

**Extraction: `app/otel_config.py` (lines 1-56)**

Same shape, with one cosmetic difference — the exporters are constructed with explicit `endpoint=f"{endpoint}/v1/{traces|logs|metrics}"` rather than relying on the env var alone. Either form works; the explicit form is easier to read in cluster logs.

**What each block does, in plain English:**

| Block | Purpose |
|---|---|
| `Resource.create({"service.name": ...})` | Tags every span/metric/log with the service name. This is how Dynatrace knows where a span came from. |
| `TracerProvider` + `BatchSpanProcessor` | The factory for `tracer` objects and the queue that batches spans before exporting them. Batching matters — exporting one span per request would tank latency. |
| `OTLPSpanExporter()` | The actual HTTP client to the collector. No endpoint arg → reads `OTEL_EXPORTER_OTLP_ENDPOINT` env var. |
| `FastAPIInstrumentor.instrument_app(app)` | Auto-creates a SERVER span around every FastAPI request. Free distributed traces for HTTP traffic. |
| `HTTPXClientInstrumentor().instrument()` | Auto-creates a CLIENT span around every outgoing httpx call (OpenAI, Neev LLM). |
| `LoggingInstrumentor().instrument(set_logging_format=True)` | Injects `otelTraceID`, `otelSpanID`, `otelServiceName`, `otelTraceSampled` as log-record attributes. Lets us correlate logs ↔ traces. |

### 4.2 `classify_pubsub.py` — manual span around Pub/Sub consume

The Google Pub/Sub client library is **not** auto-instrumented by OTel. We add spans by hand.

`app/classify_pubsub.py:50-114`:

```python
def callback(message):
    # 1. Extract upstream context from message attributes (set by the Java publisher).
    ctx = propagate.extract(message.attributes)

    # 2. Open the consumer span with that context as parent.
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
        # 3. Real work — every HTTPX call inside this block becomes a child span automatically.
        ...

        # 4. Producer span around outgoing publish, with context injected back into attributes.
        publish_attributes = {}
        with tracer.start_as_current_span(
            f"pubsub publish >> {settings.OUTPUT_TOPIC_ID}",
            kind=SpanKind.PRODUCER,
            attributes={"messaging.system": "gcp_pubsub", ...}
        ):
            propagate.inject(publish_attributes)   # writes traceparent into the dict
            publisher.publish(output_topic_path, output_data, **publish_attributes)
```

The four moves to remember:

1. `propagate.extract(message.attributes)` → reads the inbound `traceparent` and gives you a `Context` object.
2. Open a span with `context=ctx` to make it a child of the upstream trace.
3. Work happens inside the `with` block — HTTPX auto-spans become grandchildren automatically.
4. Before publishing the next message, open a `PRODUCER` span and `propagate.inject()` into the outgoing attributes so the next consumer can do step 1.

### 4.3 `extract_pubsub.py` — the same pattern PLUS asyncio context propagation

The extraction service has the same consume → process → publish flow, but with one critical wrinkle: **the actual work runs on a different asyncio event loop**, on a different thread.

`asyncio.run_coroutine_threadsafe(coro, loop)` schedules `coro` to run on `loop`. **It does not carry the OTel context with it.** That means any span created inside `coro` would have no parent and would start a brand-new trace — the exact bug we hit, where every LLM call became its own root.

The fix is in `app/extract_pubsub.py:80-225`:

```python
def callback(message):
    ctx = propagate.extract(message.attributes)

    with tracer.start_as_current_span(
        f"pubsub process >> {EXTRACT_SUB_ID}",
        context=ctx,
        kind=SpanKind.INTERNAL,
        attributes={...}
    ) as span:
        # *** Snapshot the OTel context AFTER the consumer span is current. ***
        consumer_ctx = otel_context.get_current()

        job_id = None                          # pre-declared so the ack-extension thread can read it
        stop_ack_extension = threading.Event()
        # ack-extension thread keeps the message alive while LLM is processing
        ...

        async def _run_with_context():
            # *** Re-attach the snapshot on the target loop. ***
            token = otel_context.attach(consumer_ctx)
            try:
                return await process_documents(...)
            finally:
                otel_context.detach(token)

        future = asyncio.run_coroutine_threadsafe(_run_with_context(), _main_loop)
        job_status = future.result(timeout=settings.API_TIMEOUT)
```

Two non-obvious points:

- **Snapshot AFTER `with start_as_current_span(...)`** — if you snapshot before the `with` opens, you snapshot a context with no consumer span in it, and every grandchild loses its parent.
- **Re-attach inside the coroutine, detach in `finally`** — leaking the token would pollute the loop's "current context" for everything that runs after this coroutine.

Same span-shape rules as classification:

- Consumer: `SpanKind.INTERNAL`, name `pubsub process >> EXTRACT_SUB`, `messaging.destination.name`, `messaging.operation=process`.
- All three producer spans (output / age-skip / DLQ ×2): `SpanKind.PRODUCER`, short topic name in both `name` and `messaging.destination.name`, `messaging.operation=publish`.

#### Why `INTERNAL`, not `CONSUMER`?

OTel's messaging semantic-convention says the receive side should be `SpanKind.CONSUMER` with `messaging.source.name` and `messaging.operation=receive`. We don't use that shape, on purpose.

The reason is **visual collision in the Dynatrace service flow**: the Java orchestrator that consumes our *output* topic also uses `CONSUMER` / `consume <<` shape. If our extraction consumer used the same shape, step 2 and step 5 of the architecture diagram would render identically and the trace tree would look ambiguous.

Parent stitching from the Java producer **does not depend on `SpanKind`** — it depends solely on the `traceparent` value the producer injected into `message.attributes`. So switching to `INTERNAL` / `process` is **purely cosmetic** and doesn't break the trace chain.

See the inline comment at `app/extract_pubsub.py:84-87` for the canonical version of this argument.

### 4.4 `main.py` — lifespan ordering (extraction only)

Classification calls `setup_logging()` at module top, then `configure_opentelemetry(app)` at module top. That works because they happen in deterministic order.

Extraction uses FastAPI's `lifespan` context manager, and **the order matters**:

`ef-pf-ai-document-extraction-service/app/main.py:31-66`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Order matters: setup_logging() must run BEFORE configure_opentelemetry()
    # so that LoggingInstrumentor can inject otelTraceID/otelSpanID into our
    # formatter. The previous order (configure first, then setup_logging in
    # lifespan) caused dictConfig to overwrite the trace-aware format, which
    # is why extraction logs had no trace IDs while classification did.
    setup_logging()
    configure_opentelemetry(app)
    ...
    yield
    ...
```

If you flip these two lines, `LoggingInstrumentor` installs its formatter first, and then `dictConfig` inside `setup_logging` wipes it out. The symptom is "logs have no `trace_id` field" — surprisingly hard to debug without knowing about it.

### 4.5 `logging_config.py` — trace-ID placeholders

For `LoggingInstrumentor` to actually inject `trace_id` into log lines, our formatter has to *ask for it*.

**Extraction `app/core/logging_config.py:43,48`:**

```python
"console": {
    "format": "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] - %(message)s",
},
"json": {
    "format": "%(asctime)s %(name)s %(levelname)s %(message)s %(filename)s %(lineno)d %(otelTraceID)s %(otelSpanID)s %(otelServiceName)s %(otelTraceSampled)s",
},
```

**Classification `app/logging_config.py:16-19`:**

```python
formatter = jsonlogger.JsonFormatter(
    '%(asctime)s %(levelname)s %(name)s %(message)s %(otelTraceID)s %(otelSpanID)s %(otelServiceName)s',
    rename_fields={'asctime':'timestamp'}
)
```

The placeholders `%(otelTraceID)s`, `%(otelSpanID)s`, `%(otelServiceName)s`, `%(otelTraceSampled)s` are *contributed* by `LoggingInstrumentor.instrument(set_logging_format=True)`. If you remove the instrumentor or call it after `dictConfig`, the placeholders will render as the string `None` and you lose log↔trace correlation in Dynatrace.

### 4.6 `middleware/tracing.py` — W3C trace-id from SRN (extraction only)

`app/middleware/tracing.py:1-66`:

This middleware lets the trade-case Service Request Number (SRN) act as the W3C `trace_id`, so an operator can paste an SRN into Dynatrace and find the trace. Resolution order:

1. Read `request.body().data.request.srn_no`. If present → hex-sanitise + pad/trim to 32 chars and use as trace_id.
2. Else honour incoming `traceparent` header.
3. Else generate a fresh UUID.

It also writes a `traceparent` header on the response so downstream callers can chain.

> Note: this only fires for the HTTP path (the `/extract/v1/process` synchronous endpoint). The Pub/Sub path does its own context extraction in `extract_pubsub.py:82`.

### 4.7 Producer spans — all four sites in extraction

The extraction service has *four* PRODUCER spans (versus classification's three) because age-skip publishes the same shape as the success path. All four spans use the same shape — short topic name in span name + `messaging.destination.name`, `messaging.operation=publish`:

| Site | Line | When it fires |
|---|---|---|
| Age-skip publish | `extract_pubsub.py:178` | Message older than `MESSAGE_MAX_AGE_SECONDS` → skip LLM, publish a "skipped" result. |
| Success publish | `extract_pubsub.py:253` | LLM returned OK → publish to output topic. |
| DLQ (validation) | `extract_pubsub.py:286` | `json.JSONDecodeError` or `ValidationError` → publish to DLQ. |
| DLQ (other) | `extract_pubsub.py:316` | Any other exception → publish to DLQ. |

---

## 5. How parent stitching actually works

This is the part most people get wrong, so it's worth spelling out.

**The only thing that ties two spans into one trace is the `traceparent` value.** Not `SpanKind`. Not `messaging.system`. Not `service.name`. Just `traceparent`.

`traceparent` is a string defined by W3C Trace Context (https://www.w3.org/TR/trace-context/) that looks like:

```
00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
^^ ^^                              ^^                ^^
||  trace_id (32 hex chars)         span_id (16 hex)  flags
version
```

The Java orchestrator's PRODUCER span runs, finishes, and before it publishes the Pub/Sub message it does the Java equivalent of `propagate.inject(message.attributes)`. That writes a `traceparent` attribute on the message.

Our Python `callback` does `propagate.extract(message.attributes)`. That reads `traceparent` and builds a `Context` object where the parent span is the Java PRODUCER span.

We then open our consumer span **with that context as the parent**. Dynatrace sees the same `trace_id` on both spans, sees that our consumer's `parent_span_id` matches the Java span's `span_id`, and stitches them.

If `traceparent` is missing or malformed, `propagate.extract` returns an empty context and our consumer span becomes a new root. That is the symptom of "every Pub/Sub message starts a new trace."

---

## 6. Kubernetes / Istio glue

Three files outside the Python codebase that you need to know exist:

### 6.1 `ef-pf-ai-document-extraction-service/ConfigMap.yaml`

Defines the OTel Collector itself. Key parts:

```yaml
receivers:
  otlp:
    protocols:
      grpc: { endpoint: ${env:MY_POD_IP}:4317 }
      http: { endpoint: ${env:MY_POD_IP}:4318 }
exporters:
  otlphttp/dynatrace:
    endpoint: "${env:DT_ENDPOINT}"
    headers:
      Authorization: "${env:DT_API_TOKEN}"
service:
  pipelines:
    traces:  { receivers: [otlp],            exporters: [otlphttp/dynatrace, debug] }
    metrics: { receivers: [otlp,prometheus], exporters: [otlphttp/dynatrace, debug] }
    logs:    { receivers: [otlp],            exporters: [otlphttp/dynatrace] }
```

This is what listens on 4317/4318 inside the cluster and is the thing our `OTEL_EXPORTER_OTLP_ENDPOINT` points at.

### 6.2 `ef-pf-ai-document-classification-service/otel-service-entry.yaml`

An Istio `ServiceEntry` that lets in-mesh pods reach the collector hostname (`uat-otel-collector-svc.dynatrace.svc.cluster.local`). Without it the mesh refuses traffic to that host.

### 6.3 `ef-pf-ai-document-classification-service/destination-rule-otel.yaml`

An Istio `DestinationRule` that **disables mTLS** for traffic to the collector. The collector terminates plain HTTP on 4318; mTLS would break it.

---

## 7. Trace-continuity machinery in one table

When debugging "why is my trace broken?", these are the load-bearing pieces. Touch any of them at your peril.

| What | Where | Why it matters |
|---|---|---|
| `propagate.extract(message.attributes)` | `extract_pubsub.py:82`, `classify_pubsub.py:53` | Parses inbound `traceparent`. Without this, the consumer span starts a new root trace. |
| `context=ctx` arg to `start_as_current_span` | `extract_pubsub.py:90`, `classify_pubsub.py:58` | Makes the consumer span a child of the Java publisher. |
| `consumer_ctx = otel_context.get_current()` | `extract_pubsub.py:101` | Snapshots the context AFTER the consumer span is current, for re-use on the asyncio loop. |
| `otel_context.attach(consumer_ctx)` inside `_run_with_context` | `extract_pubsub.py:219` | Restores the trace on the foreign loop so LLM/HTTP spans become children. |
| `otel_context.detach(token)` in finally | `extract_pubsub.py:223` | Prevents the snapshot from leaking into the next coroutine. |
| `propagate.inject(publish_attributes)` | `extract_pubsub.py:187,262,295,325` and `classify_pubsub.py:109,139,169` | Writes outbound `traceparent` so the next service can stitch. |
| `lifespan` ordering (`setup_logging` → `configure_opentelemetry`) | `extract_pubsub`'s `main.py:39-40` | Order reversal silently kills log↔trace correlation. |
| Formatter placeholders `%(otelTraceID)s` etc. | `logging_config.py:43,48` (extract); `logging_config.py:17` (classify) | Without these, `LoggingInstrumentor` has nothing to fill in. |
| `job_id = None` pre-declaration | `extract_pubsub.py:106` | Stops `UnboundLocalError` from silently disabling the ack-deadline extender. (Not strictly OTel, but baked into the same fix.) |

---

## 8. How Dynatrace renders our data

Once OTLP lands in Dynatrace, three views matter:

1. **Distributed Trace** (sometimes called PurePath). Open a service → click "Traces" → pick a trace. You'll see the tree drawn from Section 2. Each span has its attributes, duration, exceptions (`span.record_exception(e)` calls), and status (`span.set_status(...)`).
2. **Service Flow**. Aggregate view of "what calls what". Built from `SpanKind` and `messaging.*` attributes. This is the one that suffers if `SpanKind` is wrong — for example if our extraction consumer had stayed as `CONSUMER`, the diagram would show two `consume <<` arrows pointing into the same box and be visually confusing.
3. **Logs in context**. Open a trace → "Logs" tab → see every log line whose `otelTraceID` matches. This is what the `LoggingInstrumentor` plumbing in Section 4.5 unlocks. Without those placeholders, this view is empty.

Plus **metrics** (Prometheus counters/histograms we emit via `prometheus_fastapi_instrumentator` — `docs_processed_total`, `documents_classified_total`, `classification_duration_seconds`, `job_processing_duration_seconds`) flow through the same collector pipeline and are searchable as Dynatrace timeseries.

---

## 9. Checklist for adding a new service to this trace tree

If you ever add a third Python service (say, a post-processor) and want it to land in the same trace:

- [ ] Add `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, `opentelemetry-instrumentation-{fastapi,httpx,logging}` to `requirements.txt`.
- [ ] Copy `otel_config.py` and adjust the default service name.
- [ ] If you have a FastAPI app + `lifespan`, copy the extraction-service ordering: `setup_logging()` then `configure_opentelemetry(app)`.
- [ ] Add `%(otelTraceID)s %(otelSpanID)s %(otelServiceName)s` placeholders to every formatter.
- [ ] If you consume Pub/Sub: in your callback, `propagate.extract(message.attributes)`, open a span with `context=ctx, kind=SpanKind.INTERNAL`, name it `pubsub process >> <SUB_ID>`.
- [ ] If you publish Pub/Sub: open a `SpanKind.PRODUCER` span, `propagate.inject(publish_attributes)`, then `publisher.publish(..., **publish_attributes)`.
- [ ] If you cross an asyncio loop boundary: snapshot with `otel_context.get_current()`, re-attach with `otel_context.attach(...)` inside the coroutine, `detach` in finally.
- [ ] Set `OTEL_EXPORTER_OTLP_ENDPOINT` to the in-cluster collector via the deployment manifest.
- [ ] Add Istio `ServiceEntry` + `DestinationRule` for the collector host if your namespace doesn't already have them.

---

## 10. Common failure modes and how to diagnose

| Symptom | Likely cause | Fix |
|---|---|---|
| Every Pub/Sub message is its own root trace. | `propagate.extract` not called, or `traceparent` missing from publisher side. | Check the publisher injects; check `propagate.extract(message.attributes)` runs *before* `start_as_current_span` and is passed in via `context=...`. |
| Trace stitches across services but LLM calls (HTTPX) are orphaned. | Asyncio context not propagated across `run_coroutine_threadsafe`. | Snapshot + attach + detach. See `extract_pubsub.py:101,219,223`. |
| Logs have no `trace_id` field. | Formatter doesn't reference `%(otelTraceID)s`, or `LoggingInstrumentor` ran before `dictConfig` wiped its format. | Add placeholders; ensure `setup_logging()` runs *before* `configure_opentelemetry()` if both happen in `lifespan`. |
| Spans show up but `service.name` is `unknown_service`. | `Resource.create({"service.name": ...})` missing, or `OTEL_SERVICE_NAME` env var not set in deployment. | Set the env var in the Helm chart, or pass the kwarg explicitly. |
| Dynatrace flow diagram has an extra "consume <<" arrow where it shouldn't. | `SpanKind.CONSUMER` on the Pub/Sub callback. | Switch to `SpanKind.INTERNAL` + `messaging.destination.name` + `messaging.operation=process`. See Section 4.3. |
| `AgentState: 2` in pod logs and manual spans missing. | `autodynatrace` / OneAgent import is monkey-patching the world. | Remove the import; rely on OTLP exporter only. See `main.py:22-25` in extraction service. |
| Collector returns 401 to the app. | App is talking to Dynatrace directly instead of the collector — wrong `OTEL_EXPORTER_OTLP_ENDPOINT`. | Set the env var to the in-cluster collector hostname, not the Dynatrace tenant URL. |

---

## 11. References

- W3C Trace Context: https://www.w3.org/TR/trace-context/
- OpenTelemetry Python: https://opentelemetry.io/docs/languages/python/
- OTLP spec: https://github.com/open-telemetry/opentelemetry-proto
- OTel Messaging semantic conventions: https://opentelemetry.io/docs/specs/semconv/messaging/
- Dynatrace OTLP ingest: https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry/getting-started/otlp-export
