# Source research: schedule sources (slice 1A-6)

Evidence record behind `adapters/jakim_esolat` and `adapters/calc_mabims`: what the
unofficial JAKIM e-solat endpoint actually serves, the boundary rules extracted from its
published tables, the pinned calculation library and its fit against those tables, the
golden test vectors, and the Hijri library recommendation. Capture date: **2026-09-23**
(payloads + probe verification); re-verification **2026-09-24**. Committed evidence:
`tests/data/jakim_year_sgr01.json`, `tests/data/jakim_year_kdh01.json`.

## Endpoint behavior

Base URL (unofficial, publicly accessible, **not a JAKIM-offered service** — no uptime,
format, or rate-limit guarantees):

```
GET https://www.e-solat.gov.my/index.php?r=esolatApi/takwimsolat&period=<period>&zone=<zone>
```

Probe log — four request/response probes verified live 2026-09-23 (capture date
2026-09-23, all `HTTP 200`, `content-type: application/json`):

| # | Query | periodType echo | Rows | Span (SGR01) |
|---|-------|-----------------|------|--------------|
| 1 | `period=week&zone=SGR01` | `"week"` | 8 | capture day .. +7 days |
| 2 | `period=month&zone=SGR01` | `"month"` | 30 | `01-Sep-2026` → `30-Sep-2026` (whole current month) |
| 3 | `period=week&date=01-03-2026&zone=SGR01` | `"week"` | 8 | identical `periodType` + span to probe 1 — **`date=` is ignored** |
| 4 | `period=year&zone=SGR01` (+ `KDH01`) | `"year"` | 365 | `01-Jan-2026` → `31-Dis-2026` (whole calendar year) |

Re-verification delta (2026-09-23 23:55 → 2026-09-24 00:40, recorded because the endpoint
may change without notice — PRD §6.1):

* Re-probing all four queries — **including the `period=year` control that had succeeded
  hours earlier** — returned `HTTP 404` with a 427-byte non-JSON `text/html` body across
  3 rounds at 240 s backoff and a 7-round × 15-min background sweep: an edge/burst ban,
  not a query failure (the URL itself is unchanged and valid).
* After ~15 min of quiet, a single isolated canary (`period=week`) returned
  `HTTP 200`, 1,847 B JSON, first row `24-Sep-2026`; an immediate 4-probe burst at 5 s
  spacing then returned `404 ×4` again. The ban state re-engages within seconds of a burst.
* Consequence for the client design: short in-client backoff (2 s/4 s/8 s) cannot defeat
  a burst ban inside one request sequence — **keep-cache-on-fail plus the calc fallback
  are the real defenses**; one sync per day keeps us far below any sane burst threshold,
  and the pinned UA/timeout make the single request maximally polite.
* A slow spaced re-verification (10-min quiet, then 15-min spacing) completed at
  2026-09-24 01:41 (+08): all four queries — `week` (00:55:52), `month` (01:10:52),
  `week&date=` (01:25:52), and the `year` control (01:40:53) — still returned `404`
  with the same 427-byte `text/html` block page, interleaved with the 15-min canary
  sweep that also ended all `404` (7/7 rounds, last 01:26). So under continuous
  low-rate probing (≥10-min spacing) there was no recovery within ≥60 min of the last
  burst, even though one earlier ~15-min quiet spell had yielded a `200` — the ban
  threshold is opaque and not time-predictable. The query strings themselves are not
  at fault (all four returned `200` + `application/json` at capture; the URL is
  unchanged), so the client assumes nothing about recovery timing: one sync per day,
  keep-cache on any failure, the calc fallback, and admin calc-only mode remain the
  entire defense (PRD §6.1).

## Payload schema

Root object keys (both committed payloads): `bearing`, `lang`, `periodType`,
`prayerTime`, `serverTime`, `status`, `zone`.
`status` is `"OK!"` (accepted form: any string whose `rstrip("!") == "OK"`), `zone`
echoes the requested zone, `prayerTime` is the row array.

