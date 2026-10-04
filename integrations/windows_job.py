# SPDX-License-Identifier: AGPL-3.0-only
"""Windows process-tree containment for children held at a trusted stdin gate.

Windows 进程树包含：调用方必须在绑定成功前保持可信 stdin 启动门闩。
"""
import ctypes
from ctypes import wintypes
import math
import os
import subprocess
import threading
import time


class _BasicLimit(ctypes.Structure):
    _fields_ = [
        ('PerProcessUserTimeLimit', ctypes.c_longlong),
        ('PerJobUserTimeLimit', ctypes.c_longlong),
        ('LimitFlags', wintypes.DWORD),
        ('MinimumWorkingSetSize', ctypes.c_size_t),
        ('MaximumWorkingSetSize', ctypes.c_size_t),
        ('ActiveProcessLimit', wintypes.DWORD),
        ('Affinity', ctypes.c_size_t),
        ('PriorityClass', wintypes.DWORD),
        ('SchedulingClass', wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
        'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount',
    )]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [
        ('BasicLimitInformation', _BasicLimit),
        ('IoInfo', _IoCounters),
        ('ProcessMemoryLimit', ctypes.c_size_t),
        ('JobMemoryLimit', ctypes.c_size_t),
        ('PeakProcessMemoryUsed', ctypes.c_size_t),
        ('PeakJobMemoryUsed', ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [(name, ctypes.c_longlong) for name in (
        'TotalUserTime', 'TotalKernelTime', 'ThisPeriodTotalUserTime',
        'ThisPeriodTotalKernelTime',
    )] + [(name, wintypes.DWORD) for name in (
        'TotalPageFaultCount', 'TotalProcesses', 'ActiveProcesses',
        'TotalTerminatedProcesses',
    )]


def _kernel_api():
    if os.name != 'nt':
        raise OSError('Windows Job Objects are available only on Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        'SetHandleInformation': (
            [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD], wintypes.BOOL),
        'SetInformationJobObject': (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD],
            wintypes.BOOL),
        'QueryInformationJobObject': (
            [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
             ctypes.c_void_p], wintypes.BOOL),
        'OpenProcess': (
            [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
        'AssignProcessToJobObject': (
            [wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        'TerminateJobObject': (
            [wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        method = getattr(api, name)
        method.argtypes, method.restype = arguments, result
    return api


class WindowsJob:
    """Own one unnamed, non-inheritable kill-on-close Job Object.

独占无名、不可继承的关闭即终止 Job；线程间关闭和取消使用同一锁。
"""
    def __init__(self):
        self._lock = threading.RLock()
        self._handle = None
        self._api = _kernel_api()
        handle = self._api.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        try:
            if not self._api.SetHandleInformation(handle, 1, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            limits = _ExtendedLimit()
            # No breakaway flags: descendants must remain in the same job chain.
            # 不启用 breakaway，后代必须留在同一 Job 链中。
            limits.BasicLimitInformation.LimitFlags = 0x2000
            if not self._api.SetInformationJobObject(
                    handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close()
            raise

    def _require_handle(self):
        if self._handle is None:
            raise OSError('Windows Job Object is closed')
        return self._handle

    def assign(self, process: subprocess.Popen):
        """Bind before releasing the trusted child's startup gate.

先绑定成功，调用方才可释放可信子进程的启动门闩。
"""
        with self._lock:
            handle = self._require_handle()
            if (type(process.pid) is not int or process.pid <= 0
                    or process.poll() is not None):
                raise OSError('Cannot assign an exited process')
            target = self._api.OpenProcess(0x100 | 0x1, False, process.pid)
            if not target:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                # Popen owns its original process identity; reject exit/PID reuse.
                # Popen 保留原进程身份，拒绝退出后重新使用的 PID。
                if process.poll() is not None:
                    raise OSError('Process exited before Job assignment')
                if not self._api.AssignProcessToJobObject(handle, target):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                if not self._api.CloseHandle(target):
                    raise ctypes.WinError(ctypes.get_last_error())

    def terminate(self, timeout=5):
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout < 0):
            raise ValueError('Job termination timeout must be finite and nonnegative')
        with self._lock:
            handle = self._require_handle()
            if not self._api.TerminateJobObject(handle, 1):
                raise ctypes.WinError(ctypes.get_last_error())
            deadline = time.monotonic() + timeout
            while True:
                accounting = _Accounting()
                if not self._api.QueryInformationJobObject(
                        handle, 1, ctypes.byref(accounting),
                        ctypes.sizeof(accounting), None):
                    raise ctypes.WinError(ctypes.get_last_error())
                # The parent can already be dead while descendants remain active.
                # 父进程已退出不代表后代结束，必须查询整个 Job 的活动数量。
                if accounting.ActiveProcesses == 0:
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Windows Job still has active processes')
                time.sleep(min(0.02, remaining))

    def close(self):
        with self._lock:
            if self._handle is not None:
                if not self._api.CloseHandle(self._handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._handle = None

    def __del__(self):
        try:
            self.close()
        except (AttributeError, OSError):
            pass
