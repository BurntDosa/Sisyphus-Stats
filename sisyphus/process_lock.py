"""Single-instance lock for the Sisyphus bot (macOS/Linux via fcntl, Windows via msvcrt)."""
from __future__ import annotations

import os
import socket
import sys
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    # msvcrt.locking raises OSError with EACCES (13) when another process holds
    # the byte, or EDEADLK (36) when its retry window runs out.
    _BUSY_ERRNOS = {13, 36}
else:
    import fcntl

    _BUSY_ERRNOS = {11, 35}  # EAGAIN on Linux, EWOULDBLOCK on macOS


LOCK_PATH = Path(__file__).resolve().parents[1] / ".automation" / "run" / "bot-instance.lock"


class BotInstanceAlreadyRunning(RuntimeError):
    """Raised when another Sisyphus process already owns the host lock."""


def _lock_file(handle) -> None:
    """Take a non-blocking exclusive lock on the open file or raise OSError."""
    if sys.platform == "win32":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(handle) -> None:
    if sys.platform == "win32":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class BotInstanceLock:
    """Hold an advisory lock for the entire lifetime of one bot process."""

    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> "BotInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            _lock_file(handle)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in _BUSY_ERRNOS:
                raise BotInstanceAlreadyRunning(
                    "Another Sisyphus bot instance is already running on this machine. "
                    "Stop the supervisor or existing process before starting a manual copy."
                ) from exc
            raise

        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\nhost={socket.gethostname()}\n")
        handle.flush()
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            _unlock_file(self._handle)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "BotInstanceLock":
        return self.acquire()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()


def acquire_bot_instance_lock(path: Path = LOCK_PATH) -> BotInstanceLock:
    return BotInstanceLock(path).acquire()
