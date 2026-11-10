"""Adapter behavior tests; require the real, bootstrapped native executor."""

import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from mxc_poc import Executor, Request
from mxc_poc.lab import PROBE
from mxc_poc.runtime import python_runtime_paths


class ExecutorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.executor = Executor()
        cls.runtime = python_runtime_paths()

    def test_nonzero_workload_exit_and_stderr_are_preserved(self):
        with TemporaryDirectory(prefix="mxc-exit-test-") as temporary:
            request = Request(["/bin/sh", "-c", "printf 'application failure' >&2; exit 7"], Path(temporary))
            result = self.executor.run(request)
        self.assertEqual(result.exit_code, 7)
        self.assertIn("application failure", result.stderr)
        self.assertFalse(result.errors)
        with self.assertRaises(RuntimeError):
            result.json_output()

    def test_missing_file_cannot_pass_as_permission_denial(self):
        with TemporaryDirectory(prefix="mxc-missing-test-") as temporary:
            root = Path(temporary).resolve()
            request = Request(
                [str(Path(sys.executable).resolve()), "-I", "-S", "-B", str(PROBE), "denied", str(root / "missing.txt")],
                root, readonly=self.runtime + [str(PROBE.parent), str(root)],
            )
            result = self.executor.run(request)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("FileNotFoundError", result.stderr)
        with self.assertRaises(RuntimeError):
            result.json_output()

    def test_host_environment_is_not_inherited_by_workload(self):
        with TemporaryDirectory(prefix="mxc-env-test-") as temporary:
            root = Path(temporary).resolve()
            request = Request(
                [str(Path(sys.executable).resolve()), "-I", "-S", "-B", "-c",
                 "import json,os; print(json.dumps({'value':os.environ.get('MXC_POC_SYNTHETIC_SECRET')}))"],
                root, readonly=self.runtime + [str(root)],
            )
            with patch.dict(os.environ, {"MXC_POC_SYNTHETIC_SECRET": "synthetic-host-only-value"}):
                result = self.executor.run(request)
        self.assertEqual(result.json_output(), {"value": None})


if __name__ == "__main__":
    unittest.main()
