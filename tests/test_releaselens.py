import unittest

from fastapi.testclient import TestClient

from releaselens.demo import experiment
from releaselens.engine import Case, Gate, classify, parse_cases
from releaselens.web import app


def sample(ms, body=None, status=200, error=None):
    return {"ms": ms, "body": body, "status": status, "error": error, "trace_id": "fake"}


class ReleaseLensTests(unittest.TestCase):
    def test_gate_uses_paired_median_and_larger_margin(self):
        case = Case("Catalog", "/catalog")
        a = [100, 140, 120, 100, 110]
        b = [114, 160, 900, 116, 130]
        row = classify(case, [(sample(old), sample(new)) for old, new in zip(a, b)], Gate())
        self.assertEqual(row["paired_delta_ms"]["median"], 20)
        self.assertEqual(row["margin_ms"], 33)
        self.assertEqual(row["candidate_ms"]["p95"], 900)
        self.assertTrue(row["passed"])

    def test_compares_nested_response_and_ignores_named_metadata(self):
        case = Case("Catalog", "/catalog", frozenset({"release"}))
        old = sample(5, {"release": "a", "result": {"sum": 3}})
        new = sample(5, {"release": "b", "result": {"sum": 4}})
        row = classify(case, [(old, new)], Gate())
        self.assertEqual(row["differences"], ['$["result"]["sum"]: value changed'])
        self.assertFalse(row["passed"])
        new["body"]["result"]["sum"] = 3
        self.assertTrue(classify(case, [(old, new)], Gate())["passed"])

    def test_http_failures_are_recorded(self):
        case = Case("Health", "/health")
        row = classify(case, [(sample(2), sample(2, status=503))], Gate())
        self.assertEqual(row["differences"], ["HTTP status changed from 200 to 503."])
        self.assertFalse(row["passed"])

    def test_case_parser_rejects_nonlocal_targets_and_duplicates(self):
        for path in ("https://example.com", "//example.com", "/../health"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                parse_cases([{"name": "Health", "path": path}])
        with self.assertRaises(ValueError):
            parse_cases([{"name": "Health", "path": "/health"}] * 2)

    def test_mixed_candidate_fails_and_links_actual_parent_spans(self):
        report = experiment("mixed", Gate(repeats=5))
        catalog, health = report["cases"]
        self.assertFalse(report["passed"])
        self.assertTrue(catalog["slow"])
        self.assertEqual(catalog["contract_failures"], 5)
        self.assertTrue(health["passed"])
        trace = report["traces"][catalog["trace_ids"][1]]
        client = next(span for span in trace if span["kind"] == "CLIENT")
        server = next(span for span in trace if span["kind"] == "SERVER")
        child = next(span for span in trace if span["name"] == "catalog.redundant_lookup")
        self.assertEqual(server["parent"], client["span"])
        self.assertEqual(child["parent"], server["span"])
        self.assertGreater(child["duration_ms"], 35)

    def test_web_healthy_demo_returns_a_passing_report(self):
        with TestClient(app) as client:
            self.assertIn("Ready to ship?", client.get("/").text)
            self.assertEqual(client.get("/static/app.js").status_code, 200)
            self.assertEqual(client.post("/api/experiment", json={"repeats": 25}).status_code, 422)
            response = client.post("/api/experiment", json={"scenario": "healthy", "repeats": 5})
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["passed"])
