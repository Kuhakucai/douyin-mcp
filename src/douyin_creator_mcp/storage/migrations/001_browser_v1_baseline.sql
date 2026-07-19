CREATE TABLE videos (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  item_id TEXT,
  video_id TEXT,
  title TEXT,
  publish_time INTEGER,
  cover_url TEXT,
  video_url TEXT,
  duration INTEGER,
  status TEXT,
  source_fingerprint TEXT,
  parser_version TEXT,
  first_seen_at TEXT,
  last_seen_at TEXT,
  is_active INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE video_metrics (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  metric_date TEXT NOT NULL,
  play_count INTEGER,
  like_count INTEGER,
  comment_count INTEGER,
  share_count INTEGER,
  collect_count INTEGER,
  complete_rate REAL,
  avg_watch_duration REAL,
  follower_gain INTEGER,
  source TEXT NOT NULL,
  capability_key TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE sync_jobs (
  id TEXT PRIMARY KEY,
  account_id TEXT,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  error_type TEXT,
  error_message TEXT,
  progress_json TEXT,
  coverage_json TEXT,
  resume_cursor INTEGER,
  parser_version TEXT
);

CREATE TABLE video_metric_snapshots (
  id TEXT PRIMARY KEY,
  sync_job_id TEXT NOT NULL,
  video_id TEXT NOT NULL,
  account_id TEXT NOT NULL,
  source TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  exposure_count INTEGER,
  play_count INTEGER,
  five_second_completion_rate REAL,
  completion_rate REAL,
  average_watch_duration_seconds REAL,
  like_count INTEGER,
  collect_count INTEGER,
  comment_count INTEGER,
  share_count INTEGER,
  follower_gain INTEGER,
  raw_metric_json TEXT,
  missing_reason_json TEXT,
  quality TEXT NOT NULL,
  parser_version TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(sync_job_id, video_id, source)
);

CREATE TABLE video_derived_metrics (
  id TEXT PRIMARY KEY,
  snapshot_id TEXT NOT NULL,
  like_rate REAL,
  collect_rate REAL,
  comment_rate REAL,
  share_rate REAL,
  play_rate REAL,
  interaction_rate REAL,
  formula_version TEXT NOT NULL,
  calculated_at TEXT NOT NULL,
  UNIQUE(snapshot_id, formula_version)
);

CREATE TABLE reports (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  period TEXT NOT NULL,
  date_start TEXT,
  date_end TEXT,
  report_path TEXT,
  summary_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE browser_snapshots (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  source_url TEXT NOT NULL,
  title TEXT,
  status TEXT NOT NULL,
  extracted_json TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE browser_account_bindings (
  account_id TEXT PRIMARY KEY,
  fingerprint_salt TEXT NOT NULL,
  anchor_hashes_json TEXT NOT NULL,
  anchor_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  last_verified_at TEXT NOT NULL
);

CREATE INDEX idx_reports_account ON reports(account_id);
CREATE INDEX idx_sync_jobs_account ON sync_jobs(account_id);
CREATE INDEX idx_browser_snapshots_account ON browser_snapshots(account_id);
CREATE INDEX idx_videos_account ON videos(account_id);
CREATE INDEX idx_video_metrics_account ON video_metrics(account_id);
CREATE INDEX idx_metric_snapshots_video_source_time
  ON video_metric_snapshots(video_id, source, captured_at DESC);
CREATE INDEX idx_metric_snapshots_job ON video_metric_snapshots(sync_job_id);
CREATE INDEX idx_metric_snapshots_account_time
  ON video_metric_snapshots(account_id, captured_at DESC);
CREATE INDEX idx_derived_snapshot ON video_derived_metrics(snapshot_id);
