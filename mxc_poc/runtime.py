"""Discover read-only paths needed by the selected Python interpreter."""

import importlib.util
from pathlib import Path
import re
import subprocess
import sys
import sysconfig


def python_runtime_paths():
    executable = Path(sys.executable).resolve()
    paths = {str(executable), str(Path(sysconfig.get_path("stdlib")).resolve())}
    shared = sysconfig.get_config_var("DESTSHARED")
    if shared and Path(shared).exists():
        paths.add(str(Path(shared).resolve()))
    pending = [executable]
    # C extensions imported by the fixed probe, including subprocess support.
    for module in ("_socket", "select", "math", "_posixsubprocess", "_json"):
        spec = importlib.util.find_spec(module)
        if spec and spec.origin and spec.origin.endswith((".so", ".dylib")):
            pending.append(Path(spec.origin).resolve())
    inspected = set()
    while pending:
        binary = pending.pop()
        if binary in inspected:
            continue
        inspected.add(binary)
        listing = subprocess.check_output(["/usr/bin/otool", "-L", str(binary)], text=True, timeout=10)
        commands = subprocess.check_output(["/usr/bin/otool", "-l", str(binary)], text=True, timeout=10)
        identities = re.findall(r"cmd LC_ID_DYLIB\s+cmdsize \d+\s+name (.*?) \(offset", commands)
        for line in listing.splitlines():
            match = re.match(r"\s+(.+?) \(compatibility version", line)
            if not match:
                continue
            name = match.group(1)
            # otool -L also lists a dylib's own install name, not a dependency.
            if name in identities:
                continue
            name = name.replace("@loader_path", str(binary.parent)).replace("@executable_path", str(executable.parent))
            if name.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if not Path(name).is_absolute():
                raise RuntimeError("Cannot resolve Python library {}. Use an interpreter with absolute library paths.".format(name))
            dependency = Path(name).resolve(strict=True)
            paths.update((name, str(dependency)))
            if dependency not in inspected:
                pending.append(dependency)
    return sorted(paths)
