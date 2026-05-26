# KT — OpenTelemetry + Dynatrace Integration

**Audience:** Engineers taking over the classification + extraction services.
**Duration:** ~60 min (40 min walkthrough + 20 min Q&A / live demo in Dynatrace).
**Outcome:** After this session you should be able to (a) read a trace in Dynatrace and reason about it, (b) add OTel instrumentation to a new code path, and (c) debug a broken trace.

> Companion document: `OTEL_DYNATRACE.md` (deep dive — concepts, theory, full diagnosis playbook). Read after the session, not before.

---

## 0. Agenda

| # | Topic | Time | Where in the code |
|---|---|---|---|
| 1 | What we wired up — the 30-second version | 3 min | (slide) |
| 2 | Architecture: app → collector → Dynatrace | 5 min | `ConfigMap.yaml` |
| 3 | The SDK bootstrap (`otel_config.py`) | 8 min | both services, `app/otel_config.py` |
| 4 | Auto-instrumentation: what we get for free | 4 min | FastAPI + HTTPX + Logging |
| 5 | Manual spans on Pub/Sub (consume + publish) | 10 min | `classify_pubsub.py`, `extract_pubsub.py` |
| 6 | Cross-loop trace continuity (extraction only) | 6 min | `extract_pubsub.py:101,214-223` |
| 7 | Log↔trace correlation | 4 min | `logging_config.py` files |
| 8 | Istio / K8s glue | 4 min | `ConfigMap.yaml`, two Istio YAMLs |
| 9 | The 4 gotchas we hit | 6 min | (live, in code) |
| 10 | Live demo in Dynatrace | 10 min | Dynatrace UI |
| 11 | Q&A + handoff checklist | rest | — |

---

## 1. What we wired up — 30-second version

Both services emit **traces, logs, and metrics** in OTLP format to an in-cluster **OTel Collector** (in the `dynatrace` namespace), which forwards them to **Dynatrace SaaS**. Every Pub/Sub message carries a W3C `traceparent` so a trade-case trace stitches across Java orchestrator → classification → extraction → orchestrator into **one** Dynatrace PurePath.

Everything goes through **vendor-neutral OpenTelemetry**. There is **no OneAgent, no `autodynatrace`** anywhere in the Python code — we removed it because it kept failing to initialise (`AgentState: 2`) and shadowing our manual spans.

---

## 2. Architecture — the picture to draw on the whiteboard

```
[Java orchestrator]
       |
       | Pub/Sub msg (traceparent injected on attributes)
       v
+-----------------------------+
| classification-service      |
|  - FastAPI                  |
|  - classify_pubsub callback |--HTTPX-> OpenAI
+-----------------------------+
       |
       | OTLP/HTTP (4318)        Pub/Sub publish
       v                                |
+-----------------------------+         |
| OTel Collector              |<-- OTLP/HTTP from extraction too
| (ns: dynatrace)             |
| ConfigMap.yaml              |
+-----------------------------+
       |
       | OTLP/HTTP + Authorization header
       v
[Dynatrace tenant]   (renders PurePath, Service Flow, Logs-in-context)
```

**Three things to call out on the diagram:**
1. The Python apps **never talk to Dynatrace directly** — only to the collector. The Dynatrace API token lives in the collector pod, not in our services.
2. The Pub/Sub message itself is the carrier of the trace — without `traceparent` in the message attributes, the trace would break at every queue boundary.
3. The collector is in a different namespace (`dynatrace`), so we need Istio glue to talk to it.

---

## 3. The SDK bootstrap — `app/otel_config.py`

This is **the only file** in either service that talks to the OTel SDK directly. Same job in both, slightly different style.

### Classification — `ef-pf-ai-document-classification-service/app/otel_config.py:19-68`

Walk the audience through `configure_opentelemetry(app)` in this order:

