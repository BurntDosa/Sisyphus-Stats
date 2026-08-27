"""Offline regression check for the same-host bot process lock."""
from __future__ import annotations

import sys
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sisyphus.process_lock import BotInstanceAlreadyRunning, BotInstanceLock  # noqa: E402


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "bot-instance.lock"
        first = BotInstanceLock(path).acquire()
        try:
            second = BotInstanceLock(path)
            try:
                second.acquire()
            except BotInstanceAlreadyRunning:
                pass
            else:
                raise AssertionError("second process unexpectedly acquired the lock")
        finally:
            first.release()

        BotInstanceLock(path).acquire().release()

        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "import sys, time; "
                    "sys.path.insert(0, sys.argv[2]); "
                    "from sisyphus.process_lock import BotInstanceLock; "
                    "lock = BotInstanceLock(__import__('pathlib').Path(sys.argv[1])).acquire(); "
                    "time.sleep(3)"
                ),
                str(path),
                str(ROOT),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.2)
            try:
                BotInstanceLock(path).acquire()
            except BotInstanceAlreadyRunning:
                pass
            else:
                raise AssertionError("a second process unexpectedly acquired the lock")
        finally:
            holder.terminate()
            holder.wait(timeout=5)
    print("Process lock smoke checks passed.")


if __name__ == "__main__":
    main()
