ALTER TABLE videos ADD COLUMN visibility TEXT NOT NULL DEFAULT 'unknown'
  CHECK(visibility IN ('public','private','unknown'));
ALTER TABLE videos ADD COLUMN content_kind TEXT NOT NULL DEFAULT 'unknown'
  CHECK(content_kind IN ('video','image','unknown'));
ALTER TABLE videos ADD COLUMN classification_source TEXT;
