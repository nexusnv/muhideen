# ARCHITECTURE.md refresh — describe landed work as built — Implementation Plan (issue #47)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: self-review below (plan-review skill unavailable this session), `oracle-review` on the branch diff before PR handoff.

**Goal:** ARCHITECTURE.md matches main — landed display/admin/playlists/groups/backup/audio/calc work described as built, genuinely-future items cross-linked to backlog issues, no other drift introduced.

**Architecture:** Docs-only. Rewrite System Overview (as-built diagram + planned paragraph), retarget Pi-specific claims to the recorded Debian-only v1.0 decision, update persistence/acquire/backend-frontend sections for landed realities (manual pins, groups+media in use, export/restore, tick error stage, retry policy). No code, contract, fixture, or behavior change.

**Tech Stack:** Markdown; repo gates (`ruff format` covers md code fences).

**References:** Issue #47; `ARCHITECTURE.md:7-31,35,39,79,88,110-123,131-134` (drift sites); recorded v1.0 decisions (Debian-only per #46 discussion — Pi claims go; #46 slice owns packaging/install details + its ADR); genuinely-future: #39 (community themes/preview), #42 (OTA rollback), PRD Phase 2 (CEC), rejected-and-recorded: setup token (ADR-0004), per-display schedule fork + video items (#45); `docs/adr/0004-first-boot-setup-gate.md` (reference style).

**Branch:** `docs/architecture-refresh` (cut when approved; base on `main`)

---

## Background the implementer needs

Locked scope: every claimed-unbuilt item verified against main and either struck or issue-linked; Pi→Debian rewording limited to what the recorded #46 decision already settled (generic "Debian machine/kiosk" wording — do NOT write #46's ADR or packaging changes here); no other content change (resist drive-by improvements).

## File Structure

- Modify: `ARCHITECTURE.md` only (+ this plan file).
- Verify-only: `src/` tree listing for claimed components (views/, templates, static/, playlist CRUD, group overrides, backup/adhan adapters); open-issue numbers for cross-links.

---

### Task 1: Rewrite drifted sections + verify no other drift

- [ ] Read the full `ARCHITECTURE.md` once; for each drift site write the minimal accurate replacement:
  - `## System Overview`: backend landed AND frontend landed (display + admin + playlists); diagram FRONTEND block → landed; seam paragraph keeps contract-first history in past tense.
  - Planned paragraph (`:31`): strike display/admin pages, backup/restore UI, audio upload, display groups, extra calc methods (all landed); keep carousel manager? — verify: carousel-dot wiring + Stage playlists landed → strike, keep the per-group flag sentence; keep theme pipeline scoped to what exists (scaffolder/lint/knobs landed; community upload/preview → #39); keep CEC → PRD Phase 2 (no issue — link PRD, do not file); setup token → rejected per ADR-0004 (link).
  - Pi claims (`:35`, `:39`): Debian machine/kiosk wording per recorded #46 decision (no Pi model claims either way).
  - `:79` ticker/scheduler: state/tick fan-out + daily sync with classified retries (short chain + 6h long-pole) in one sentence each.
  - `:88` fixtures sentence: past-tense the "until the frontend lands" clause.
  - `:110` Acquire: add manual pins (admin-entered rows, sync-protected) as a third source.
  - `:131-134` persistence: groups + media tables in use (not "stand ready"); backups cover one-click export too.
- [ ] Verify: `uv run ruff format --check ARCHITECTURE.md` → clean; full `uv run pytest -q` → green (docs-only guard); re-read the whole file once for newly-introduced drift (no other claims touched).
- [ ] Self-check against issue acceptance: every original claimed-unbuilt item either struck or linked; `grep -n -i "planned\|future\|not yet" ARCHITECTURE.md` reviewed line by line.

## Self-review

1. Spec coverage: #47 → Task 1 (all sites + acceptance grep-check). #46 packaging/ADR explicitly out.
2. Placeholder scan: no TBD/TODO; exact lines named.
3. Momus dry gate: references verified on disk this session (full file read); single task, startable, QA = ruff + pytest + grep with expected outputs.