| Step | Lines | What it does |
|---|---|---|
| Resource | `25-26` | Stamps every signal with `service.name=document-classification-service` (or whatever `OTEL_SERVICE_NAME` env var says). This is how Dynatrace knows which box on the service-flow diagram a span belongs to. |
| Trace exporter | `31-42` | `TracerProvider` + `BatchSpanProcessor` + `OTLPSpanExporter()`. The exporter has **no endpoint arg** — it reads `OTEL_EXPORTER_OTLP_ENDPOINT` from env. |
| Log exporter | `44-52` | Same shape for logs: `LoggerProvider` + `BatchLogRecordProcessor` + `OTLPLogExporter()`. Then `LoggingHandler` is attached to the **root logger** — every `logging.info(...)` anywhere in the codebase now also ships to Dynatrace. |
| Metric exporter | `54-58` | `MeterProvider` + `PeriodicExportingMetricReader` + `OTLPMetricExporter()`. |
| Auto-instrumentation | `62-68` | `FastAPIInstrumentor`, `HTTPXClientInstrumentor`, `LoggingInstrumentor`. Each one monkey-patches its library. |

### Extraction — `ef-pf-ai-document-extraction-service/app/otel_config.py:20-56`

Same structure, with two cosmetic differences:
- Endpoint is passed **explicitly** to each exporter as `f"{endpoint}/v1/traces"` / `/v1/logs` / `/v1/metrics` (lines `30,37,47`). Both forms work — explicit is easier to read in pod logs.
- `FastAPIInstrumentor.instrument_app(app)` is wrapped in `if app:` (line `52`) so the function can be called from a non-FastAPI entrypoint too.

**Key takeaway for the trainee:** if you ever need to switch from Dynatrace to Tempo/Honeycomb/Grafana Cloud, you change **one env var** (`OTEL_EXPORTER_OTLP_ENDPOINT`). No code changes. That's the whole point of OTel.

---

## 4. What we get for free from auto-instrumentation

These three lines at the bottom of `configure_opentelemetry(...)` do most of the work:

```python
FastAPIInstrumentor.instrument_app(app)   # SERVER span around every HTTP request
HTTPXClientInstrumentor().instrument()    # CLIENT span around every outgoing HTTP
LoggingInstrumentor().instrument(set_logging_format=True)  # trace_id into log records
```

What this means in practice:
- Every `POST /extract/v1/process` automatically gets a span with HTTP method, status code, path, latency.
- Every outbound call to OpenAI (classification) or Neev LLM (extraction) automatically becomes a child CLIENT span with request URL, response status, duration.
- Every `log.info(...)` carries `otelTraceID` / `otelSpanID` / `otelServiceName` / `otelTraceSampled` attributes — provided the formatter asks for them (see section 7).

**We did not write any of those spans.** That's the value of auto-instrumentation.

---

## 5. Manual spans on Pub/Sub

The Google Pub/Sub Python client is **not** in the OTel auto-instrumentation list, so we hand-rolled spans around it. The pattern is identical in both services — **memorise this pattern**:

### 5.1 Consumer side — receive a message and open a span

**Classification — `app/classify_pubsub.py:50-66`:**

```python
def callback(message):
    ctx = propagate.extract(message.attributes)      # (a)

    with tracer.start_as_current_span(                # (b)
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
        ...
```

**Extraction — `app/extract_pubsub.py:80-98`:** same code, just `EXTRACT_SUB_ID`.

Four points to teach:
- **(a)** `propagate.extract(message.attributes)` reads the inbound `traceparent` attribute and returns a `Context` object that points at the Java publisher's span.
- **(b)** `context=ctx` makes our new span a **child** of that Java span — that's how the trace stitches across services.
- **`SpanKind.INTERNAL`, not `CONSUMER`** — deliberate. See gotcha #3.
- The `messaging.*` attribute names match OTel's semantic conventions so Dynatrace's service-flow diagram knows it's a queue boundary.

### 5.2 Producer side — publish a message and open a span

