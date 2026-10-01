# SPDX-License-Identifier: AGPL-3.0-only
"""Protected destinations and atomic local exports."""
import hashlib
import os
from pathlib import Path
import tempfile


def _atomic_export(destination, data, *, protected_roots=(), original_paths=(), original_hashes=(), protected_files=()):
    destination = Path(destination)
    if not destination.is_absolute():
        raise ValueError('导出位置必须是本机绝对路径。')
    if destination.is_symlink():
        raise ValueError('不能导出到符号链接。')
    target = destination.parent.resolve(strict=True) / destination.name
    if target.exists() and not target.is_file():
        raise ValueError('导出位置必须是文件。')
    for directory in protected_roots:
        root = Path(directory).resolve()
        if target == root or target.is_relative_to(root):
            raise ValueError('不能覆盖应用数据、运行资源或内部翻译产物。')
    for internal in protected_files:
        if target.exists() and Path(internal).exists() and os.path.samefile(target, internal):
            raise ValueError('不能覆盖内部文件的硬链接。')
    for original in original_paths:
        path = Path(original).resolve()
        if target == path:
            raise ValueError('不能覆盖原始导入文件。')
        if target.exists() and path.exists():
            try:
                if os.path.samefile(target, path):
                    raise ValueError('不能覆盖原始导入文件的硬链接。')
            except FileNotFoundError:
                continue
    if target.exists() and target.stat().st_size <= 100 * 1024 * 1024:
        with target.open('rb') as stream:
            prefix = stream.read(5)
            if prefix == b'%PDF-':
                digest = hashlib.sha256(prefix)
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
                if digest.hexdigest() in set(original_hashes):
                    raise ValueError('不能覆盖与库内原文相同的 PDF。')
    descriptor, temporary = tempfile.mkstemp(prefix='.polyscholar-export-', dir=target.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def atomic_export(destination, data, **protection):
    try:
        return _atomic_export(destination, data, **protection)
    except OSError:
        raise ValueError('导出失败，请检查目标目录是否存在且可写。') from None
