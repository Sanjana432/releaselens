"""Run actual HTTP fixtures and join client/server OpenTelemetry spans."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from time import sleep

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from .engine import Gate, parse_cases, run


CASES = [
    {"name": "Catalog totals", "path": "/catalog", "ignore_top_level": ["release"]},
    {"name": "Health contract", "path": "/health", "ignore_top_level": ["release"]},
]
SCENARIOS = ("healthy", "slow", "contract", "mixed")


@contextmanager
def telemetry():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        yield provider.get_tracer("releaselens"), exporter
    finally:
        provider.shutdown()


@contextmanager
def fixture(variant, scenario, tracer):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            carrier = {key.lower(): value for key, value in self.headers.items()}
            context = TraceContextTextMapPropagator().extract(carrier)
            with tracer.start_as_current_span(variant + " " + self.path, context=context, kind=SpanKind.SERVER):
                body, code = self.result()
                payload = json.dumps({"release": variant, **body}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        def result(self):
            if self.path == "/health":
                return {"ready": True}, 200
            if self.path != "/catalog":
                return {"error": "unknown route"}, 404
            with tracer.start_as_current_span("catalog.lookup"):
                sleep(.004)
            if variant == "candidate" and scenario in {"slow", "mixed"}:
                with tracer.start_as_current_span("catalog.redundant_lookup"):
                    sleep(.044)
            with tracer.start_as_current_span("catalog.price"):
                subtotal = 1800
                discount = 300 if variant == "candidate" and scenario in {"contract", "mixed"} else 200
                return {"items": 3, "subtotal_cents": subtotal, "total_cents": subtotal - discount}, 200

        def log_message(self, *_):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        server.daemon_threads = False
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        try:
            yield server.server_address[1]
        finally:
            server.shutdown()
            thread.join(timeout=2)


def trace_records(report, exporter):
    wanted = {trace_id for case in report["cases"] for trace_id in case["trace_ids"]}
    grouped = {trace_id: [] for trace_id in wanted}
    for span in exporter.get_finished_spans():
        trace_id = f"{span.context.trace_id:032x}"
        if trace_id in wanted:
            grouped[trace_id].append(span)
    return {trace_id: [
        {"name": span.name, "kind": span.kind.name, "span": f"{span.context.span_id:016x}",
         "parent": f"{span.parent.span_id:016x}" if span.parent else None,
         "start_ms": round((span.start_time - origin) / 1e6, 3),
         "duration_ms": round((span.end_time - span.start_time) / 1e6, 3)}
        for span in sorted(group, key=lambda span: span.start_time)]
        for trace_id, group in grouped.items() if group
        for origin in [min(span.start_time for span in group)]}


def experiment(scenario="mixed", gate=Gate()):
    if scenario not in SCENARIOS:
        raise ValueError("Choose healthy, slow, contract or mixed.")
    with telemetry() as (tracer, exporter):
        with fixture("baseline", scenario, tracer) as a, fixture("candidate", scenario, tracer) as b:
            report = run(a, b, parse_cases(CASES), gate, tracer)
        return {"scenario": scenario, **report, "traces": trace_records(report, exporter)}