**Classification — `app/classify_pubsub.py:97-110`** (and again at `130-141`, `160-171` for the two DLQ paths):

```python
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
    propagate.inject(publish_attributes)   # writes traceparent into dict
    publisher.publish(output_topic_path, output_data, **publish_attributes)
```

**Extraction has four producer spans** (one more than classification because of the age-skip path):

| Site | Line | Purpose |
|---|---|---|
| Age-skip publish | `extract_pubsub.py:178-188` | Message older than `MESSAGE_MAX_AGE_SECONDS` → skip LLM, publish "Skipped". |
| Success publish | `extract_pubsub.py:253-263` | LLM OK → publish to output topic. |
| DLQ (validation) | `extract_pubsub.py:286-296` | `json.JSONDecodeError` or `ValidationError`. |
| DLQ (other) | `extract_pubsub.py:316-326` | Any other exception. |

**The pattern is identical everywhere.** Memorise the four moves:

1. Open `PRODUCER` span.
2. `propagate.inject(publish_attributes)` — writes outbound `traceparent` into the dict.
3. `publisher.publish(..., **publish_attributes)` — sends it on the wire.
4. Span auto-closes when the `with` exits.

---

## 6. Cross-loop trace continuity — extraction only

This is the trickiest piece in the codebase. Skip the theory if time is short, but everyone needs to know **the symptom** so they don't reintroduce the bug.

### Why this exists

The extraction service has two threads:
- **Pub/Sub callback thread** — pulled by Google's library, runs `callback(message)`.
- **Main asyncio loop** — where `process_documents(...)` actually runs because it does async HTTPX calls.

We hand work from one to the other via `asyncio.run_coroutine_threadsafe(coro, _main_loop)`. **That call does not carry OTel context across threads.** Without a fix, every LLM call would create a brand-new root trace.

### The fix — `app/extract_pubsub.py:101, 214-223`

```python
# Inside the consumer span "with" block:
consumer_ctx = otel_context.get_current()   # line 101 — snapshot AFTER span is current
...
async def _run_with_context():
    token = otel_context.attach(consumer_ctx)   # line 219 — re-attach on target loop
    try:
        return await process_documents(...)
    finally:
        otel_context.detach(token)              # line 223 — clean up

future = asyncio.run_coroutine_threadsafe(_run_with_context(), _main_loop)
```

**Three things must be in this exact order:**
1. **Snapshot AFTER `with start_as_current_span(...)`** — if you snapshot before, you snapshot a context with no consumer span in it and grandchildren orphan.
2. **Attach inside the coroutine** on the target loop, not on the source thread.
3. **Detach in `finally`** — leaking the token pollutes the loop's "current context" for the next coroutine.

**Symptom if this breaks:** trace shows `pubsub process >> EXTRACT_SUB` correctly, but every HTTPX call (LLM) shows up as its own root trace with no parent. In Dynatrace Service Flow you'll see the Java→Python arrow but no Python→LLM arrow.

---

## 7. Log ↔ trace correlation

For an operator to be able to click a trace in Dynatrace and see **the actual log lines that ran inside that trace**, every log record needs a `trace_id` attribute. Two things have to line up:

### 7.1 `LoggingInstrumentor` injects placeholders

`otel_config.py` calls `LoggingInstrumentor().instrument(set_logging_format=True)`. This monkey-patches Python's `logging` module so every `LogRecord` gets four extra attributes: `otelTraceID`, `otelSpanID`, `otelServiceName`, `otelTraceSampled`.

### 7.2 Our formatter has to *ask* for them

**Classification — `app/logging_config.py:16-19`:**
```python
formatter = jsonlogger.JsonFormatter(
    '%(asctime)s %(levelname)s %(name)s %(message)s '
    '%(otelTraceID)s %(otelSpanID)s %(otelServiceName)s',
    rename_fields={'asctime':'timestamp'}
)
```

