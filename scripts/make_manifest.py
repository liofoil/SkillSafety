#!/usr/bin/env python3
"""Create or verify the public artifact's SHA-256 file manifest."""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "MANIFEST.sha256"
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "jobs",
    "logs",
    "results",
    "runs",
    "samples",
    "workdir",
}
EXCLUDED_NAMES = {"MANIFEST.sha256", ".envrc"}


def included_files() -> list[Path]:
    """Return stable source-artifact members, excluding generated/private data."""
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if path.name in EXCLUDED_NAMES:
            continue
        if any(part in EXCLUDED_DIRS for part in relative.parts):
            continue
        if path.suffix == ".pyc":
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(ROOT).as_posix())


def render_manifest() -> str:
    lines = []
    for path in included_files():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(ROOT).as_posix()
        lines.append(f"{digest}  {relative}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify MANIFEST.sha256 instead of rewriting it",
    )
    args = parser.parse_args(argv)
    expected = render_manifest()
    if args.check:
        if not MANIFEST.is_file():
            print("ERROR: MANIFEST.sha256 is absent", file=sys.stderr)
            return 1
        if MANIFEST.read_text(encoding="utf-8") != expected:
            print("ERROR: MANIFEST.sha256 is stale or incomplete", file=sys.stderr)
            return 1
        print(f"Verified {len(included_files())} files")
        return 0
    MANIFEST.write_text(expected, encoding="utf-8", newline="\n")
    print(f"Wrote {MANIFEST.name} for {len(included_files())} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
