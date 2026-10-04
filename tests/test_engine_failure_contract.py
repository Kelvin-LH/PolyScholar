# SPDX-License-Identifier: AGPL-3.0-only
"""Independent regression checks for failed workers; no real API or document data."""
from dataclasses import replace
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from integrations import engines


class WorkerFailureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "synthetic.pdf"
        self.source.write_bytes(b"%PDF-1.4\nsynthetic\n")
        self.req = engines.Request(
            "babeldoc", Path(sys.executable), self.source, self.base / "output",
            "https://api.example.test", "synthetic-model",
            allow_document_upload=True, allow_asset_download=True,
        )

    def assert_worker_failure(self, program, message):
        worker = self.base / "worker.py"
        # 先消费真实输入，避免管道提前关闭掩盖目标失败路径。
        # Consume the request so BrokenPipe cannot mask the intended failure.
        worker.write_text(
            "import json,os,pathlib,sys\n"
            "body=json.load(sys.stdin)\n"
            "assert body['api_key'] not in ' '.join(sys.argv)\n"
            "assert all(body['api_key'] not in v for v in os.environ.values())\n"
            "assert not list(pathlib.Path.cwd().iterdir())\n" + program,
            encoding="utf-8",
        )
        directories = []
        processes = []
        original = engines.start_process

        def launch(*args, **kwargs):
            directories.append(Path(kwargs['cwd']))
            process = original(*args, **kwargs)
            processes.append(process)
            return process

        with patch.object(engines, "check_version"), \
             patch.object(engines, "start_process", side_effect=launch), \
             patch.object(engines, "command", return_value=[sys.executable, str(worker)]):
            with self.assertRaisesRegex(RuntimeError, message):
                engines.run(self.req, "synthetic-secret")
        self.assertEqual(len(directories), 1)
        self.assertFalse(directories[0].exists())
        self.assertIsNotNone(processes[0].poll())
        self.assertFalse(self.req.output.exists())
        self.assertFalse((self.req.output / "polyscholar-export.json").exists())

    def test_nonzero_exit_is_not_success_and_cleans_credentials(self):
        self.assert_worker_failure("raise SystemExit(7)\n", "Engine failed")

    def test_zero_exit_without_output_is_not_success(self):
        self.assert_worker_failure("pass\n", "valid PDF output")

    def test_invalid_pdf_output_is_not_success(self):
        self.assert_worker_failure(
            f"from pathlib import Path\nPath({str(self.req.output / 'bad.pdf')!r}).write_bytes(b'not-pdf')\n",
            "valid PDF output",
        )

    def test_source_modification_rejects_success(self):
        self.assert_worker_failure(
            f"from pathlib import Path\nPath({str(self.source)!r}).write_bytes(b'%PDF-1.4 modified')\n"
            f"Path({str(self.req.output / 'translated.pdf')!r}).write_bytes(b'%PDF-1.4 synthetic output')\n",
            "Source PDF changed",
        )

    def test_validation_failure_creates_no_output(self):
        with self.assertRaises(ValueError):
            engines.run(replace(self.req, allow_document_upload=False), "synthetic-secret")
        self.assertFalse(self.req.output.exists())


if __name__ == "__main__":
    unittest.main()
