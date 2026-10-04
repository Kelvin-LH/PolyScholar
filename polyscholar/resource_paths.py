# SPDX-License-Identifier: AGPL-3.0-only
"""Locate trusted shipped documentation / 定位随软件分发的可信文档资源。"""
from pathlib import Path, PureWindowsPath
import sys


def resource_path(relative):
    """Source, wheel and frozen resources share one lookup policy.
    源码、wheel 和冻结资源使用同一定位规则，不读取任意用户资料路径。
    """
    item = Path(relative)
    windows_item = PureWindowsPath(relative)
    # Reject rooted/drive paths on every host, including Windows drive-relative forms.
    # 在所有系统拒绝根路径、盘符及盘符相对路径，防止跨平台定位越界。
    if item.anchor or windows_item.anchor or '..' in item.parts or '..' in windows_item.parts:
        raise ValueError('资源名称无效。')
    roots = []
    if getattr(sys, 'frozen', False):
        if sys.platform == 'darwin':
            roots.append(Path(sys.executable).parents[1] / 'Resources/resources')
        roots.append(Path(getattr(sys, '_MEIPASS', Path(sys.executable).parent)) / 'resources')
    else:
        roots.append(Path(__file__).resolve().parents[1])
        roots.append(Path(sys.prefix) / 'share/polyscholar')
    return next((root / item for root in roots if (root / item).exists()), roots[0] / item)
