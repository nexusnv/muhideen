---
name: muhideen-source-research
description: Research a new Muhideen schedule source, calculation method, or Hijri variant to gold standard and emit a docs/development/research report. Use when the user asks to research a prayer-time source, add a calculation method (MABIMS/MWL/ISNA/Egyptian), survey JAKIM e-solat, pin Hijri calendar behavior, or prepare a source/method plan. Produces a primary-source, citation-backed report that directly seeds a slice plan.
---

# Muhideen Source Research Skill

> **Gold standard:** primary-source discipline — every angle, endpoint, parameter, and example traces to its publisher (JAKIM, MABIMS, method author, or validator source code). Read this entire file before fetching a single URL.

## When to Use

- User says `research MABIMS parameters`, `survey JAKIM e-solat`, `add ISNA method`, `pin Hijri Umm-al-Qura behavior`, `how should sync work for zone X`
- User points at `docs/development/PHASES_AND_SLICES.md` slice 1A-6 and asks what to build
- User wants a `docs/development/research/*.md` report for a new schedule source
- User wants a pre-implementation survey that later becomes a `docs/development/plans/*.md`

If the source/method is not named, discover it from the slice plan or ask.

## Hard Rules (Non-Negotiable)

1. **Primary sources only.** Every claim traces to the publisher, the Registration Authority, the method author's paper/code, or a validator's source. A blog post *about* MABIMS is not a source; the MABIMS resolution text or JAKIM technical note is.
2. **No source-code or test modifications.** Research only. You may read `src/muhideen/` and `tests/`, never edit them. Record repo state (`git rev-parse --short HEAD` + branch) in the report header.
3. **Cite or it did not happen.** Every angle, endpoint, field, length, and example carries a URL or a codebase path. Secondary sources labeled as such.
4. **Save to `docs/development/research/YYYY-MM-DD-{kebab}.md`**. Never elsewhere unless explicitly overridden.
5. **Respect `docs/development/AGENTS.md`:** ephemeral, unshipped, unreferenced by code or shipped docs.
6. **Ground in Muhideen conventions.** Read `PRD.md` §3.1/§6.1, `ARCHITECTURE.md`, `CONTEXT.md`, `docs/api-contract.md`, and `docs/adr/0003-sqlite-behind-ports.md` before proposing anything.
7. **Inventory every published form.** A schedule source publishes across its full surface (today/week/month/year endpoints, PDF takwim, CSV dumps, HTML tables) — enumerate each with evidence and a disposition: RECOGNIZE (v1 sync client handles it), DEFER (documented future work), or REJECT (never valid input, with rationale). Silence is not a decision.

## Phase 0 — Discovery (5 minutes)

1. Resolve the source/method name (JAKIM zone feed, MABIMS, MWL, ISNA, Egyptian, Umm-al-Qura Hijri, tabular Hijri).
2. Record repo state: branch, commit, date (UTC `YYYY-MM-DD`).
3. Note which slice this feeds (`docs/development/PHASES_AND_SLICES.md` 1A-6 or 1A-2).

## Phase 1 — Research Protocol

### A) Authority & lineage
- Publisher pages for every relevant edition/resolution. Record edition, date, status, what it supersedes, and lifecycle.
- For calculation methods: the defining angles (Fajr/Isha twilight, Maghrib offset, Asr juristic factor), midnight rule, and high-latitude rule — each with its source line.
- For Hijri: calendar arithmetic (tabular vs astronomical), month-length rule, and known ±1-day variance causes.
- Lineage table (≥3 rows where history exists).

### B) Ecosystem validators (real-world evidence)
Fetch and quote at least 3: prayer-calc ports used in the wild, `hijri-converter` or equivalents, existing JAKIM scrapers/sync scripts. Extract exact parameters or parsing logic into a comparison table.

### C) Publication-surface survey (mandatory)
Catalogue every representation the source actually publishes: API periods, field names, time formats (`HH:MM` vs 24h vs 12h), timezone attachment, date rollover behavior, error payloads, rate limits, observed outages or silent schema changes. Per form: name | example | attested where | prevalence | v1 disposition + mechanism.

### D) Shipped Muhideen precedent
Anchor to verbatim code: `src/muhideen/domain/` fallback chain, `src/muhideen/adapters/` ports, `api/fixtures/` shapes, `src/muhideen/engine/` resolution. Cite paths, never paraphrase from memory.

## Phase 2 — Report Structure

```markdown
# {Source/Method} Research — muhideen

**Date:** YYYY-MM-DD
**Scope:** Primary-source survey of {X} to ground slice {1A-N}. No code modified.
**Evidence basis:** {every primary source fetched} + {ecosystem libs} + {shipped precedent}. Repo state: {branch} @ {sha}.

## Executive Summary
Fit assessment + 5 key findings shaping the design.

## 1. Target User
| Persona | Why they need it | Context |

## 2. Shape of Schedule (published surface)
### 2.1 Publication inventory (MANDATORY — every form + RECOGNIZE/DEFER/REJECT)
### 2.2 Wild variants (endpoint quirks, DST edges, leap days, outage payloads)
### 2.3 What is NOT this source (sibling feeds, unofficial mirrors)

## 3. Parameters / Notation
Angles, offsets, juristic factors, or calendar arithmetic as frozen dataclass fields.

## 4. Sync / Computation Strategy
Fetch cadence, retry/backoff, validation gates (HH:MM sanity, Fajr< Syuruq< Dhuhr< Asr< Maghrib< Isha ordering), cache windows, stale policy.

## 5. Provenance
Authority, spec name, version, URL, lifecycle, publication year — one entry per rule/data file the implementation will add.

## 6. Contract Impact
New/changed `docs/api-contract.md` fields, fixture additions, `source` flag values.

## 7. Validation Levels
`jakim` vs `calc` vs `manual` — what each guarantees, what each degrades.

## 8. Edge Cases (≥12 rows: case, input, expected source/state/flag, why)

## 9. Freshness Map
| Situation | source | stale | Banner? |

## 10. File Layout
Exact `Create:` / `Modify:` paths under `src/muhideen/`, `tests/`, `api/fixtures/`.

## 11. Test Strategy
Unit (pinned clock), contract (fixture parity), integration (seeded DB + stub feed), property (ordering invariants).

## 12. Open Decisions (each: recommendation + rationale)

## 13. URL Reference (fetched YYYY-MM-DD, kind per row)

## 14. Evidence Completion (checkboxes earned, not assumed)
```

## Phase 3 — Quality Bar

- 8+ primary URLs fetched and dated; every table row cited.
- Publication inventory complete: no silently unhandled form.
- ≥12 edge cases with expected `source`/`stale`/state.
- Code blocks (parameters dataclass, validation snippet) pyright-clean in intent: frozen domain, no `output_format`-style presentation leaks into rules.
- File layout names real paths; open decisions all carry recommendations.

## What Not to Do

- Do not invent endpoints, angles, or URLs.
- Do not propose presentation (themes, banners copy) — resolution data only.
- Do not gate recognition on authority features; a fetched-but-unvalidated day is `calc`/`stale`, never silently dropped.
- Do not touch `src/`, `tests/`, or configs.
