"""From the repository root: python3 -m examples.run_python"""

import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from mxc_poc import Executor, Request
from mxc_poc.runtime import python_runtime_paths

with TemporaryDirectory(prefix="mxc-python-example-") as temporary:
    request = Request(
        argv=[str(Path(sys.executable).resolve()), "-I", "-S", "-B", "-u", "-c", "import json; print(json.dumps({'answer': 6 * 7}))"],
        cwd=Path(temporary),
        readonly=python_runtime_paths() + [str(Path(temporary).resolve())],
        timeout_ms=3000,
    )
    result = Executor().run(request)
    print(json.dumps(result.json_output(), indent=2))
