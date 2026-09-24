-- Slice 1A-5: full PRD §6.2 SQLite schema (v0.1).
-- Idempotent (ADR-0003): safe to re-run after a partial failure.

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
  -- keys: masjid_name, zone_code|lat,lon,method, hijri_offset(-2..2),
  -- adhan_duration_s, dim_minutes_default, dim_minutes_jumuah,
  -- boundary_countdown(0|1), calc_only(0|1), carousel_enabled, theme_default,
  -- qr_visible_default
);

CREATE TABLE IF NOT EXISTS prayer_times (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  date_gregorian DATE NOT NULL,
  zone_code TEXT NOT NULL DEFAULT 'DEFAULT',
  imsak TEXT NOT NULL, fajr TEXT NOT NULL, syuruq TEXT NOT NULL,
  dhuha TEXT NOT NULL, dhuhr TEXT NOT NULL, asr TEXT NOT NULL,
  maghrib TEXT NOT NULL, isha TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'jakim', -- jakim|calc|manual
  fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  UNIQUE(date_gregorian, zone_code)
);
CREATE INDEX IF NOT EXISTS idx_prayer_zone_date
  ON prayer_times(zone_code, date_gregorian);

CREATE TABLE IF NOT EXISTS iqamah_rules (
  prayer TEXT PRIMARY KEY, -- fajr,dhuhr,asr,maghrib,isha,jumuah (Boundary Time Markers excluded)
  mode TEXT NOT NULL DEFAULT 'delay', -- delay|fixed
  delay_minutes INTEGER DEFAULT 10,
  fixed_time TEXT -- HH:MM when mode=fixed
);

CREATE TABLE IF NOT EXISTS media (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  caption TEXT DEFAULT '',
  sort_order INTEGER DEFAULT 0,
  enabled INTEGER DEFAULT 1,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS display_groups (
  name TEXT PRIMARY KEY,
  theme TEXT DEFAULT 'classic-green',
  carousel_enabled INTEGER DEFAULT 1,
  dim_minutes_override INTEGER -- NULL = use settings
);

CREATE TABLE IF NOT EXISTS displays (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  ip_address TEXT,
  group_name TEXT DEFAULT 'Default' REFERENCES display_groups(name),
  current_theme TEXT DEFAULT 'classic-green',
  last_seen DATETIME
);

CREATE TABLE IF NOT EXISTS users (
  username TEXT PRIMARY KEY,
  password_hash TEXT NOT NULL, -- argon2id
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Seeds (idempotent): non-identity settings defaults only; identity
-- (masjid_name, zone_code, lat, lon) comes from the setup wizard (FR-6.2).
INSERT OR IGNORE INTO settings (key, value) VALUES
  ('hijri_offset', '0'),
  ('adhan_duration_s', '180'),
  ('dim_minutes_default', '20'),
  ('dim_minutes_jumuah', '45'),
  ('boundary_countdown', '0'),
  ('method', 'MABIMS');

-- FR-1.4 defaults; Boundary Time Markers have no iqamah rule (FR-1.7).
INSERT OR IGNORE INTO iqamah_rules (prayer, mode, delay_minutes, fixed_time) VALUES
  ('fajr', 'delay', 15, NULL),
  ('dhuhr', 'delay', 10, NULL),
  ('asr', 'delay', 10, NULL),
  ('maghrib', 'delay', 10, NULL),
  ('isha', 'delay', 15, NULL),
  ('jumuah', 'delay', 10, NULL);

-- FK target of displays.group_name.
INSERT OR IGNORE INTO display_groups (name, theme)
  VALUES ('Default', 'classic-green');
