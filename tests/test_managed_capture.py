# SPDX-License-Identifier: AGPL-3.0-only
"""Managed capture failure paths / 托管输出采集的失败路径。"""
from pathlib import Path
import subprocess
import sys
import time
import unittest

from integrations.engines import limited_environment
from integrations.managed_process import run_captured


class ManagedCaptureTests(unittest.TestCase):
    def test_blocked_stdin_is_within_timeout_and_callbacks_finish(self):
        events = []
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            run_captured([sys.executable, '-c', 'import time; time.sleep(30)'],
                env=limited_environment(), timeout=0.1, input_data=b'x' * (2 * 1024 * 1024),
                on_spawn=lambda process: events.append(('spawn', process.pid)),
                on_done=lambda process: events.append(('done', process.pid)))
        self.assertEqual([event[0] for event in events], ['spawn', 'done'])
        self.assertEqual(events[0][1], events[1][1])
        self.assertLess(time.monotonic() - started, 6)

    def test_worker_rejects_invalid_input_without_disclosing_credentials(self):
        worker = Path(__file__).resolve().parents[1] / 'integrations/html_worker.py'
        output, errors, code, truncated = run_captured(
            [sys.executable, '-I', str(worker)], env=limited_environment(), timeout=5,
            input_data=b'{"token":"synthetic-private-key","timeout":-1}')
        self.assertEqual(code, 0)
        self.assertEqual(output, b'{"ok":false}')
        self.assertEqual(errors, b'')
        self.assertFalse(truncated)


if __name__ == '__main__':
    unittest.main()