Each row carries exactly these 11 keys: `asr`, `date`, `day`, `dhuha`, `dhuhr`, `fajr`,
`hijri`, `imsak`, `isha`, `maghrib`, `syuruk`.

* Marker times: `HH:MM:SS` — seconds are `"00"` on every marker of every row
  (730 rows × 8 markers = 5,840 values, all `"00"`).
* `date`: `dd-Mmm-yyyy` with exactly the 12 Malay month tokens
  `Jan Feb Mac Apr Mei Jun Jul Ogos Sep Okt Nov Dis`. English `Mar/May/Aug/Dec` never
  appear (0/730 date fields) — a parser accepting only these tokens is sufficient.
* `day`: English weekday name (`"Thursday"`), presentation-only.
* `hijri`: zero-padded string form `1447-07-11` — parsed-and-ignored until an owning
  slice lands FR-1.5 computation (see Hijri section).

## Fetch window

**Choice: `period=year`.** One call returns the entire current calendar year
(`01-Jan-…` → `31-Dis-…`, 365 rows; leap years 366), which makes a single daily fetch
the whole sync — no weekly/monthly prefetch loop is needed. `date=` is ignored, so
arbitrary-window fetches are impossible; `period=week` (today..+7) and `period=month`
(whole current month) exist only as reduced-call alternatives.

FR-1.1 window arithmetic (cache 30 days forward + 7 days past):

* **Forward (30 d):** fully covered while `today + 30 ≤ Dec 31`, i.e. for dates up to
  **Dec 01**. From **Dec 02** the table runs out of rows a little more each day, and no
  rows exist at all past `31-Dis` — those forward days resolve through the FR-1.2
  fallback chain (calc → last-known + banner) until the next January fetch, which the
  02:00 daily sync picks up automatically from Jan 01.
* **Past (7 d):** fully covered while `today − 7 ≥ Jan 01`, i.e. for dates from
  **Jan 08**. During Jan 01–07 the 7-days-past side needs previous-year rows: the sync
  upserts and never deletes, so rows fetched in the previous calendar year persist
  across the year boundary; only a first-ever install during Jan 01–07 starts without
  them and leans on FR-1.2 until the next fetch accumulates history.
* (Correction: an earlier draft of the plan said the shortfall was "Dec 28–31"; the
  arithmetic above is authoritative — the shortfall begins Dec 02.)

## Naming map

Adapter-side map from source spellings to the canonical backend vocabulary
(`core.values.MarkerName`; PRD §6.1 states backend naming is canonical):

| Source key (observed / PRD variant) | Canonical `MarkerName` | Wire value |
|-------------------------------------|------------------------|------------|
| `imsak` | `IMSAK` | `imsak` |
| `fajr`, PRD variant `subuh` | `FAJR` | `fajr` |
| `syuruk` | `SYURUQ` | `syuruq` |
| `dhuha`, PRD variant `duha` | `DHUHA` | `dhuha` |
| `dhuhr`, PRD variant `zohor` | `DHUHR` | `dhuhr` |
| `asr` | `ASR` | `asr` |
| `maghrib` | `MAGHRIB` | `maghrib` |
| `isha`, PRD variant `isyak` | `ISHA` | `isha` |

Note the deliberate spelling divergence on the syuruq row: the source says `syuruk`, the
backend says `syuruq` — the adapter owns the rename. Non-marker row keys (`hijri`,
`day`) and non-row root keys (`bearing`, `lang`, `periodType`, `serverTime`, `status`,
`zone`) are handled explicitly by the parser (ignored, or validated at the payload
level for `status`/`zone`), never silently coerced into markers. Conflicting spellings
of one marker inside a single row are a rejection, not a last-write-wins.

## Boundary derivations

Extracted from both zones' full-2026 tables, 730 rows total:

| Rule | Evidence | Result |
|------|----------|--------|
| `imsak = fajr − 10 min` | exact on 730/730 rows (both zones, every day) | constant, no fit needed |
| `dhuha = syuruk + zone constant` | SGR01 **+25 min** on 365/365 rows; KDH01 **+27 min** on 365/365 rows | per-zone constant observed exactly |

