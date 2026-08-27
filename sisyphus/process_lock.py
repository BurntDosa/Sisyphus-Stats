"""Single-instance lock for the Mac-hosted Sisyphus bot."""
from __future__ import annotations

import fcntl
import os
import socket
from pathlib import Path


LOCK_PATH = Path(__file__).resolve().parents[1] / ".automation" / "run" / "bot-instance.lock"


class BotInstanceAlreadyRunning(RuntimeError):
    """Raised when another Sisyphus process already owns the host lock."""


class BotInstanceLock:
    """Hold an advisory lock for the entire lifetime of one bot process."""

    def __init__(self, path: Path = LOCK_PATH) -> None:
        self.path = Path(path)
        self._handle = None

    def acquire(self) -> "BotInstanceLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            handle.close()
            if isinstance(exc, BlockingIOError) or getattr(exc, "errno", None) in {11, 35}:
                raise BotInstanceAlreadyRunning(
                    "Another Sisyphus bot instance is already running on this Mac. "
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
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "BotInstanceLock":
        return self.acquire()

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.release()


def acquire_bot_instance_lock(path: Path = LOCK_PATH) -> BotInstanceLock:
    return BotInstanceLock(path).acquire()
