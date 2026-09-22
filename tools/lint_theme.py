"""Validate a theme package without backend deps.

Usage: uv run tools/lint_theme.py --theme <slug>
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {".html", ".css", ".js", ".png", ".jpg", ".webp", ".woff2"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--theme", required=True)
    args = parser.parse_args()
    target = ROOT / "themes" / args.theme
    manifest = target / "manifest.json"
    if not manifest.is_file():
        print(f"missing {manifest}")
        return 1
    data = json.loads(manifest.read_text())
    for key in ("name", "version", "entry"):
        if key not in data:
            print(f"manifest missing key: {key}")
            return 1
    if not (target / data["entry"]).is_file():
        print(f"missing entry {data['entry']}")
        return 1
    bad: list[str] = []
    for path in sorted(target.rglob("*")):
        if path.is_symlink():
            bad.append(f"symlink: {path}")
        if (
            path.is_file()
            and path.suffix not in ALLOWED
            and path.name != "manifest.json"
        ):
            bad.append(f"extension not allowed: {path}")
        if path.name.startswith("."):
            bad.append(f"dotfile: {path}")
    total = sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    if total > 2 * 1024 * 1024:
        bad.append(f"exceeds 2MB: {total} bytes")
    if bad:
        print("\n".join(bad))
        return 1
    print(f"theme {args.theme} ok ({total} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
