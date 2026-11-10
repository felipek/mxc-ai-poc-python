"""Run with python3 -m mxc_poc; no installation or pip dependencies required."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sys
import tempfile

from .client import Executor, Request
from .lab import SCENARIOS, run_lab
from .setup import ROOT, SOURCE, bootstrap


def main():
    parser = argparse.ArgumentParser(description="AI-generated, unofficial Python-only MXC proof of concept")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap", help="Build the pinned native executor (Rust and Git required)")
    commands.add_parser("doctor", help="Inspect the runtime and execute a sandbox smoke check")
    commands.add_parser("list", help="List available scenarios")
    commands.add_parser("demo", help="Run all real sandbox scenarios")
    selected = commands.add_parser("run", help="Run selected scenarios")
    selected.add_argument("scenarios", nargs="+", choices=SCENARIOS)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            bootstrap()
            return 0
        if args.command == "list":
            for name, description in SCENARIOS.items():
                print("{:<12} {}".format(name, description))
            return 0
        executor = Executor()
        if args.command == "doctor":
            with tempfile.TemporaryDirectory(prefix="mxc-doctor-") as temporary:
                result = executor.run(Request(["/bin/echo", "MXC ready"], Path(temporary), readonly=[str(Path(temporary).resolve())]))
            ready = result.exit_code == 0 and not result.errors and not result.watchdog_expired and result.stdout.strip() == "MXC ready"
            print(json.dumps({"python": sys.version.split()[0], "platform": sys.platform, "architecture": platform.machine(),
                              "executor": executor.metadata, "smoke_test_passed": ready, "smoke_test": result.to_dict()}, indent=2))
            return 0 if ready else 1

        print("MXC Python policy lab · AI-generated POC · Python {} · {}".format(platform.python_version(), platform.machine()), flush=True)

        def show(result):
            print("{}  {:<12} {} ({}ms)".format("PASS" if result["passed"] else "FAIL", result["id"], result["description"], result["elapsed_ms"]), flush=True)
            if "error" in result:
                print("      {}: {}".format(result["error"]["type"], result["error"]["message"]), flush=True)
            for run in result["runs"]:
                for warning in run["warnings"]:
                    print("      MXC warning: {}".format(warning))

        results = run_lab(executor, list(SCENARIOS) if args.command == "demo" else args.scenarios, show)
        created = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        report = {"schema_version": 1, "created_at": created, "ai_generated": True,
                  "host": {"python": platform.python_version(), "os": platform.platform(), "architecture": platform.machine()},
                  "executor": executor.metadata, "contract_version": SOURCE["contract_version"],
                  "passed": all(result["passed"] for result in results), "results": results}
        reports = ROOT / "reports"
        reports.mkdir(exist_ok=True)
        report_path = reports / (created.replace(":", "-") + ".json")
        text = json.dumps(report, indent=2) + "\n"
        report_path.write_text(text)
        (reports / "latest.json").write_text(text)
        print("\n{}/{} checks passed\nReport: {}".format(sum(result["passed"] for result in results), len(results), report_path))
        return 0 if report["passed"] else 1
    except (OSError, RuntimeError, ValueError) as error:
        print("Error: {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
