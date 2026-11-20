import argparse
import json
from pathlib import Path

from .demo import SCENARIOS, experiment, telemetry, trace_records
from .engine import Gate, parse_cases, run


def main():
    parser = argparse.ArgumentParser(description="Compare two local HTTP services.")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int, default=8132)
    demo = sub.add_parser("demo")
    demo.add_argument("scenario", choices=SCENARIOS, nargs="?", default="mixed")
    check = sub.add_parser("check")
    check.add_argument("baseline_port", type=int)
    check.add_argument("candidate_port", type=int)
    check.add_argument("cases", type=Path)
    for command in (demo, check):
        command.add_argument("--repeats", type=int, default=8)
        command.add_argument("--margin-ms", type=float, default=18.0)
        command.add_argument("--relative-margin", type=float, default=.30)
    args = parser.parse_args()
    if args.command == "serve":
        import uvicorn
        uvicorn.run("releaselens.web:app", host="127.0.0.1", port=args.port)
        return 0
    gate = Gate(args.repeats, args.relative_margin, args.margin_ms)
    try:
        if args.command == "demo":
            result = experiment(args.scenario, gate)
        else:
            cases = parse_cases(json.loads(args.cases.read_text(encoding="utf-8")))
            with telemetry() as (tracer, exporter):
                result = run(args.baseline_port, args.candidate_port, cases, gate, tracer)
                result["traces"] = trace_records(result, exporter)
    except (ValueError, OSError, RecursionError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