Single latitude-based rule for the zone constant (two-point fit through the observed
constants, since calc only knows the configured coordinates):

```
offset_min = round(22.9851 + 0.6555 × latitude)
```

* SGR01 (lat 3.0738) → `round(25.0000) = 25`, observed 25 — **max residual 0 min**
  (0/365 mismatches).
* KDH01 (lat 6.1248) → `round(27.0003) = 27`, observed 27 — **max residual 0 min**
  (0/365 mismatches).
* Overall **max residual per zone: 0 min** (0/730).

Representative coordinates used by the golden vectors:

| Zone | Latitude | Longitude | Rationale |
|------|----------|-----------|-----------|
| SGR01 | 3.0738 | 101.5167 | Shah Alam (Selangor zone anchor) |
| KDH01 | 6.1248 | 100.3678 | Alor Setar (Kedah zone anchor) |

JAKIM computes each zone at its own unpublished reference point; a well-known anchor
city approximates it, the golden tolerance absorbs the intra-zone spread, and production
computation uses the user's configured lat/lon (FR-1.3) rather than these representatives.
Fit evidence that the points are good approximations: the raw solar-transit offset is
−2.1 min (SGR01) / −2.3 min (KDH01) — whether that is JAKIM's reference point sitting
~0.5° west of the anchors or a +2 min post-zawal dhuhr convention cannot be told apart
from the tables alone (both produce an identical uniform shift); the pinned `dhuhr`
tune of +2 min reproduces JAKIM's published `dhuhr` at both zones to a mean of
−0.16 / −0.31 min either way.

## Ordering

PRD's strict chain `Imsak < Fajr < Syuruq < Dhuha < Dhuhr < Asr < Maghrib < Isha`
verified programmatically over all 730 rows: **0 ordering violations, 0 equal-adjacent
pairs, 0 date-parse failures**. The chain becomes the shared domain validator
(`domain.ordering.ensure_ordered`, Design Decision 4) applied to parsed JAKIM rows and
to every calc-produced day alike.

## Calc library spike

Four candidates evaluated against the four criteria: (1) strict-pyright clean without
`type: ignore` (`CONTRIBUTING.md:51`), (2) MABIMS params expressible, (3) golden fit
within tolerance, (4) dependency weight (PRD `:146` lean toolchain, `:200` ≤150 MB RSS,
Raspberry Pi / Linux ARM target).

| Candidate | (1) strict typing | (2) params | (3) golden fit | (4) weight | Verdict |
|-----------|-------------------|------------|----------------|------------|---------|
| `adhan` 0.1.1 (LGPLv3, 2015) | FAIL — strict errors without `type: ignore` | FAIL — hemisphere bug: `compute_zuhr_utc` uses `abs(longitude)`, ~13.5 h off west of Greenwich (would break FR-1.3 international) | — | unmaintained since 2015 | **rejected** |
| `islamic-times` 3.1.0 (MIT) | pass | pass (custom angles) | FAIL — best fit at 17.75/18.25 pooled max 4 (KDH01 dhuhr mean −2.15, max 4; no dhuhr-tune knob) > 3 | FAIL — pulls `numpy`+`pytz`+`timezonefinder`, 23.8 MB sdist, no Linux ARM wheels | **rejected** (kept as independent cross-check, see below) |
| vendored PrayTimes.org 2.3.2 (LGPLv3, 432-line single file, stdlib-only) | FAIL raw (7 strict errors); pass only after we annotate the vendored copy | pass — full `adjust()` incl. `dhuhr` tune; built-in `imsak: '10 min'` matches JAKIM | pass — pooled max 3, MAE 0.935 | zero deps, but LGPLv3 copyright block must ship | **backup — rejected on license + vendoring burden** |
| **`adhanpy` 1.0.5 (MIT)** | **pass — 0 errors** (strict probe of the full usage API inside `src/`, validated with a negative control so the checker demonstrably sees its types; no `py.typed`, but pyright reads installed library source) | **pass** — `CalculationParameters(fajr_angle=…, isha_angle=…)`, `PrayerAdjustments(dhuhr=2)` per-marker minute tunes, `Madhab.SHAFI` = Asr shadow factor 1 | **pass — pooled max 3, MAE 0.938** | **pass — zero runtime deps, 17.2 kB wheel, MIT, pure Python; port of batoulapps/adhan-java (the reference adhan implementation)** | **PINNED** |

