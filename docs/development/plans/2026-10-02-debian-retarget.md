# v1.0 Debian retarget: drop Pi, document kiosk, QA checklists — Implementation Plan (issue #46)

> **For workers:** Execute task-by-task via `tdd` + `verification-before-completion`. Review gate: self-review below (plan-review skill unavailable this session), `oracle-review` on the branch diff before PR handoff.

**Goal:** v1.0 targets Debian machines + kiosk Chromium only: Pi-specific claims removed repo-wide, the Debian install/kiosk story documented with a human QA checklist, and the decision recorded — leaving only the physical QA pass and the v1.0.0 tag cut as explicit human gates.

**Architecture:** Docs + packaging-text + checklists. No behavior change: PRD platform/tiers/budgets/installer/vendor statements retargeted; deployment guide rewritten for Debian + kiosk launch (incl. adhan autoplay-policy flag); install.sh messaging de-Pi'd (flags/behavior unchanged); vendor script verified working (outputs NOT committed); QA checklist doc created for the human pass; ADR-0005 records Debian-only v1.0. Out of scope for this slice (explicit human gates, not to be taken): cutting the v1.0.0 tag, the physical install/legibility/QR walkthrough.

**Tech Stack:** Markdown, shell (`install.sh`, `tools/build_vendor.sh`), systemd unit text, pytest (device tests guard scripts).

**References:** Issue #46 (recorded decision: drop Pi, Debian + kiosk Chromium; any Debian machine + TV browser or direct display); `docs/deployment.md:9-26,47` (Pi tiers/armv7l/preflight); `PRD.md:10` (Target Platforms), `:144-147` (stack/Pi viability/installer), `:200-203` (§5.1 budgets/tiers/boot), `:325` (all-in-one gate); `install.sh` (preflight messaging); `tools/build_vendor.sh:45-48` (cross wheels); `packaging/muhideen.service:8-14` (Restart=always, no watchdog); `tests/device/` (script guards); `docs/adr/0004-first-boot-setup-gate.md` (ADR format).

**Branch:** `docs/debian-retarget` (cut when approved; base on `main`)

---

## Background the implementer needs

Locked decisions (user, this session): v1.0 supports Debian-based Linux + kiosk-mode Chromium only (any machine that runs Debian; TV with browser or directly-connected display; Debian desktop UI required for the kiosk side). Pi models/tiers, armv7l, Pi OS references go. Kiosk = documented Chromium launch (`--kiosk` + `--autoplay-policy=no-user-gesture-required` for adhan audio — verify the adhan `play().catch()` context first via `grep -n "autoplay\|play()" src/muhideen/static/app.js`), not a new unit (no display-manager integration in v1.0 scope). Service resilience = existing `Restart=always` + boot enablement, documented (no watchdog: app has no sd_notify — do NOT add one).

## File Structure

- Modify: `PRD.md` (platforms row, §5.1 tiers/budgets/boot, installer/vendor/armv7l statements — grep `Pi \|Pi4\|armv7\|Pi OS\|Raspberry` first for the full list; leave backend-selection history like `:147` unless it makes Pi promises — minimal diff, no rewording for style).
- Modify: `docs/deployment.md` (Hardware section → Debian tiers; vendor/armv7l paragraphs → Debian x86_64/aarch64 reality; preflight Pi mention → Debian wording; kiosk launch + power-loss/clock-fault behavior definitions).
- Modify: `install.sh` — messaging only (preflight Pi reference → Debian wording; verify no Pi-only logic branches exist — `grep -n -i "pi\|arm\|arch" install.sh` first; flags/behavior byte-identical).
- Modify: `tools/build_vendor.sh` — comments only (Pi OS → Debian ARM64/x86_64), unless a dry run surfaces a real bug (then fix + test, reported).
- Create: `docs/qa/v1.0-debian-checklist.md` — human pass checklist (clean-room install log, legibility notes, QR scans, state walkthrough incl. ADHAN audio + error slates, power-loss + clock-fault behavior), each item sign-off boxes.
- Create: `docs/adr/0005-debian-only-v1.md` — Status Accepted, Date 2026-10-02 (Pi deferred to a future version; revisit triggers).
- Verify-only: `vendor/` stays uncommitted (release artifact, not git content); `dist/` untouched; no code changes (prove with `git diff --stat`: md/sh-adjacent only — install.sh/build_vendor.sh messaging/comments only).
- Explicitly NOT in this slice: `git tag v1.0.0`, the physical QA run, kiosk systemd unit, watchdog.

