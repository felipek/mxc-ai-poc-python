"""Small Python adapter for MXC's JSON executor interface, not an official SDK."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import shlex
import signal
import time

from .setup import EXECUTABLE, LOCAL, SOURCE, require_macos, sha256


@dataclass
class Request:
    argv: list[str]
    cwd: Path
    readonly: list[str] = field(default_factory=list)
    readwrite: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
    network_allowed: bool = False
    timeout_ms: int = 5000

    def to_config(self):
        if not self.argv or not self.argv[0] or not all(isinstance(arg, str) and "\0" not in arg for arg in self.argv):
            raise ValueError("argv must contain non-NUL strings and name an executable")
        if not 1 <= self.timeout_ms <= 4_294_967_295:
            raise ValueError("timeout_ms must be positive and fit MXC's 32-bit timeout")
        action = "allow" if self.network_allowed else "deny"
        return {
            "version": SOURCE["contract_version"],
            "containment": "seatbelt",
            "process": {
                # Seatbelt runs a command through /bin/sh. Quote each argument.
                "commandLine": "exec " + shlex.join(self.argv),
                "cwd": str(self.cwd.resolve()),
                "env": ["PATH=/usr/bin:/bin", "LANG=C"],
                "timeout": self.timeout_ms,
            },
            "filesystem": {"readonlyPaths": self.readonly, "readwritePaths": self.readwrite, "deniedPaths": self.denied},
            "network": {"egress": {"default": action}, "ingress": {"default": action, "hostLoopback": action}},
            "telemetry": {"enabled": False},
        }


@dataclass
class Result:
    config: dict
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int
    events: list[dict]
    errors: list[dict]
    warnings: list[dict]
    watchdog_expired: bool

    def to_dict(self):
        return asdict(self)

    def json_output(self):
        if self.exit_code != 0 or self.errors or self.watchdog_expired:
            raise RuntimeError("MXC execution failed (exit {}): {}".format(self.exit_code, self.stderr))
        return json.loads(self.stdout)


class Executor:
    def __init__(self, executable=None):
        require_macos()
        selected = executable or os.environ.get("MXC_EXECUTABLE")
        self.executable = Path(selected).expanduser().resolve() if selected else EXECUTABLE
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise RuntimeError("MXC executor missing or not executable. Run: python3 -m mxc_poc bootstrap")
        self.metadata = {"path": str(self.executable), "sha256": sha256(self.executable), "source": "external"}
        if not selected:
            installed = json.loads((LOCAL / "runtime.json").read_text())
            if installed["sha256"] != self.metadata["sha256"] or installed["source"] != SOURCE:
                raise RuntimeError("Native runtime differs from its pinned build metadata. Run bootstrap again.")
            self.metadata["source"] = installed["source"]
            self.metadata["rustc"] = installed["rustc"]

    def run(self, request, stdin=""):
        return asyncio.run(self._run(request, stdin))

    async def _run(self, request, stdin):
        config = request.to_config()
        encoded = base64.b64encode(json.dumps(config).encode()).decode("ascii")
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            str(self.executable), "--config-base64", encoded,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True,
        )
        events = []

        async def collect(reader, channel):
            data = bytearray()
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                data.extend(chunk)
                events.append({"channel": channel, "at_ms": round((time.monotonic() - started) * 1000), "text": chunk.decode("utf-8", errors="replace")})
            return data.decode("utf-8", errors="replace")

        async def feed():
            try:
                process.stdin.write(stdin.encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                process.stdin.close()

        tasks = [asyncio.create_task(collect(process.stdout, "stdout")),
                 asyncio.create_task(collect(process.stderr, "stderr")),
                 asyncio.create_task(feed()), asyncio.create_task(process.wait())]
        watchdog_expired = False
        try:
            # MXC owns the workload deadline. This longer watchdog is a host failure.
            await asyncio.wait_for(asyncio.shield(asyncio.gather(*tasks)), request.timeout_ms / 1000 + 8)
        except asyncio.TimeoutError:
            watchdog_expired = True
            self._kill_group(process.pid)
            await asyncio.wait_for(asyncio.gather(*tasks), 3)
        except BaseException:
            self._kill_group(process.pid)
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        stdout, stderr = tasks[0].result(), tasks[1].result()
        errors, warnings = [], []
        for line in stderr.splitlines():
            try:
                envelope = json.loads(line)
            except ValueError:
                continue
            if isinstance(envelope, dict):
                if isinstance(envelope.get("error"), dict):
                    errors.append(envelope["error"])
                if isinstance(envelope.get("warning"), dict):
                    warnings.append(envelope["warning"])
        return Result(config, process.returncode, stdout, stderr,
                      round((time.monotonic() - started) * 1000), events, errors, warnings, watchdog_expired)

    @staticmethod
    def _kill_group(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
