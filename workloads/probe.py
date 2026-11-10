"""Trusted, fixed Python workloads executed inside the MXC sandbox."""

import errno
import json
from pathlib import Path
import socket
import sys
import time


def emit(value):
    print(json.dumps(value), flush=True)


def require_denial(operation):
    try:
        operation()
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EACCES, errno.EROFS):
            raise
        return {"blocked": True, "errno": error.errno}
    raise RuntimeError("Policy failure: forbidden operation succeeded")


mode, *args = sys.argv[1:]
if mode == "hello":
    emit({"message": "Hello from Python inside MXC", "python": sys.version.split()[0]})
elif mode == "readonly":
    file = Path(args[0])
    text = file.read_text()
    result = require_denial(lambda: file.write_text("unexpected mutation"))
    emit(dict(result, input=text))
elif mode == "transform":
    orders = json.loads(Path(args[0]).read_text())
    result = {"orders": len(orders), "total_cents": sum(order["quantity"] * order["unit_cents"] for order in orders)}
    Path(args[1]).write_text(json.dumps(result) + "\n")
    emit(result)
elif mode == "read":
    emit({"text": Path(args[0]).read_text()})
elif mode == "denied":
    emit(require_denial(lambda: Path(args[0]).read_text()))
elif mode == "network":
    try:
        with socket.create_connection(("127.0.0.1", int(args[0])), timeout=1.5) as connection:
            emit({"connected": True, "response": connection.recv(256).decode()})
    except OSError as error:
        emit({"connected": False, "errno": error.errno})
elif mode == "stream":
    emit({"stage": "received", "input": sys.stdin.read().strip()})
    print("Python stream diagnostic", file=sys.stderr, flush=True)
    time.sleep(0.6)
    emit({"stage": "finished"})
elif mode == "timeout":
    print("workload started", flush=True)
    time.sleep(10)
    print("unexpected completion", flush=True)
else:
    raise ValueError("Unknown workload: {}".format(mode))
