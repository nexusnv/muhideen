-- Playlist storage: playlists plus their ordered image items.
-- Idempotent: safe to re-run after a partial failure.

CREATE TABLE IF NOT EXISTS playlists (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, -- 0|1
  window_start TEXT, -- HH:MM or marker name, NULL = open
  window_end TEXT, -- HH:MM or marker name, NULL = open
  anchor_marker TEXT, -- marker name when anchored, else NULL
  anchor_start_offset_min INTEGER NOT NULL DEFAULT 0,
  anchor_stop_offset_min INTEGER NOT NULL DEFAULT 0,
  cycle_mode TEXT NOT NULL DEFAULT 'indefinite',
  max_cycles INTEGER -- NULL = uncapped
);

CREATE TABLE IF NOT EXISTS playlist_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  playlist_id TEXT NOT NULL,
  image_path TEXT NOT NULL,
  duration_s INTEGER NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_playlist_items_order
  ON playlist_items(playlist_id, sort_order);
