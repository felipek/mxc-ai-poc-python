"""End-to-end containment checks using synthetic fixtures and real Python jobs."""

import errno
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

from .client import Request
from .runtime import python_runtime_paths

SCENARIOS = {
    "hello": "Run Python inside MXC",
    "readonly": "Read input, block mutation, verify the host file is unchanged",
    "transform": "Transform orders and write only to the granted output directory",
    "denied": "Read a canary, then enforce a deny over its read grant",
    "network": "Allow local TCP, then deny the same reachable endpoint",
    "stream": "Send stdin and receive live stdout plus independent stderr",
    "timeout": "Require MXC to terminate a ten-second job after one second",
}
PROBE = Path(__file__).resolve().parent.parent / "workloads" / "probe.py"


def require(condition, message):
    # Keep validation active even when Python runs with -O.
    if not condition:
        raise AssertionError(message)


def run_lab(executor, selected, on_result=print):
    runtime = python_runtime_paths()
    results = []
    with tempfile.TemporaryDirectory(prefix="mxc-python-poc-") as temporary:
        root = Path(temporary).resolve()
        inputs, outputs, private = (root / name for name in ("read only 'inputs'", "write outputs", "private"))
        for directory in (inputs, outputs, private):
            directory.mkdir()
        orders = [{"quantity": 2, "unit_cents": 1250}, {"quantity": 1, "unit_cents": 3999}]
        original = json.dumps(orders) + "\n"
        input_file = inputs / "orders.json"
        output_file = outputs / "summary.json"
        canary = private / "canary.txt"
        input_file.write_text(original)
        canary.write_text("synthetic-canary\n")

        for name in selected:
            started = time.monotonic()
            runs = []

            def request(mode, *args, **options):
                return Request(
                    argv=[str(Path(sys.executable).resolve()), "-I", "-S", "-B", "-u", "-X", "utf8", str(PROBE), mode] + [str(arg) for arg in args],
                    cwd=inputs, readonly=runtime + [str(PROBE.parent), str(inputs)], **options,
                )

            def execute(req, stdin=""):
                result = executor.run(req, stdin)
                runs.append(result.to_dict())
                return result

            error = None
            try:
                if name == "hello":
                    require(execute(request("hello")).json_output()["message"] == "Hello from Python inside MXC", "Missing sandbox greeting")
                elif name == "readonly":
                    result = execute(request("readonly", input_file)).json_output()
                    require(result["blocked"] and result["input"] == original, "Read-only policy failed")
                    require(input_file.read_text() == original, "Host input was modified")
                elif name == "transform":
                    expected = {"orders": 2, "total_cents": 6499}
                    req = request("transform", input_file, output_file, readwrite=[str(outputs)])
                    require(execute(req).json_output() == expected, "Incorrect transformation output")
                    require(json.loads(output_file.read_text()) == expected, "Output artifact differs")
                    require(input_file.read_text() == original, "Transformation modified its input")
                elif name == "denied":
                    control = request("read", canary)
                    control.readonly.append(str(private))
                    require(execute(control).json_output()["text"] == "synthetic-canary\n", "Read control failed")
                    blocked = request("denied", canary, denied=[str(private)])
                    blocked.readonly.append(str(private))
                    require(execute(blocked).json_output()["blocked"], "Explicit deny did not override read grant")
                elif name == "network":
                    with socket.socket() as server:
                        server.bind(("127.0.0.1", 0))
                        server.listen()
                        server.settimeout(6)
                        server_errors = []

                        def serve():
                            try:
                                with server.accept()[0] as connection:
                                    connection.sendall(b"mxc-python-local-control")
                            except OSError as cause:
                                server_errors.append(str(cause))

                        thread = threading.Thread(target=serve, daemon=True)
                        thread.start()
                        port = server.getsockname()[1]
                        try:
                            allowed = execute(request("network", port, network_allowed=True)).json_output()
                            require(allowed == {"connected": True, "response": "mxc-python-local-control"}, "Network allow control failed")
                            blocked = execute(request("network", port)).json_output()
                            require(not blocked["connected"] and blocked["errno"] in (errno.EPERM, errno.EACCES), "Expected a permission denial, not a refusal or timeout: {}".format(blocked))
                        finally:
                            thread.join(timeout=7)
                        require(not thread.is_alive() and not server_errors, "Local control service failed: {}".format(server_errors))
                elif name == "stream":
                    result = execute(request("stream"), stdin="hello over Python stdin\n")
                    require(result.exit_code == 0 and not result.errors and not result.watchdog_expired, result.stderr)
                    lines = [json.loads(line) for line in result.stdout.splitlines()]
                    require(lines == [{"stage": "received", "input": "hello over Python stdin"}, {"stage": "finished"}], "Incorrect stream exchange")
                    require("Python stream diagnostic" in result.stderr, "stderr was not captured")
                    events = [event for event in result.events if event["channel"] == "stdout"]
                    require(len(events) >= 2 and events[-1]["at_ms"] - events[0]["at_ms"] >= 300, "Output did not arrive live")
                elif name == "timeout":
                    result = execute(request("timeout", timeout_ms=1000))
                    require(not result.watchdog_expired, "The host watchdog fired; MXC did not enforce its deadline")
                    require(result.exit_code != 0, "Timed job unexpectedly succeeded")
                    require(any(error.get("code") == "backend_error" and error.get("message") == "script timed out after 1000ms: Seatbelt: process timed out" for error in result.errors), "Missing pinned MXC timeout diagnostic: {}".format(result.stderr))
                    require("workload started" in result.stdout and "unexpected completion" not in result.stdout, "Workload did not start or was not terminated")
                    require(800 <= result.elapsed_ms < 5000, "Native deadline did not fire promptly")
                else:
                    raise ValueError("Unknown scenario: {}".format(name))
            except Exception as cause:
                error = {"type": type(cause).__name__, "message": str(cause)}
            result = {"id": name, "description": SCENARIOS[name], "passed": error is None,
                      "elapsed_ms": round((time.monotonic() - started) * 1000), "runs": runs}
            if error:
                result["error"] = error
            results.append(result)
            on_result(result)
    return results
