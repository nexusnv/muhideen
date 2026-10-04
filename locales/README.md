# Locales (display string tables)

`en.json` is the full display string table (`prayer`, `boundary`, `display`
groups). Arabic prayer names are NOT in the table: they are invariant UI,
rendered always regardless of language. `ms.json` covers the `prayer` +
`boundary` groups; the label triples live in `src/muhideen/views/display.py`
(`PRAYER_LABELS`/`BOUNDARY_LABELS`) and the honesty test
(`tests/unit/test_locales.py`) pins every table value against its owner.

There is no admin UI and no client-side string loading: the display is
server-rendered with the requesting display's language already applied.

## Adding a language

1. Copy `en.json` to `<lang>.json` (same keys, translated values; keep `{name}` placeholders).
2. Extend the label triples in `src/muhideen/views/display.py` with the new
   language column and wire it into the display context's language select.
3. Re-point the honesty test at every table (parametrize `en.json` → all `*.json`).
