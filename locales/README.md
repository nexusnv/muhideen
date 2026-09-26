# Locales (future translation seam)

`en.json` is the full English string table for the UI. Arabic prayer names
are NOT in the table: they are invariant UI, rendered always regardless of
language.

## Adding a language

1. Copy `en.json` to `<lang>.json` (same keys, translated values; keep `{name}` placeholders).
2. Wire a loader: templates need a `T()` Jinja global reading the active table with en-fallback; `admin.js` needs the table embedded (render as JSON in a `<script>` block or a `GET /api/strings?lang=` endpoint — endpoint is a contract addition, decide then).
3. Re-point the honesty test at every table (parametrize `en.json` → all `*.json`).
4. Add a language switcher back to the three admin templates (removed in the English-only strip; see git history).