**Library: `adhanpy` 1.0.5 — MIT — PyPI — zero runtime dependencies** (`uv add
adhanpy==1.0.5`). Risks: single maintainer, last release 2023-01-28, classifiers stop at
Python 3.12 (verified working on 3.13). Mitigations: the golden tests pin its outputs
so any behavioural drift or a future swap is caught at the `CalcEngine` seam, which the
architecture already treats as replaceable (Design Decision 8 — no ADR needed).

Pinned MABIMS parameters (grid fit over fajr ∈ {17.5, 17.75, 18.0} ×
isha ∈ {18.0, 18.25, 18.5} × dhuhr tune ∈ {0, 2}, pooled over both zones × 365 days ×
8 markers):

* `fajr_angle = 17.75°`, `isha_angle = 18.25°`
* Asr: `Madhab.SHAFI` → shadow factor **1** (Standard/MABIMS)
* `dhuhr` tune **+2 min**; maghrib offset **0 min** (sunset); asr tune 0
* `imsak = fajr − 10 min` (derived), `dhuha = syuruk + round(22.9851 + 0.6555 × lat)`
  (derived)
* **`GOLDEN_TOLERANCE_MIN = 3`** (target 3 min, hard ceiling 5 — met at the target:
  pooled max|e| = **3**, MAE = **0.938** over 5,840 marker values)

Per-marker error tables at the pinned parameters (sign: calc − JAKIM, minutes;
columns: mean / MAE / max|e|, each over 365 days):

| Zone | imsak | fajr | syuruk | dhuha | dhuhr | asr | maghrib | isha |
|------|-------|------|--------|-------|-------|-----|---------|------|
| SGR01 | −0.17 / 0.53 / 2 | −0.17 / 0.53 / 2 | +1.99 / 1.99 / 3 | +1.99 / 1.99 / 3 | −0.16 / 0.16 / 1 | −1.08 / 1.08 / 3 | −1.28 / 1.28 / 3 | −0.28 / 0.59 / 2 |
| KDH01 | −0.20 / 0.27 / 2 | −0.20 / 0.27 / 2 | +1.67 / 1.67 / 3 | +1.67 / 1.67 / 3 | −0.31 / 0.32 / 1 | −1.22 / 1.22 / 2 | −1.25 / 1.25 / 2 | −0.19 / 0.20 / 1 |

Contrast at **nominal MABIMS 20°/18°, no tune** (why the angles are fitted, not
nominal): `fajr` mean **−9.63 / −9.65**, max **12 / 11**; `isha` −1.32 / −1.25 (already
within tolerance at nominal 18°); `dhuhr` −2.14 / −2.31. The fajr anomaly is
JAKIM-side, not a library bug — three independent checks agree:

1. `adhanpy` (this pin) and `islamic-times` (independent astronomy core) both produce
   fajr mean ≈ −9.6 min at nominal 20°;
2. a textbook hour-angle calculation for SGR01 on 01-Jan-2026 puts −20° twilight
   ≈ 84 min before sunrise, while JAKIM's table shows fajr only 71 min before sunrise
   (06:06 vs 07:17) ⇒ ≈ 17.2° effective twilight;
3. the grid fit lands at 17.75° with MAE ≈ 0.5.

Conclusion: JAKIM's published *subuh* corresponds to ≈17.75° twilight (isha, in
contrast, matches its nominal 18°); fitting to the golden tables — which are the
ground truth for Malaysian displays — is the plan-mandated behaviour ("MABIMS params
fitted to the golden rows"). The remaining ≤3 min residuals (syuruk/dhuha +2,
asr −1.1, maghrib −1.3) are shared, signed JAKIM-side conventions (rise/set angle
handling and rounding), identical in direction across two independent implementations,
and absorbed by `GOLDEN_TOLERANCE_MIN = 3`.

