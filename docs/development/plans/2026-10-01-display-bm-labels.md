# Trilingual prayer labels (BM) on the display — Implementation Plan (issue #37)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: plan self-review below, `oracle` on the branch diff before PR handoff. Do NOT commit this plan file until the slice is approved for implementation.

**Goal:** Render Malay prayer names (Subuh, Zohor, Asar, Maghrib, Isyak, Jumaat; Syuruk/Imsak/Dhuha) on the public display hero, cards, and boundary strip alongside English and Arabic. Runtime source is the triple label tables in `views/display.py`; `locales/ms.json` is a pinned file-based copy (subset table, not read at runtime in this slice) for the future full-i18n loader.

**Architecture:** Extend the single-owner label tables in `views/display.py` from pairs to triples `(en, ar, bm)` so keys cannot drift between languages; `build_display_context` carries `bm` keys next to `en`/`ar`; `display.html` renders the third name. `locales/ms.json` holds `prayer` + `boundary` groups only (prayer-label scope — not the full UI table) and is pinned to the triples by the honesty test + key-parity check; no runtime code reads it yet (loader/`T()` global/endpoint decision stays in the follow-up slice per `locales/README.md` steps 2–4). No DTO/fixture/contract change (display HTML only), no switcher, no admin strings, no `pre_note`/banner translation (explicit non-goals).

**Tech Stack:** Python 3.11+, Jinja templates, pytest markers `unit/e2e`.

**References:** Issue #37; `src/muhideen/views/display.py:17-30` (`PRAYER_LABELS`/`BOUNDARY_LABELS` pairs); `src/muhideen/views/display.py:109,117,139,173-174,186` (unpack sites + `next_name_en`/`ar` + `pre_note`); `src/muhideen/views/templates/display.html:14,20,37,50,71,81` (render sites); `locales/en.json` (table shape); `locales/README.md` (4-step language procedure — this slice deliberately diverges from step 1 same-keys, see decision 2); `tests/unit/test_locales.py` (honesty test); `tests/unit/test_display_context.py`; `tests/e2e/test_display.py`; BM spellings verified against JAKIM e-solat BM pages + waktusolat/MSIA apps (Subuh/Zohor/Asar/Maghrib/Isyak, Syuruk, Jumaat for Friday).

**Branch:** `feature/display-bm-labels` (cut when approved; base on `main` — independent of `feature/calc-alfalak`)

---

## Background the implementer needs

### BM spellings (locked, JAKIM-consistent)

| key | en | ar (unchanged) | bm |
|---|---|---|---|
| fajr | Fajr | الفجر | Subuh |
| dhuhr | Dhuhr | الظهر | Zohor |
| asr | Asr | العصر | Asar |
| maghrib | Maghrib | المغرب | Maghrib |
| isha | Isha | العشاء | Isyak |
| jumuah | Jumuah | الجمعة | Jumaat |
| imsak | Imsak | الإمساك | Imsak |
| syuruq | Syuruq | الشروق | Syuruk |
| dhuha | Dhuha | الضحى | Dhuha |

### Design decisions (locked)

1. Triples, not a second dict: `(en, ar, bm)` in the same tables; unpack all three sites (`display.py:109` hero labels, `display.py:117` cards, `display.py:139` bounds) plus return-dict keys (`display.py:173-174` `next_name_bm`). Order is `(en, ar, bm)` — BM appended as `[2]` so existing `en`/`ar` indices stay byte-identical (minimal diff vs historic `(en, bm, ar)` in `b638060`). Template order is therefore `en · ar · bm` (differs from historic `en · bm · ar`; intentional). One table means a missing BM name is a shape error at import/test time, not silent drift.
2. `ms.json` holds `prayer` + `boundary` groups only and is NOT read at runtime in this slice. Full-UI BM (admin strings, banners, switcher, `T()` global, endpoint-vs-embed decision) is a separate i18n slice per `locales/README.md` steps 2–4; this plan does step 1 (table) + step 3 scoped to the subset (per-file group expectations). This deliberately diverges from README step 1 ("same keys as `en.json`"); the follow-up loader slice must reconcile to full-table parity. Honesty test requires per-file expectations PLUS key-parity: `set(ms["prayer"]) == set(en["prayer"])` and same for `boundary`, otherwise a dropped `jumuah` would pass silently.
3. Template format: `{{ en }} · {{ ar }} · {{ bm }}` on hero, cards, and bounds. Bounds change from `en` only to `en · ar · bm` + time is an intentional scope expansion (bounds never had Arabic, even when trilingual — see `b638060`): it keeps one rule everywhere but needs product sign-off; pinned by a dedicated bounds e2e assertion. `pre_note` (`display.py:186` `Preparing for …`) and banners stay English-only in this slice (display chrome, like admin strings — full-i18n follow-up).

## File Structure