**Extraction — `app/core/logging_config.py:43, 48`:** same idea, in both `console` and `json` formatters, with `otelTraceSampled` added.

**If the formatter doesn't reference these placeholders, they don't appear in the log output, and Dynatrace's "Logs in context" view goes empty.** This is gotcha #2 — see section 9.

---

## 8. Istio + K8s glue (no Python here)

Three YAMLs outside the Python codebase that you need to know exist. Tell trainees to skim them once, not memorise.

### 8.1 `ef-pf-ai-document-extraction-service/ConfigMap.yaml`

Defines the OTel Collector itself (`namespace: dynatrace`, name `perf-otel-collector-config`). Three pipelines: `traces`, `metrics`, `logs`. Exporter `otlphttp/dynatrace` reads `DT_ENDPOINT` + `DT_API_TOKEN` from env and adds the `Authorization` header.

This is **the file you change** if you ever need to add a new exporter destination (Tempo for dev, say), tweak batching, or scrape new Prometheus targets.

### 8.2 `ef-pf-ai-document-classification-service/otel-service-entry.yaml`

Istio `ServiceEntry` in namespace `trd-perf1`. Tells the mesh that the host `uat-otel-collector-svc.dynatrace.svc.cluster.local` is reachable. Without this, in-mesh traffic to the collector is refused.

### 8.3 `ef-pf-ai-document-classification-service/destination-rule-otel.yaml`

Istio `DestinationRule` in namespace `trd-perf1`. **Disables mTLS** for traffic to the collector hostname (the collector terminates plain HTTP on 4318; mTLS would break it).

> If you ever deploy this stack to a fresh namespace, you must copy **both** the ServiceEntry and the DestinationRule into that namespace. The extraction service doesn't have its own copies — it relies on the same ones from the classification namespace because they're co-deployed.

---

## 9. The 4 gotchas we actually hit (and you might too)

Walk through these slowly — they are the most likely things a new owner will get bitten by.

### Gotcha 1 — `lifespan` ordering in extraction

`ef-pf-ai-document-extraction-service/app/main.py:31-40`:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Order matters: setup_logging() must run BEFORE configure_opentelemetry()
    setup_logging()
    configure_opentelemetry(app)
