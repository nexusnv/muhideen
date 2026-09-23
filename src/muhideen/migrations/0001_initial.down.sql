-- Down-migration 0001: drop the PRD §6.2 v0.1 schema.
-- Order respects FK references (displays -> display_groups).

DROP TABLE IF EXISTS users;
DROP TABLE IF EXISTS displays;
DROP TABLE IF EXISTS display_groups;
DROP TABLE IF EXISTS media;
DROP TABLE IF EXISTS iqamah_rules;
DROP TABLE IF EXISTS prayer_times;
DROP TABLE IF EXISTS settings;
