"""Scaffold a new sandboxed theme. Run: uv run tools/new_theme.py <slug> --name "..."

Creates themes/<slug>/ with manifest.json, entry index.html, theme.css,
and preview wiring against api/fixtures/. Never edits another theme.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <link rel="stylesheet" href="theme.css" />
  <title>{name}</title>
</head>
<body>
  <!-- Sandboxed: receives read-only prayer JSON via postMessage, no admin access. -->
  <main id="app" data-next-event="/api/next-event" data-events="/api/events">
    <h1 id="clock">--:--</h1>
    <p id="next">Loading fixtures…</p>
  </main>
  <script src="theme.js"></script>
</body>
</html>
"""

THEME_JS = """// Read-only consumer of the contract.
// Polls next-event; upgrades to SSE when present.
async function tick() {
  try {
    const r = await fetch('/api/next-event', {cache: 'no-store'});
    const e = await r.json();
    document.getElementById('next').textContent =
      e.state + ' → ' + (e.next_prayer || '');
  } catch { /* keep last rendered state offline */ }
}
setInterval(tick, 5000); tick();
"""

THEME_CSS = """:root { color-scheme: dark; }
body { margin: 0; font-family: system-ui, sans-serif; }
#app { min-height: 100vh; display: grid; place-content: center; text-align: center; }
#clock { font-size: 12vh; margin: 0; }
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    target = ROOT / "themes" / args.slug
    if target.exists():
        raise SystemExit(f"exists: {target}")
    (target).mkdir(parents=True)
    (target / "manifest.json").write_text(
        json.dumps(
            {"name": args.name, "version": "0.1.0", "entry": "index.html"},
            indent=2,
        )
        + "\n"
    )
    (target / "index.html").write_text(INDEX_HTML.format(name=args.name))
    (target / "theme.css").write_text(THEME_CSS)
    (target / "theme.js").write_text(THEME_JS)
    print(f"created {target}")


if __name__ == "__main__":
    main()
