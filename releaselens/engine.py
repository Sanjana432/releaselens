"""Paired HTTP comparison and an explainable release gate."""
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPException
import json
import math
import re
from statistics import median
from time import perf_counter

from opentelemetry.context import Context
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


@dataclass(frozen=True)
class Case:
    name: str
    path: str
    ignore_top_level: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Gate:
    repeats: int = 8
    relative_margin: float = .30
    absolute_margin_ms: float = 18.0

    def validate(self):
        if type(self.repeats) is not int or not 3 <= self.repeats <= 24:
            raise ValueError("Use 3 to 24 paired requests per case.")
        if not 0 <= self.relative_margin <= 5 or not 1 <= self.absolute_margin_ms <= 1000:
            raise ValueError("Invalid regression margins.")


def parse_cases(raw):
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
        raise ValueError("Provide 1 to 4 test cases.")
    cases = []
    for row in raw:
        if not isinstance(row, dict):
            raise ValueError("Each test case must be an object.")
        name, path, ignore = row.get("name"), row.get("path"), row.get("ignore_top_level", [])
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise ValueError("Specify a case name of up to 80 characters.")
        if not isinstance(path, str) or not re.fullmatch(r"/[A-Za-z0-9_./-]{0,160}", path) or ".." in path or "//" in path:
            raise ValueError("Use simple origin-relative GET paths.")
        if not isinstance(ignore, list) or len(ignore) > 12 or any(not isinstance(key, str) for key in ignore):
            raise ValueError("Provide up to 12 top-level field names to ignore.")
        cases.append(Case(name, path, frozenset(ignore)))
    if len({case.name for case in cases}) != len(cases):
        raise ValueError("Case names must be unique.")
    return cases


def first_diff(before, after, path="$"):
    if len(path) > 512 and before != after:
        return f"{path[:512]}…: nested value changed"
    if type(before) is not type(after):
        return f"{path}: JSON type changed"
    if isinstance(before, dict):
        for key in sorted(before.keys() | after.keys()):
            next_path = f"{path}[{json.dumps(key)}]"
            if key not in before:
                return f"{next_path}: field added"
            if key not in after:
                return f"{next_path}: field removed"
            diff = first_diff(before[key], after[key], next_path)
            if diff:
                return diff
    elif isinstance(before, list):
        if len(before) != len(after):
            return f"{path}: array length changed"
        for index, (a, b) in enumerate(zip(before, after)):
            diff = first_diff(a, b, f"{path}[{index}]")
            if diff:
                return diff
    elif before != after:
        return f"{path}: value changed"
    return None


def payload_difference(a, b, case):
    if a["error"] or b["error"]:
        return f"Request failed ({a['error'] or b['error']})."
    if a["status"] != b["status"]:
        return f"HTTP status changed from {a['status']} to {b['status']}."
    if not 200 <= a["status"] < 300:
        return f"Expected successful HTTP response, received {a['status']}."
    def clean(document):
        if isinstance(document, dict):
            return {key: value for key, value in document.items() if key not in case.ignore_top_level}
        return document
    return first_diff(clean(a["body"]), clean(b["body"]))


def request(port, case, tracer, variant):
    connection = HTTPConnection("127.0.0.1", port, timeout=.75)
    with tracer.start_as_current_span("GET " + case.path, context=Context(), kind=SpanKind.CLIENT,
                                     attributes={"variant": variant, "network.peer.port": port}) as span:
        carrier = {"Accept": "application/json"}
        TraceContextTextMapPropagator().inject(carrier)
        trace_id = f"{span.get_span_context().trace_id:032x}"
        started = perf_counter()
        code, body, error = None, None, None
        try:
            connection.request("GET", case.path, headers=carrier)
            response = connection.getresponse()
            code = response.status
            raw = response.read(65_537)
            if len(raw) > 65_536:
                error = "response too large"
            elif 200 <= code < 300:
                body = json.loads(raw, parse_constant=_invalid_constant)
        except (OSError, ValueError, RecursionError, HTTPException) as exc:
            error = type(exc).__name__
        finally:
            connection.close()
        elapsed = (perf_counter() - started) * 1000
        if code is not None:
            span.set_attribute("http.response.status_code", code)
        if error:
            span.set_attribute("error.type", error)
        return {"ms": elapsed, "status": code, "body": body, "error": error, "trace_id": trace_id}


def _invalid_constant(value):
    raise ValueError("Nonstandard JSON constant: " + value)


def summarize(values):
    ordered = sorted(values)
    return {"median": round(median(ordered), 3),
            "p95": round(ordered[math.ceil(.95 * len(ordered)) - 1], 3)}


def classify(case, pairs, gate):
    a = summarize([old["ms"] for old, _ in pairs])
    b = summarize([new["ms"] for _, new in pairs])
    delta = summarize([new["ms"] - old["ms"] for old, new in pairs])
    contract = [problem for old, new in pairs if (problem := payload_difference(old, new, case))]
    budget = max(gate.absolute_margin_ms, a["median"] * gate.relative_margin)
    slower = delta["median"] > budget
    worst = max(pairs, key=lambda pair: pair[1]["ms"] - pair[0]["ms"])
    return {"name": case.name, "path": case.path, "baseline_ms": a, "candidate_ms": b,
            "paired_delta_ms": delta, "margin_ms": round(budget, 3), "slow": slower,
            "contract_failures": len(contract), "differences": sorted(set(contract))[:6],
            "trace_ids": [row["trace_id"] for row in worst], "samples": len(pairs),
            "passed": not slower and not contract}


def run(baseline_port, candidate_port, cases, gate, tracer, warmup=True):
    gate.validate()
    if any(type(p) is not int or not 1 <= p <= 65535 for p in (baseline_port, candidate_port)) or baseline_port == candidate_port:
        raise ValueError("Provide two distinct localhost ports.")
    rows = []
    for case in cases:
        if warmup:
            request(baseline_port, case, tracer, "warmup-baseline")
            request(candidate_port, case, tracer, "warmup-candidate")
        pairs = []
        for i in range(gate.repeats):
            order = (("baseline", baseline_port), ("candidate", candidate_port))
            if i % 2:
                order = order[::-1]
            pair = {role: request(port, case, tracer, role) for role, port in order}
            pairs.append((pair["baseline"], pair["candidate"]))
        rows.append(classify(case, pairs, gate))
    return {"passed": all(row["passed"] for row in rows), "cases": rows,
            "method": "Sequential paired GETs. Run order alternates. Warmups are excluded."}
