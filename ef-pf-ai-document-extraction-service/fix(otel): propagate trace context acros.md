fix(otel): propagate trace context across asyncio boundary in extraction pubsub

  The extraction service was breaking the Dynatrace PurePath after the Neev LLM
  call because asyncio.run_coroutine_threadsafe does not carry the OTel context
  to the target loop. Every LLM call ended up as a new root trace, and the
  publish-to-PubSub span — though emitted — was orphaned from the Java
  publisher → extraction consumer chain.

  Changes:

  - extract_pubsub.py:
    * Snapshot OTel context after the consumer span is current and re-attach it
      inside the coroutine handed to _main_loop, so LLM/HTTP spans become
      children of the consumer trace instead of starting new roots.
    * Switch consumer span to SpanKind.CONSUMER with messaging.source.name +
      messaging.operation=receive, matching the Java side's semantic
      convention and enabling Dynatrace PRODUCER→CONSUMER stitching.
    * Align all three PRODUCER spans (output, age-skip, DLQ) with the Java /
      classification convention: short topic name in span name and
      messaging.destination.name (was full projects/.../topics/... path).
    * Pre-declare job_id = None before starting the ack-extension thread to
      fix the UnboundLocalError closure bug that silently disabled ack
      deadline extension for long-running jobs.

  - main.py:
    * Move configure_opentelemetry(app) into lifespan, AFTER setup_logging(),
      so dictConfig no longer wipes the LoggingInstrumentor format. This
      restores otelTraceID / otelSpanID injection into log records — matching
      the classification service's working order.
    * Remove the autodynatrace import (OneAgent SDK was failing init with
      AgentState: 2 and could shadow our manual OTel instrumentation).

  - core/logging_config.py:
    * Add otelTraceID, otelSpanID, otelServiceName, otelTraceSampled
      placeholders to console and JSON formatters so log lines carry trace
      correlation, mirroring the classification service output.

  No business-logic or asyncio scheduling changes. process_documents,
  llm_client, the W3C SRN-based traceparent header, FlowControl, concurrency,
  ack/nack/DLQ routing, and the publish payload are all unchanged.






fix(otel): align extraction consumer span with classification convention

    Follow-up to the asyncio-context-propagation fix. The previous patch also
    switched the Pub/Sub consumer span to the strict OTel messaging semconv
    (kind=CONSUMER, messaging.source.name, operation=receive, "pubsub consume
    << <sub>"). In Dynatrace this collided visually with the architecture
    diagram's step 5 — the Java orchestration service that consumes the output
    topic uses the same "consume <<" shape — so the extraction consumer (step
    2) appeared in the wrong slot of the trace tree.

    Parent stitching from the Java PRODUCER actually happens via the
    traceparent injected into message.attributes (propagate.extract on line
    82), not via SpanKind or messaging.* attributes — confirmed by the
    classification trace, which stitches cleanly under the older convention.
    The strict semconv switch was therefore cosmetic, and the established
    cosmetic in this codebase is "pubsub process >> <sub>".

    Changes (app/extract_pubsub.py, consumer span only):

    - Span name:   "pubsub consume << {EXTRACT_SUB_ID}"
                -> "pubsub process >> {EXTRACT_SUB_ID}"
    - SpanKind:    CONSUMER -> INTERNAL
    - Attributes:  messaging.source.name      -> messaging.destination.name
                   messaging.operation=receive -> messaging.operation=process

    Now byte-for-byte aligned with classify_pubsub.py:57-66.

    Unchanged (and load-bearing for trace continuity across the thread/loop
    boundary):

    - propagate.extract(message.attributes) at the top of callback
    - consumer_ctx = otel_context.get_current() snapshot after span is current
    - otel_context.attach(consumer_ctx) inside _run_with_context, which keeps
      the Neev POST (step 3) and output publish (step 4) as children of the
      consumer trace
    - All three PRODUCER spans (output / age-skip / DLQ x2) keep the short
      topic name + messaging.destination.name shape
    - main.py lifespan ordering (setup_logging before configure_opentelemetry)
    - core/logging_config.py otelTraceID / otelSpanID formatter placeholders
    - job_id pre-declaration for the ack-extension thread closure

    Expected DT trace after deploy: pubsub publish >> extract-topic (Java)
    -> pubsub process >> EXTRACT_SUB (python) -> POST to Neev -> pubsub
    publish >> output-topic, all under a single PurePath.




fix(otel): rename extraction consumer span to match classification

    Follow-up to the asyncio-context-propagation fix. Revert the consumer span
    shape from the strict OTel CONSUMER/source.name/receive convention back to
    classification's existing INTERNAL/destination.name/process shape, so the
    trace reads "pubsub process >> EXTRACT_SUB" (step 2 in the architecture
    diagram) instead of "pubsub consume << EXTRACT_SUB" — which visually
    collided with step 5 (Java consuming the output topic).

    Parent stitching from the Java PRODUCER is driven by the traceparent in
    message.attributes (propagate.extract), not by SpanKind or messaging.*
    attributes, so this change is purely cosmetic.

    All trace-continuity machinery is untouched: consumer_ctx snapshot,
    otel_context.attach inside _run_with_context, PRODUCER span attributes,
    lifespan ordering, and log formatter placeholders all remain.