- Modify first: `src/muhideen/views/display.py` — triples + `bm` context keys (`next_name_bm`, `c.bm`, `b.bm` + `b.ar`)
- Create: `locales/ms.json` — `prayer` + `boundary` groups, BM values above (pinned copy, not read at runtime yet)
- Modify: `src/muhideen/views/templates/display.html` — render `bm` at hero/cards/bounds
- Modify: `tests/unit/test_locales.py` — per-file group expectations + `en`↔`ms` key-parity for `prayer`/`boundary`
- Test: `tests/unit/test_display_context.py` — `bm` keys + triple shape (incl. all themes)
- Test: `tests/e2e/test_display.py` — BM strings in rendered HTML (cards + hero + bounds + Friday Jumuah)
- Docs: `CHANGELOG.md` Added entry (display-only, no contract change)

No DTO, fixture, migration, or settings change. `pre_note`/banners unchanged (English-only).

---

### Task 1: Triple label tables + context keys

**Files:** `src/muhideen/views/display.py:17-30,109,117,139,173-174`

**Goal:** One table owns all three names; context carries `bm` everywhere `en` goes. Runs first — `ms.json` honesty pin (Task 2) requires BM strings to exist in `display.py`.

- [ ] Failing test: add `test_context_carries_bm_names` to `tests/unit/test_display_context.py`: default theme first card `bm == "Subuh"`, hero `next_name_bm == "Zohor"` for a pinned `next_prayer="dhuhr"` day, `next_name_bm == "Jumaat"` for `next_prayer="jumuah"`, bounds carry `bm` (+ `ar`), and `bm` keys present under `midnight`/`system`/`compact` theme variants (assert `ctx["cards"][0]["bm"]` + `ctx["next_name_bm"]` under each theme). Run: `uv run pytest tests/unit/test_display_context.py -q` → Expected: FAIL (`KeyError: 'bm'`).
- [ ] Implement: triples in both tables; unpack `en, ar, bm` at all three sites (`109`, `117`, `139`); add `next_name_bm`, `c["bm"]`, `b["bm"]` + `b["ar"]`; keep `en`/`ar` values byte-identical; leave `pre_note`/banners untouched. Verify: `uv run pytest tests/unit/test_display_context.py -q` → PASS.

### Task 2: ms.json subset table + honesty test

**Files:** `locales/ms.json` (create), `tests/unit/test_locales.py`

**Goal:** The BM pinned copy exists and cannot drift from its owners.

- [ ] Failing test first: parametrize the honesty test over `("en.json", set(OWNERS))` and `("ms.json", {"prayer", "boundary"})` with per-group owner lookup, plus key-parity `assert set(ms["prayer"]) == set(en["prayer"])` and `assert set(ms["boundary"]) == set(en["boundary"])`. Run: `uv run pytest tests/unit/test_locales.py -q` → Expected: FAIL (`ms.json` missing).
- [ ] Implement: write `locales/ms.json` with the locked spellings (keep key order mirroring `en.json`). Verify: `uv run pytest tests/unit/test_locales.py -q` → PASS. Note: no runtime code reads `ms.json` yet; parity is test-only until the loader slice.

### Task 3: Template render + e2e pin

**Files:** `src/muhideen/views/templates/display.html`, `tests/e2e/test_display.py`

**Goal:** Jamaah sees three names; HTML pins them.

- [ ] Failing test: assert `"Subuh"` and `"Zohor"` in the rendered display HTML for a pinned day (cards always render all five, so no `next_prayer` pinning needed); assert bounds contain `Syuruk` + Arabic (`الشروق`) for the intentional `en · ar · bm` expansion; extend `test_state_walkthrough_jumuah_friday` to assert `"Jumaat"` alongside `"Jumuah"`. Run: `uv run pytest tests/e2e/test_display.py -q` → Expected: FAIL (no BM in HTML).
- [ ] Implement: hero/cards/bounds render `· {{ bm }}` (bounds become `en · ar · bm` + time). Verify: `uv run pytest tests/e2e/test_display.py tests/unit/test_display_context.py -q` → PASS.

### Task 4: Changelog + full gate

- [ ] CHANGELOG `[Unreleased] Added`: trilingual prayer labels on display (runtime triples in `display.py`; `locales/ms.json` pinned copy, not yet read at runtime; bounds `en · ar · bm` expansion + `pre_note`/banner English-only explicitly noted; full-UI BM out of scope).
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.

## Self-review

1. Spec coverage: #37 prayer-label scope → Tasks 1–3 (cards + hero + bounds BM; Friday `Jumaat` updated); full i18n (admin strings, switcher, `T()` global, endpoint decision, `pre_note`/banners) explicitly excluded with pointer to `locales/README.md`. Bounds Arabic flagged as intentional expansion needing sign-off.
2. Placeholder scan: no TBD/TODO/appropriate/similar-to-Task — files, tests, commands all named.
3. Type consistency: `(str, str, str)` triples `(en, ar, bm)`; `bm: str` keys mirror `en` keys 1:1; key-parity `ms↔en` required.
4. Momus dry gate: `display.py:17-30,109,117,139,173-174,186` / `display.html:14,20,37,50,71,81` / `en.json`/README/test line references verified on disk this session; task order fixed (triples before `ms.json` so honesty pin can pass); each task has a red→green test + exact `uv run` command. `setup.html`/`admin.js` untouched (no switcher in this slice).
