-- app.duckdb migrations. Idempotent — run on every startup.

CREATE TABLE IF NOT EXISTS app.users (
  username   TEXT PRIMARY KEY,
  created_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS app.ratings (
  username   TEXT NOT NULL,
  work_key   TEXT NOT NULL,
  input_text TEXT NOT NULL,          -- denormalized 'Book|author|title' for vec lookup
  rating     INTEGER NOT NULL CHECK (rating IN (-2,-1,1,2)),
  updated_at TIMESTAMP NOT NULL,
  PRIMARY KEY (username, work_key)
);

CREATE TABLE IF NOT EXISTS app.ondemand_vecs (
  input_sha256 TEXT NOT NULL,
  model_id     TEXT NOT NULL,
  input_text   TEXT NOT NULL,
  vec          FLOAT[] NOT NULL,     -- 4096, L2-normalized
  created_at   TIMESTAMP NOT NULL,
  PRIMARY KEY (input_sha256, model_id)
);