```

If you flip these two lines, `LoggingInstrumentor` installs its format hook first; then `dictConfig` inside `setup_logging` wipes it out. Symptom: extraction logs have **no `trace_id` field**, classification (which doesn't use `dictConfig`) still has them. Surprisingly hard to debug without knowing.

The inline comment at lines `34-38` is the canonical reference — leave it there.

### Gotcha 2 — placeholders missing from formatter

If the formatter string in `logging_config.py` doesn't include `%(otelTraceID)s` etc., `LoggingInstrumentor` has nothing to fill in. Symptom: log records emitted but no trace correlation in Dynatrace "Logs in context" view. Fix: add the placeholders to **every** formatter you define.

### Gotcha 3 — SpanKind for the Pub/Sub consumer

We use `SpanKind.INTERNAL` + `messaging.operation=process`, **not** `SpanKind.CONSUMER` + `messaging.operation=receive`.

Why: the Java orchestrator that consumes our *output* topic also uses `CONSUMER` shape. If our extraction consumer used the same shape, Dynatrace would render two `consume <<` arrows pointing into the same box and the service-flow diagram would look ambiguous.

**Parent stitching does NOT depend on `SpanKind`** — only on `traceparent`. So switching to `INTERNAL`/`process` is purely cosmetic and doesn't break the trace chain.

The canonical comment is at `extract_pubsub.py:84-87` — read it out loud during KT.

### Gotcha 4 — `autodynatrace` was removed deliberately

Read this comment to trainees: `extraction-service/app/main.py:22-25`:

```python
# autodynatrace removed: OneAgent SDK fails to initialise in this pod
# (AgentState: 2 — see startup logs) and the import can shadow our manual
# OTel spans. The OTLP exporter wired up in app.otel_config is the source
# of truth for traces -> Dynatrace.
```

If anyone "helpfully" re-adds the import, manual spans will silently disappear. There is no good error message for this — it just looks like our instrumentation stopped working. Defend against re-introduction in code review.

---

## 10. Live demo — what to show in Dynatrace

Pull up a real trace and show:

1. **PurePath / Distributed Trace view**
   - Find a recent trade-case trace (filter by `service.name=document-classification-service`).
   - Show the tree: Java publisher → `pubsub process >> CLASSIFY_SUB` → `HTTPX POST openai…` → `pubsub publish >> output-topic` → Java consume → extraction.
   - Click into a span and show: name, `service.name`, `messaging.*` attributes, duration, exceptions if any.

2. **Service Flow**
   - Show the aggregate "what calls what" diagram. Point at the queue icons — those come from the `messaging.system=gcp_pubsub` attribute on producer/consumer spans.

3. **Logs in context**
   - Open a trace, click the "Logs" tab.
   - Show that the log lines from inside that trace are filtered automatically by `otelTraceID`.
   - Sanity-check that `trace_id`, `span_id`, `service.name` are all populated on the records.

4. **Search by SRN**
   - Take a trade-case SRN (Service Request Number) and paste it in as a trace search.
   - It works because `app/middleware/tracing.py:10-24` formats the SRN into a W3C-compliant 32-char hex trace_id for the synchronous `/extract/v1/process` endpoint.

5. **Metrics**
   - Show the `docs_processed_total`, `documents_classified_total`, `classification_duration_seconds`, `job_processing_duration_seconds` Prometheus metrics in Dynatrace's metric explorer. They flow through the same collector pipeline.

---

## 11. Trace-continuity machinery — one-page cheat sheet

If something breaks, these are the load-bearing pieces. Each row says "if you touch this and get it wrong, **this** is what breaks."

| What | Where | What breaks if you get it wrong |
|---|---|---|
| `propagate.extract(message.attributes)` | `classify_pubsub.py:53`, `extract_pubsub.py:82` | Consumer span starts a new root trace. Java→Python arrow disappears. |
| `context=ctx` arg to `start_as_current_span` | `classify_pubsub.py:58`, `extract_pubsub.py:90` | Same as above. |
| `consumer_ctx = otel_context.get_current()` (after `with`) | `extract_pubsub.py:101` | Snapshot taken too early → cross-loop work orphans. |
| `otel_context.attach(consumer_ctx)` inside coroutine | `extract_pubsub.py:219` | LLM/HTTP calls become roots, not children. |
| `otel_context.detach(token)` in finally | `extract_pubsub.py:223` | Context leaks; next coroutine inherits a stale parent. |
| `propagate.inject(publish_attributes)` | `classify_pubsub.py:109, 139, 169`; `extract_pubsub.py:187, 262, 295, 325` | Outbound `traceparent` missing → next service starts a new trace. |
| `lifespan` order: `setup_logging` → `configure_opentelemetry` | `extract main.py:39-40` | Logs have no `trace_id`. |
| `%(otelTraceID)s` etc. in formatter | `classify logging_config.py:17`; `extract core/logging_config.py:43,48` | Same as above. |
| `SpanKind.INTERNAL` for consumer | `classify_pubsub.py:59`, `extract_pubsub.py:91` | Service flow diagram becomes visually ambiguous (does not break trace). |
| No `autodynatrace` import | `extract main.py:22-25` | Manual spans silently disappear. |

---

## 12. Handoff checklist — trainee should be able to

After this KT, the new owner should be able to do all of these without asking. Use this as the "did the KT work?" test.

- [ ] Open Dynatrace, find today's trace for a given SRN, and walk through every span in the tree.
- [ ] Read `otel_config.py` and explain what each of the six blocks does.
- [ ] Point at the four lines in `extract_pubsub.py` that implement cross-loop trace continuity.
- [ ] Add a new PRODUCER span to a fresh `publisher.publish(...)` call without copy-pasting.
- [ ] Diagnose "every Pub/Sub message is its own trace" using the table in section 11.
- [ ] Diagnose "logs have no trace_id" using gotchas 1+2.
- [ ] Explain why we use `SpanKind.INTERNAL` instead of `CONSUMER`.
- [ ] Explain why `autodynatrace` is not in `requirements.txt`.
- [ ] Name the three Kubernetes/Istio YAMLs and what each one does.

---

## 13. Anticipated questions

**Q: Why are we running our own OTel Collector instead of pointing the app straight at the Dynatrace OTLP endpoint?**
A: (1) The Dynatrace API token stays inside one pod (the collector), not in every app pod. (2) The collector also scrapes Prometheus federate targets (YugaByte DB metrics) — see `ConfigMap.yaml:16-37` — and merges them into the same export pipeline. (3) If we ever need to fan out to a second backend (Tempo for local dev, say), we change collector config, not 200 app pods.

**Q: Why not just use Dynatrace's OneAgent / `autodynatrace`?**
A: It failed to initialise in our pods (`AgentState: 2` in startup logs) and the import shadowed our manual OTel spans. OTel is vendor-neutral by design — moving to a new APM is a single env-var change.

**Q: How does the trace ID get carried over Pub/Sub?**
A: As an attribute on the Pub/Sub message itself. The publisher calls `propagate.inject(attributes_dict)` which writes a W3C `traceparent` value into the dict. The consumer calls `propagate.extract(message.attributes)` which reads it back. Dynatrace stitches by matching `trace_id` and `parent_span_id` across spans regardless of language/service.

**Q: What is `traceparent` actually?**
A: A W3C-standard string: `00-<32 hex trace_id>-<16 hex span_id>-01`. Defined at https://www.w3.org/TR/trace-context/.

**Q: Why does the extraction service have a `W3CTracingMiddleware` but classification doesn't?**
A: Extraction has a synchronous HTTP endpoint (`/extract/v1/process`) where ops need to find a trace by SRN. The middleware sets the trace_id to a hex-sanitised version of the SRN. Classification's HTTP endpoint is dev-only — it's the Pub/Sub path that matters in prod.

**Q: What's the difference between `SpanKind.INTERNAL`, `CLIENT`, `SERVER`, `PRODUCER`, `CONSUMER`?**
A: It's how Dynatrace draws the icon and decides the arrow direction in service-flow. `INTERNAL`=in-process work, `CLIENT`=outbound call, `SERVER`=inbound call, `PRODUCER`=publish to queue, `CONSUMER`=receive from queue. It does **not** affect trace stitching — that's purely `traceparent`.

**Q: Do we need to add the OTel packages to `requirements.txt`?**
A: Already there in both services. See `# Dynatrace / OpenTelemetry Core & Exporters` block — `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp`, plus the three instrumentations: `fastapi`, `httpx`, `logging`.

**Q: What env vars need to be set in the deployment?**
A: At minimum: `OTEL_EXPORTER_OTLP_ENDPOINT` (the in-cluster collector URL), `OTEL_SERVICE_NAME` (the box name in Dynatrace). The collector pod additionally needs `DT_ENDPOINT` and `DT_API_TOKEN`.

---

## 14. Further reading

- `OTEL_DYNATRACE.md` (in this repo) — full theory, diagnosis playbook, and complete failure-mode table.
- W3C Trace Context: https://www.w3.org/TR/trace-context/
- OTel Python docs: https://opentelemetry.io/docs/languages/python/
- OTel Messaging semantic conventions: https://opentelemetry.io/docs/specs/semconv/messaging/
- Dynatrace OTLP ingest: https://docs.dynatrace.com/docs/extend-dynatrace/opentelemetry/getting-started/otlp-export
