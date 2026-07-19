CREATE TABLE video_transcript_runs (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  trigger TEXT NOT NULL,
  target_mode TEXT NOT NULL CHECK(target_mode IN ('video_ids','recent','all_public')),
  requested_video_ids_json TEXT NOT NULL DEFAULT '[]',
  target_state TEXT NOT NULL CHECK(target_state IN ('pending','discovering','frozen','failed')),
  lifecycle_state TEXT NOT NULL CHECK(lifecycle_state IN ('queued','running','waiting_user','cancelling','terminal')),
  result TEXT CHECK(result IS NULL OR result IN ('success','partial','failed','cancelled')),
  pipeline_version TEXT NOT NULL,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
  discovery_lease_owner TEXT,
  discovery_lease_token TEXT,
  discovery_lease_expires_at TEXT,
  discovery_heartbeat_at TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL,
  error_type TEXT,
  error_message TEXT
);

CREATE TABLE video_content_jobs (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  account_id TEXT NOT NULL,
  pipeline_version TEXT NOT NULL,
  force_requested INTEGER NOT NULL DEFAULT 0 CHECK(force_requested IN (0,1)),
  status TEXT NOT NULL CHECK(status IN ('queued','running','waiting_retry','waiting_user','completed','failed','cancelled')),
  stage TEXT NOT NULL CHECK(stage IN ('registered','target_verified','observing_bundle','downloading_candidates','validating_media','extracting_audio','transcribing','persisting','analysis_ready','no_speech')),
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 2,
  retry_class TEXT,
  next_attempt_at TEXT,
  lease_owner TEXT,
  lease_token TEXT,
  lease_expires_at TEXT,
  heartbeat_at TEXT,
  bundle_id TEXT,
  transcription_asset_id TEXT,
  reference_video_asset_id TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK(cancel_requested IN (0,1)),
  error_type TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX uq_active_video_content_job
  ON video_content_jobs(video_id, pipeline_version)
  WHERE status IN ('queued','running','waiting_retry','waiting_user');

CREATE TABLE video_transcript_run_items (
  run_id TEXT NOT NULL REFERENCES video_transcript_runs(id) ON DELETE RESTRICT,
  job_id TEXT NOT NULL REFERENCES video_content_jobs(id) ON DELETE RESTRICT,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  demand_state TEXT NOT NULL CHECK(demand_state IN ('active','cancelled','completed')),
  requested_force INTEGER NOT NULL DEFAULT 0 CHECK(requested_force IN (0,1)),
  outcome TEXT NOT NULL CHECK(outcome IN ('pending','analysis_ready','no_speech','failed','cancelled')),
  attached_at TEXT NOT NULL,
  detached_at TEXT,
  completed_at TEXT,
  error_type TEXT,
  error_message TEXT,
  PRIMARY KEY(run_id, job_id)
);

CREATE INDEX idx_run_items_job ON video_transcript_run_items(job_id, demand_state);
CREATE INDEX idx_content_jobs_claim
  ON video_content_jobs(status, next_attempt_at, lease_expires_at, created_at);

CREATE TABLE video_media_assets (
  id TEXT PRIMARY KEY,
  bundle_id TEXT NOT NULL,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  account_id TEXT NOT NULL,
  job_id TEXT NOT NULL REFERENCES video_content_jobs(id) ON DELETE RESTRICT,
  media_role TEXT NOT NULL CHECK(media_role IN ('video_only','audio_only','audiovisual')),
  state TEXT NOT NULL CHECK(state IN ('staging','validated','available','invalid','missing','deleted')),
  storage_path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0),
  duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
  container TEXT,
  audio_codec TEXT,
  video_codec TEXT,
  sample_rate INTEGER,
  channels INTEGER,
  is_transcription_source INTEGER NOT NULL DEFAULT 0 CHECK(is_transcription_source IN (0,1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(job_id, sha256, media_role)
);

CREATE UNIQUE INDEX uq_job_transcription_asset
  ON video_media_assets(job_id)
  WHERE state='available' AND is_transcription_source=1;

CREATE TABLE video_transcripts (
  id TEXT PRIMARY KEY,
  video_id TEXT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  account_id TEXT NOT NULL,
  job_id TEXT NOT NULL REFERENCES video_content_jobs(id) ON DELETE RESTRICT,
  asset_id TEXT REFERENCES video_media_assets(id) ON DELETE RESTRICT,
  revision INTEGER NOT NULL CHECK(revision > 0),
  is_current INTEGER NOT NULL CHECK(is_current IN (0,1)),
  status TEXT NOT NULL CHECK(status IN ('available','no_speech')),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  model_version TEXT,
  extractor_version TEXT NOT NULL,
  params_json TEXT NOT NULL,
  language TEXT,
  raw_text TEXT NOT NULL,
  text_sha256 TEXT NOT NULL,
  segment_count INTEGER NOT NULL CHECK(segment_count >= 0),
  duration_ms INTEGER CHECK(duration_ms IS NULL OR duration_ms >= 0),
  created_at TEXT NOT NULL,
  UNIQUE(video_id, revision)
);

CREATE UNIQUE INDEX uq_video_current_transcript
  ON video_transcripts(video_id) WHERE is_current=1;

CREATE TABLE video_transcript_segments (
  id TEXT PRIMARY KEY,
  transcript_id TEXT NOT NULL REFERENCES video_transcripts(id) ON DELETE CASCADE,
  segment_index INTEGER NOT NULL CHECK(segment_index >= 0),
  start_ms INTEGER NOT NULL CHECK(start_ms >= 0),
  end_ms INTEGER NOT NULL CHECK(end_ms >= start_ms),
  text TEXT NOT NULL,
  avg_logprob REAL,
  no_speech_prob REAL,
  language TEXT,
  UNIQUE(transcript_id, segment_index)
);

CREATE INDEX idx_transcript_segments_page
  ON video_transcript_segments(transcript_id, segment_index);
