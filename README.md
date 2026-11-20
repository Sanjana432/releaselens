# ReleaseLens

ReleaseLens compares baseline and candidate HTTP APIs. It detects response changes, measures paired latency, and shows where time went in an OpenTelemetry request waterfall. The demo sends real requests to two temporary localhost servers.

## Run it

Requires Python 3.11 or newer. Run these commands from this repository.

```console
python -m pip install -e ".[test]"
python -m releaselens serve
```

Open [the ReleaseLens dashboard](http://127.0.0.1:8132). The default demo blocks the candidate because its catalog total changes and a redundant lookup slows it down. Select **Health contract** to see a passing endpoint in the same run. Select **Healthy candidate** and rerun to show the full release passing. The report can be exported as JSON.

```console
python -m releaselens demo mixed
python -m releaselens demo healthy
python -m unittest discover -s tests -v
```

## Compare your own local services

Run the baseline and candidate on distinct ports on `127.0.0.1`. Define one to four read-only GET cases in a JSON file.

```json
[
  {"name": "Catalog totals", "path": "/catalog", "ignore_top_level": ["release"]},
  {"name": "Health contract", "path": "/health"}
]
```

```console
python -m releaselens check 9001 9002 cases.json --repeats 12 --margin-ms 18 --relative-margin 0.30
```

The CLI prints a JSON report and exits 1 when the release gate fails. The dashboard runs the bundled scenarios. The CLI records client spans for external services on localhost, while the bundled fixtures also expose correlated server spans in the report.

## How the gate works

- Warm up both releases, then take sequential pairs in alternating order. The report shows medians and nearest-rank p95 values. A latency regression occurs when the median paired increase exceeds the larger of the absolute margin or the relative margin times the baseline median.
- Compare HTTP status and JSON values and shapes, ignoring only named top-level fields. The report identifies the first changed path in each sample, without printing response values.
- Propagate W3C trace context into the fixtures, which emit server and internal OpenTelemetry spans. Inspect the baseline and candidate traces from the pair with the largest latency increase.

The small sample size suits repeatable local smoke checks. This experiment does not model production traffic or establish statistically significant performance changes.
