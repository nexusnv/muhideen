-- Down-migration 0003: drop per-display overrides, remove theme seeds.
-- Custom knob values are discarded; re-running 0003 re-seeds defaults.

DROP TABLE IF EXISTS display_settings;
DELETE FROM settings WHERE key LIKE 'theme.%';
