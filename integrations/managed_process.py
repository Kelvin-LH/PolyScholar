# SPDX-License-Identifier: AGPL-3.0-only
"""Shared translation process ownership / 翻译进程的共同生命周期管理。"""
import os
from pathlib import Path
import subprocess
import threading

if __package__:
    from .windows_job import WindowsJob
else:
    from windows_job import WindowsJob


class ProcessCleanupError(OSError):
    """Process-tree exit is unproven / 尚未确认进程树完全退出。"""


class WindowsProcess:
    """A blocked bootstrap is assigned before any worker or engine can start.

    Job 句柄仅归启动者所有；启动者硬退出时 Windows 终止其进程树。
    The owning handle is never inherited, including by the bootstrap itself.
    """

    def __init__(self, command, *, env, cwd=None, stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 gate_path=None):
        if stdin not in (subprocess.PIPE, subprocess.DEVNULL):
            raise ValueError('Only managed stdin is supported')
        if (not isinstance(command, list) or not command
                or any(not isinstance(value, str) or '\x00' in value for value in command)):
            raise ValueError('Invalid launch command')
        gate = (Path(gate_path).resolve() if gate_path is not None
                else Path(__file__).resolve().with_name('process_gate.py'))
        if not gate.is_file():
            raise OSError('Packaged launch gate is missing')
        self._lock = threading.RLock()
        self._job = WindowsJob()
        self._process = None
        self._stopped = False
        self._cleanup_failed = False
        try:
            self._process = subprocess.Popen(
                [command[0], '-I', str(gate), *command], cwd=cwd, env=env,
                stdin=subprocess.PIPE, stdout=stdout, stderr=stderr,
                close_fds=True,
            )
            self._job.assign(self._process)
            # 单字节门闩不会填满管道；no worker code runs before assignment.
            self._process.stdin.write(b'\x01')
            self._process.stdin.flush()
            if stdin == subprocess.DEVNULL:
                self._process.stdin.close()
        except BaseException:
            self._abort_launch()
            raise

    def _abort_launch(self):
        try:
            try:
                if self._process is not None:
                    if self._process.stdin:
                        try:
                            self._process.stdin.close()
                        except OSError:
                            pass
                    if self._process.poll() is None:
                        self._process.kill()
                    self._process.wait(timeout=5)
                self._job.terminate(timeout=5)
            finally:
                self._job.close()
        except (OSError, subprocess.TimeoutExpired):
            raise ProcessCleanupError('Launch process cleanup was not confirmed') from None
        finally:
            if self._process is not None:
                for stream in (self._process.stdout, self._process.stderr):
                    if stream:
                        stream.close()

    @property
    def pid(self):
        return self._process.pid

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    @property
    def stderr(self):
        """Read-only captured stream / 只读捕获流，沿用受管进程生命周期。"""
        return self._process.stderr

    @property
    def returncode(self):
        return self._process.returncode

    def poll(self):
        return self._process.poll()

    def wait(self, timeout=None):
        return self._process.wait(timeout=timeout)

    def stop(self):
        with self._lock:
            if self._stopped:
                return
            if self._cleanup_failed:
                raise ProcessCleanupError('Process cleanup was not confirmed')
            # 父进程退出不等于整棵树退出；query the Job even after poll() ends.
            try:
                try:
                    self._job.terminate(timeout=5)
                    self._process.wait(timeout=5)
                finally:
                    self._job.close()
            except (OSError, subprocess.TimeoutExpired):
                self._cleanup_failed = True
                raise ProcessCleanupError('Process cleanup was not confirmed') from None
            self._stopped = True


def start_process(command, *, env, cwd=None, stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                  start_new_session=False, gate_path=None):
    """Keep POSIX sessions; Windows launches through the same owned gate.

    共用入口避免桌面 sidecar 和独立引擎采用不同的 Windows 终止策略。
    """
    if os.name == 'nt':
        return WindowsProcess(command, env=env, cwd=cwd, stdin=stdin,
                              stdout=stdout, stderr=stderr, gate_path=gate_path)
    return subprocess.Popen(command, env=env, cwd=cwd, stdin=stdin,
                            stdout=stdout, stderr=stderr,
                            start_new_session=start_new_session)


def run_captured(command, *, env, timeout, input_data=None, max_bytes=1024 * 1024,
                 on_spawn=None, on_done=None):
    """Capture bounded output while supervising the complete process tree.

    在监管整个进程树的同时限制输出内存；stdin 写入也纳入同一超时。
    Returns (stdout, stderr, exit_code, truncated) / 返回输出、退出码与截断标记。
    """
    # Deferred import avoids the engines/managed_process module cycle.
    # 延迟导入避免引擎模块和生命周期模块的循环依赖。
    from .engines import stop_process
    process = start_process(command, env=env,
        stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    threads = []

    def drain(stream, index):
        while True:
            chunk = stream.read(8192)
            if not chunk:
                break
            available = max(0, max_bytes - len(buffers[index]))
            buffers[index].extend(chunk[:available])
            truncated[index] |= len(chunk) > available

    def write_input():
        try:
            process.stdin.write(input_data)
            process.stdin.flush()
        except (BrokenPipeError, OSError):
            pass  # The child exit code decides success / 由子进程退出码判定结果。
        finally:
            try:
                process.stdin.close()
            except OSError:
                pass

    try:
        for index, stream in enumerate((process.stdout, process.stderr)):
            thread = threading.Thread(target=drain, args=(stream, index))
            threads.append(thread)
            thread.start()
        if on_spawn:
            on_spawn(process)
        if input_data is not None:
            writer = threading.Thread(target=write_input)
            threads.append(writer)
            writer.start()
        exit_code = process.wait(timeout=timeout)
    finally:
        # Parent exit never proves descendants exited / 父进程结束不代表后代结束。
        try:
            stop_process(process)
        finally:
            for thread in threads:
                thread.join(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
            if on_done:
                on_done(process)
    return bytes(buffers[0]), bytes(buffers[1]), exit_code, any(truncated)
