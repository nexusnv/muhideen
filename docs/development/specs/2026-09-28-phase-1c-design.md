# Phase 1C Design — Polish (Display Reskin, Main Stage, Admin)

Status: brainstormed 2026-09-28 (user decisions locked below). Implementation via 1C-1…1C-4 slices in `PHASES_AND_SLICES.md`.

Sources of truth: `PRD.md`, `ARCHITECTURE.md`, `docs/api-contract.md` + `api/fixtures/`, `CONTEXT.md` (Main Stage, Playlist, Countdown), user-approved display example + admin example (aesthetic basis only — both use forbidden Tailwind CDN and must be converted).

## Locked decisions

1. **Two tiers only.** Global settings + per-display allowlist (theme choice, dim durations). No cascade engine, no display-grouping timeline. The research report's 4-tier model is not planned.
2. **Main Stage model.** Hero area has exactly one occupier: scheduled Playlist, Adhan/Iqamah Countdown, or Clock default. Countdowns always override. Overlaps resolve by most-recent activation (i.e. the in-window playlist whose window opened most recently (window start; input order breaks ties)).
3. **Countdown settings.** Pre-adhan takeover configurable globally (default 5, range 0–90) with per-prayer overrides. Post-adhan side unchanged (overlay duration, then iqamah countdown).
4. **Theme split.** Themes change how facts look, never which facts show. App-wide: all prayer data, rules, schedules, playlists, countdown timings, hijri, dim defaults, boundary opt-in. Theme knobs: palette, fonts, countdown style, clock format, Hijri form, boundary strip, density. Per-display: theme choice, dim durations.
5. **Vanilla only.** Tailwind Play CDN and Google Fonts links are forbidden (offline-first, PRD §4.2). Reskin in hand-written CSS; vendor Outfit + JetBrains Mono woff2.
6. **Per-card iqamah in scope.** Resolved server-side for all five prayers (today: next only).
7. **Hijri long format.** Transliterated month names ("15 Rabi' al-Awwal 1448") for display; wire value stays `YYYY-MM-DD`.
8. **Phase 2/3 retired.** Carousel display-cycling subsumed by Stage; upload/storage absorbed by 1C-3. Leftovers sit in the unscheduled backlog; PDF/social/mobile/victoria items are v2 (unplanned).
9. **Admin nav (presentation regroup, same fields/endpoints).** Profile (name, zone) · Time & Date Marker (method, lat/lon, calc-only, offsets, hijri, boundary countdown, pre-adhan windows, iqamah rules, adhan duration) · Display (theme knobs) · Playlists (list, editor, toggles) · System (QR, version; password change + backup/restore stay backlog).
10. **Setup token.** First-boot gate (open setup iff no admin exists, 409 after) is the one-time mechanism; no token endpoint.

## Display reskin (1C-1)

User example converted 1:1 minus the dev-only mode bar. Countdown view covers PRE_ADHAN and long-idle NORMAL (same component, longer timer); prayer view covers IQAMAH_COUNTDOWN. ADHAN overlay, SALAH_DIM, footer regions keep 1B semantics with the new aesthetic. Playwright screenshot at 1080p is the acceptance check (no pixel-match requirement, but density + hierarchy must read at 10m).

## Stage engine (1C-2)

Occupancy evaluated per tick from {active playlists in-window, countdown windows, default}. Playlist schedule = clock window and/or marker-anchored start/stop + cycle policy (indefinite or N) + per-item durations. Settings: pre-adhan global + per-prayer map. Pure domain function over (now, schedule, settings, playlists) — same determinism rule as prayer state.

## Playlist backend + editor (1C-3)

Image store (Pillow, EXIF strip, 5MB/50 caps — carried from retired 2A), item ordering, active toggle. Admin editor: schedule, cycling, items, preview of computed occupancy ("on Stage now / next at …").

## Admin restyle (1C-4)

Same routes/fields/APIs, new grouping (§9 nav), light visual system (small headings, consistent buttons, horizontal nav, multi-column forms). Per-display theme + dim overrides plumbed through existing display registry.

## Explicit non-goals

Manual timetable entry/CSV, emergency overlay, audio chimes, backup/restore UI, extra themes, theme zip pipeline, portrait layouts, CEC, thin client, Go appliance, PDF/social/mobile/widgets, video. First exception: none — additions need a new decision.
