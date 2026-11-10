"""Build the upstream native executor without any Node tooling."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent
SOURCE = json.loads((ROOT / "mxc-source.json").read_text())
LOCAL = ROOT / ".local"
EXECUTABLE = LOCAL / "bin" / SOURCE["executable"]


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_macos():
    if sys.platform != "darwin":
        raise RuntimeError("This POC implements macOS Seatbelt only. Linux and Windows are not implemented.")


def bootstrap():
    require_macos()
    for tool in ("git", "cargo", "rustc", "cc"):
        if not shutil.which(tool):
            raise RuntimeError("Missing {}. Install Rust and the Xcode Command Line Tools; see README.md.".format(tool))
    source = ROOT / ".cache" / "mxc"
    source.parent.mkdir(exist_ok=True)
    if not source.exists():
        subprocess.run(["git", "init", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "remote", "add", "origin", SOURCE["repository"]], check=True)
    origin = subprocess.check_output(["git", "-C", str(source), "remote", "get-url", "origin"], text=True).strip()
    if origin != SOURCE["repository"]:
        raise RuntimeError("Cached checkout has an unexpected remote: {}".format(origin))
    changes = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain", "--untracked-files=no"], text=True)
    if changes:
        raise RuntimeError("Cached MXC sources have local changes; use a clean checkout before building.")
    head = subprocess.run(["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.stdout.strip() != SOURCE["revision"]:
        subprocess.run(["git", "-C", str(source), "fetch", "--depth", "1", "origin", SOURCE["revision"]], check=True)
        subprocess.run(["git", "-C", str(source), "checkout", "--detach", "FETCH_HEAD"], check=True)
    # Fix the output directory even when the caller configured Cargo elsewhere.
    target = source / "src" / "target"
    print("Building native MXC at {} (no Node/npm required)".format(SOURCE["revision"]), flush=True)
    subprocess.run([
        "cargo", "build", "--locked", "--release", "--target-dir", str(target),
        "-p", SOURCE["cargo_package"],
    ], cwd=source / "src", check=True)
    EXECUTABLE.parent.mkdir(parents=True, exist_ok=True)
    temporary = EXECUTABLE.with_suffix(".tmp")
    shutil.copy2(target / "release" / SOURCE["executable"], temporary)
    temporary.chmod(0o755)
    os.replace(temporary, EXECUTABLE)
    metadata = {
        "source": SOURCE,
        "sha256": sha256(EXECUTABLE),
        "rustc": subprocess.check_output(["rustc", "--version"], cwd=source / "src", text=True).strip(),
    }
    (LOCAL / "runtime.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print("Installed {}".format(EXECUTABLE))
    return metadata
