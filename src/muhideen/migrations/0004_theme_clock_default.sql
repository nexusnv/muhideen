-- Theme clock default flip: seeded 24h-seconds rows become 12h.
-- Idempotent: safe to re-run after a partial failure. Explicit non-default
-- choices (24h) never match the WHERE clause and are left alone.

UPDATE settings SET value = '12h'
WHERE key = 'theme.clock_format' AND value = '24h-seconds';
