"""Offline checks for one-time curated release changelog behavior."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sisyphus.changelog import needs_curated_prompt


def main() -> None:
    assert needs_curated_prompt("v2.1.6", "abc", {"curated_processed_versions": {}})
    processed = {"curated_processed_versions": {"v2.1.6": "abc"}}
    assert not needs_curated_prompt("v2.1.6", "def", processed)
    assert not needs_curated_prompt(
        "v2.0.0",
        "def",
        {"curated_processed_versions": {}, "v2_curated_processed_sha": "abc"},
    )
    assert not needs_curated_prompt("v2.1.5", "abc", processed)
    print("Changelog one-time smoke checks passed.")


if __name__ == "__main__":
    main()
