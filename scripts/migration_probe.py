# SPDX-License-Identifier: AGPL-3.0-only
"""Bounded timing diagnostics for synthetic native migration checks only.

仅用于合成检查：固定阶段、调用次数和耗时，不记录参数、路径或异常内容。
"""
from contextlib import ExitStack
from functools import wraps
import json
import threading
import time
from unittest.mock import patch


class MigrationTimingProbe:
    """Measure inclusive stage time without changing deadlines or IO behavior.

    嵌套阶段耗时不可相加；不改业务时限、不绕过校验、不重试失败。
    """

    def __init__(self):
        self._patches = ExitStack()
        self._lock = threading.Lock()
        self._stages = {}

    @staticmethod
    def _targets():
        from multiprocessing.process import BaseProcess
        from polyscholar.migration_recovery import MigrationRecoverySession
        from polyscholar.zotero_migration import (
            ZoteroMigrationImporter, ZoteroMigrationPlanner,
            ZoteroPdfValidator, ZoteroSnapshotReader,
        )
        from polyscholar.zotero_resources import ZoteroResourcePlanner

        return (
            ('preview', ZoteroMigrationImporter, 'preview'),
            ('snapshot', ZoteroSnapshotReader, 'graph'),
            ('source-read', ZoteroSnapshotReader, '_read'),
            ('metadata-plan', ZoteroMigrationPlanner, 'plan'),
            ('resource-plan', ZoteroResourcePlanner, 'plan'),
            ('pdf-validation', ZoteroPdfValidator, 'validate'),
            ('process-start', BaseProcess, 'start'),
            ('recovery-write', MigrationRecoverySession, 'save'),
            ('recovery-cleanup', MigrationRecoverySession, 'finish'),
        )

    def _measure(self, name, original):
        @wraps(original)
        def measured(*args, **kwargs):
            started = time.perf_counter()
            succeeded = False
            try:
                result = original(*args, **kwargs)
                succeeded = True
                return result
            finally:
                elapsed = (time.perf_counter() - started) * 1000
                with self._lock:
                    stage = self._stages.setdefault(
                        name, dict(calls=0, errors=0, inclusiveMs=0.0, maxMs=0.0))
                    stage['calls'] += 1
                    stage['errors'] += not succeeded
                    stage['inclusiveMs'] += elapsed
                    stage['maxMs'] = max(stage['maxMs'], elapsed)
        return measured

    def __enter__(self):
        for name, owner, method in self._targets():
            self._patches.enter_context(
                patch.object(owner, method, self._measure(name, getattr(owner, method))))
        return self

    def __exit__(self, *_exception):
        self._patches.close()
        with self._lock:
            report = {
                name: {key: round(value, 3) if isinstance(value, float) else value
                       for key, value in stage.items()}
                for name, stage in sorted(self._stages.items())
            }
        print('Synthetic migration stage timings:', json.dumps(report), flush=True)
