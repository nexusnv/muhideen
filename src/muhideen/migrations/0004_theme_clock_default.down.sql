-- Down-migration 0004: restore the previous seeded clock default.
-- Pre-1.0 reinterpretation: rows explicitly set to 12h flip back too
-- (no production installs to preserve); re-running 0004 re-applies 12h.

UPDATE settings SET value = '24h-seconds'
WHERE key = 'theme.clock_format' AND value = '12h';