---

### Task 1: PRD + deployment + install/vendor messaging retarget

- [ ] Grep the full Pi-surface first: `grep -rn -i "raspberry\|pi 4\|pi 3\|pi4\|armv7\|pi os\|raspi" PRD.md docs/deployment.md install.sh tools/build_vendor.sh packaging/ | grep -v Binary`. For each hit decide: retarget (platform/tiers/install claims) or keep (historical backend-selection notes like PRD:147 — keep unless promising Pi support).
- [ ] Edit: PRD platform/tiers/boot/installer/vendor lines → Debian wording (keep budgets honest: backend RSS/CPU numbers are platform-agnostic; Chromium-dominated boot/render notes stay, Pi-model-specific timings go generic); deployment Hardware → Debian tiers + kiosk launch command + autoplay-policy flag + power-loss/clock-fault definitions; install.sh messaging (flags identical); build_vendor.sh comments.
- [ ] Verify: repeat grep → only intentional historical hits remain (list them in the commit message); `uv run bash -n install.sh tools/build_vendor.sh` (syntax); `tests/device -q` → green; `uv run ruff format --check` on touched md.

### Task 2: QA checklist + ADR-0005 + vendor-script verification

- [ ] Write `docs/qa/v1.0-debian-checklist.md` (environment block: machine/distro/Chromium/TV; install-log capture; legibility 10m notes; QR scans; state walkthrough incl. PRE_ADHAN/ADHAN+audio/IQAMAH/DIM/Jumuah; error slates via 404/503 induction; power-loss recovery; clock-fault TIME UNSYNCED; sign-off per item + overall verdict line).
- [ ] Write `docs/adr/0005-debian-only-v1.md` (0004 format; Context: Pi holes listed in #46; Decision: Debian-only v1.0, kiosk Chromium; Rationale: volunteered testing surface, wheel availability x86_64/aarch64, no Pi hardware to validate on; Consequences: Pi support explicitly future, armv7l unsupported stays, revisit triggers).
- [ ] Vendor script: run `./tools/build_vendor.sh --help` (or dry-run flag if supported — read the script header first); if it needs network/large downloads, run it only if fast (<5 min), else verify by reading + `bash -n` and note the gap honestly. NEVER commit `vendor/` or `dist/` outputs (`git status` must show none).
- [ ] Verify: `uv run pytest tests/device -q` → green; full `uv run pytest -q` → green (docs-only guard); `git status --porcelain` shows no binaries/bundles.

### Task 3: Changelog + full gate + human-gates handoff note

- [ ] CHANGELOG `[Unreleased] Changed`: Debian-only v1.0 retarget (Pi dropped to future; kiosk Chromium documented) + QA checklist pointer (no dev-plan filename ref).
- [ ] Gate: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run lint-imports` → green; `uv run pytest -q` → green, coverage ≥95.
- [ ] Write the handoff summary for the user: two remaining human gates (physical Debian QA pass → sign checklist; `git tag v1.0.0` + close #46) — do NOT perform either.

## Self-review

1. Spec coverage: #46 repo automatables → Tasks 1–2 (docs/scripts/checklist/ADR); tag + physical QA explicitly handed off, not taken. Vendor outputs not committed; kiosk unit/watchdog excluded with reasons.
2. Placeholder scan: no TBD/TODO; grep-first steps named.
3. Momus dry gate: references verified on disk this session (deployment/PRD lines, service unit, device tests, ADR format); order fixed (messaging → checklist/ADR → changelog); QA = grep/bash-n/pytest with expected outputs.
