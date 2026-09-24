# Muhideen Domain Glossary

Glossary only. No implementation, no file paths, no tooling.

## Core Concepts

### Muhideen
An offline-first masjid display system. Given the same wall-clock time, the same stored schedule snapshot, and the same settings, it shows the same prayer state. Time-dependent by nature; determinism is scoped to a fixed clock + snapshot.

### Masjid / Surau
A mosque or prayer hall where the system is installed. The word `masjid` in settings and UI means the place, not the product.

### Jama'ah
A worshipper viewing the public display. Needs legibility at distance, no clutter, no distraction during prayer.

### Admin
A non-technical committee member configuring the system over LAN from a phone or PC. Never edits config files.

### Integrator
A technical volunteer installing hardware, onboarding displays, or contributing themes and calculation methods.

## Prayer Vocabulary

### Prayer Time Marker
The five daily prayers: Fajr, Dhuhr, Asr, Maghrib, Isha. Jumuah replaces Dhuhr on Fridays. Only Prayer Time Markers have Adhan, Iqamah, and dimming, and only they drive the Prayer State machine.

### Boundary Time Marker
Imsak, Syuruq, Dhuha: informational markers. Never Adhan, never Iqamah, never auto-dim, never a non-NORMAL state. Optional countdown when the installation opts in. Rendered below the Prayer Time Marker level, never on prayer cards or the hero.

### Adhan
The call announcing a prayer time has arrived. Triggers a full-screen overlay of fixed duration.

### Iqamah
The start of congregational prayer. Either a delay in minutes after Adhan or a fixed clock time, configured per prayer.

### Syuruq / Sunrise
Sunrise marker — a Boundary Time Marker: displayed, never Adhan/Iqamah/dim/state.

### Jumuah
Friday congregational prayer. Replaces Dhuhr on Fridays with its own Iqamah rule and dim duration.

### Hijri Date
Islamic calendar date shown alongside the Gregorian date, with a manual regional offset of -2 to +2 days.

### Zone
A JAKIM prayer zone code (e.g. `SGR01`). Exactly one zone is configured per installation.

### Calculation Method
A prayer-time algorithm (MABIMS, MWL, ISNA, Egyptian) plus latitude, longitude, timezone, and Asr juristic rule. Used as fallback offline and primary outside Malaysia.

## System Vocabulary

### Prayer State
Exactly one of `NORMAL`, `PRE_ADHAN`, `ADHAN`, `IQAMAH_COUNTDOWN`, `SALAH_DIM`. Computed by the backend; the display never computes it. Driven only by Prayer Time Markers; Boundary Time Markers always render under `NORMAL`.

### Fallback Chain
Ordered schedule resolution: cached schedule, then on-device calculation, then last-known day with warning. The display always renders the resolved value plus a freshness flag.

### Stale
A schedule older than 48 hours or produced by a degraded fallback step. Shown as a banner, never silent.

### TIME UNSYNCED
The device clock cannot be trusted: NTP reported unsynchronised at boot, or a wall-clock step greater than 5 seconds against the monotonic clock was detected. Shown as a banner, never silent. A boot-time unsync clears when NTP confirms sync again; a detected step additionally latches the warning for five minutes, and the latch clears only after it ages out **and** the probe confirms sync — a probe reading "still synced" cannot clear it early. Surfaces contract-side as `time_synced: false` on next-event and SSE `state`.

### Display
A Chromium kiosk screen showing the public view. Identified by a stable registration ID, not an IP address.

### Display Group
A named set of displays (e.g. Main Hall, Lobby) sharing theme, carousel, and dim settings.

### Theme
A sandboxed visual skin for the public view. Receives read-only prayer data; cannot reach admin functions.

### Carousel
A rotating set of image posters (announcements, reminders, QR codes). Always hidden around prayer states.

### Dim
The solemn display mode during prayer: darkened or black with a minimal clock only.

### Contract
The versioned JSON shape exchanged between backend and frontend (prayer day, next event, event stream, heartbeat). The sole coupling between contributor tracks.

### Fixtures
Checked-in example contract payloads. Frontend builds against fixtures without a live backend.
