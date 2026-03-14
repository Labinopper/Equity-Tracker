"""Small PID-file lock helper for single-instance local processes."""

from __future__ import annotations

import ctypes
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def _utcnow_text() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return int(exit_code.value) == STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@dataclass
class ProcessLock:
    path: Path
    pid: int

    def release(self) -> None:
        try:
            if self.path.exists():
                payload = json.loads(self.path.read_text(encoding="utf-8"))
                if int(payload.get("pid") or 0) == self.pid:
                    self.path.unlink(missing_ok=True)
        except Exception:
            self.path.unlink(missing_ok=True)


def active_process_lock_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        pid = int(payload.get("pid") or 0)
    except Exception:
        return None
    if pid and _pid_is_alive(pid):
        return pid
    return None


def acquire_process_lock(path: Path) -> ProcessLock | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    current_pid = os.getpid()
    payload = {
        "pid": current_pid,
        "claimed_at": _utcnow_text(),
    }

    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            existing_pid = int(existing.get("pid") or 0)
        except Exception:
            existing_pid = 0
        if existing_pid and _pid_is_alive(existing_pid) and existing_pid != current_pid:
            return None
        path.unlink(missing_ok=True)

    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
    return ProcessLock(path=path, pid=current_pid)
