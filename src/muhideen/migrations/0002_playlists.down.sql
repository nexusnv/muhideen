-- Down-migration 0002: drop playlist storage (children first).

DROP TABLE IF EXISTS playlist_items;
DROP TABLE IF EXISTS playlists;