## Golden vectors

Six test vectors for the golden test, extracted from the committed payloads (provenance:
`tests/data/jakim_year_{zone}.json`, captured 2026-09-23 with the project toolchain,
each `HTTP 200` / `status "OK!"` / 365 rows). Expected marker values (all `"HH:MM:00"`):

| File | `date` | imsak | fajr | syuruk | dhuha | dhuhr | asr | maghrib | isha |
|------|--------|-------|------|--------|-------|-------|-----|---------|------|
| `jakim_year_sgr01.json` | `01-Jan-2026` | 05:56 | 06:06 | 07:17 | 07:42 | 13:19 | 16:42 | 19:17 | 20:31 |
| `jakim_year_sgr01.json` | `01-Jun-2026` | 05:39 | 05:49 | 07:01 | 07:26 | 13:14 | 16:39 | 19:22 | 20:37 |
| `jakim_year_sgr01.json` | `23-Sep-2026` | 05:45 | 05:55 | 07:01 | 07:26 | 13:09 | 16:14 | 19:11 | 20:20 |
| `jakim_year_kdh01.json` | `01-Jan-2026` | 06:06 | 06:16 | 07:27 | 07:54 | 13:24 | 16:45 | 19:16 | 20:31 |
| `jakim_year_kdh01.json` | `01-Jun-2026` | 05:38 | 05:48 | 07:01 | 07:28 | 13:19 | 16:43 | 19:31 | 20:47 |
| `jakim_year_kdh01.json` | `01-Dis-2026` | 05:51 | 06:01 | 07:12 | 07:39 | 13:10 | 16:31 | 19:03 | 20:17 |

Observed deviations of the pinned engine at those six vectors (calc − JAKIM, minutes;
every cell within `GOLDEN_TOLERANCE_MIN = 3`, in fact within ±2):

| Vector | imsak | fajr | syuruk | dhuha | dhuhr | asr | maghrib | isha |
|--------|-------|------|--------|-------|-------|-----|---------|------|
| SGR01 01-Jan-2026 | −1 | −1 | +2 | +2 | 0 | 0 | −1 | +1 |
| SGR01 01-Jun-2026 | 0 | 0 | +2 | +2 | 0 | −2 | −2 | −1 |
| SGR01 23-Sep-2026 | 0 | 0 | +2 | +2 | −1 | −2 | −1 | −1 |
| KDH01 01-Jan-2026 | −1 | −1 | +2 | +2 | 0 | −1 | −1 | 0 |
| KDH01 01-Jun-2026 | 0 | 0 | +2 | +2 | −1 | −1 | −1 | 0 |
| KDH01 01-Dis-2026 | 0 | 0 | +2 | +2 | −1 | −1 | −2 | −1 |

The exact `imsak = fajr − 10 min` rule is asserted separately from the tolerance
(it is an identity of the engine, not an approximation), and every golden day must
pass `ensure_ordered` on its way out of `compute_day`.

## Hijri

Recommendation for FR-1.5's base calendar (discharges the 1A-2 deferral at the
*choice* level — `2026-09-22-1a-2-domain-pure-logic.md` deferred it here):

* **`hijridate` 2.3.0** — MIT (PyPI `license-expression: MIT`), **zero runtime
  dependencies**, classifier `Typing :: Typed`, conversion range **1343–1500 AH**,
  actively maintained as the successor to the deprecated `hijri-converter`.
* Alternatives considered: `hijri-converter` (deprecated fork-upstream of the same
  code), `ummalqurachronicle`-style hardcoded tables (narrower coverage, weaker
  maintenance), and reimplementing tabular arithmetic in-house (out of scope — do not
  reinvent).

Computation itself stays **out of scope** for this slice: no slice row owns FR-1.5's
computation or a contract field for it yet (PHASES follow-up: fold into 1A-7's settings
surface or a 1A-6b). Until then the payload's `hijri` rows are parsed-and-ignored, and
the `settings.hijri_offset` −2..+2 guard already lives in `core.values.Settings`.
