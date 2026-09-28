-- Theme knobs: global defaults plus per-display overrides.
-- Idempotent: safe to re-run after a partial failure.

CREATE TABLE IF NOT EXISTS display_settings (
  display_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value TEXT NOT NULL,
  PRIMARY KEY (display_id, key),
  -- Allowlist (enforced in the repo, not the schema): the seven
  -- theme.* knob keys plus dim_minutes_override only.
  FOREIGN KEY (display_id) REFERENCES displays(id) ON DELETE CASCADE
);

-- Global theme defaults; identity still comes from the setup wizard.
INSERT OR IGNORE INTO settings (key, value) VALUES
  ('theme.palette', 'classic-green'),
  ('theme.font', 'outfit'),
  ('theme.countdown_style', 'boxes'),
  ('theme.clock_format', '24h-seconds'),
  ('theme.hijri_form', 'long'),
  ('theme.boundary_strip', 'show'),
  ('theme.density', 'comfortable');
