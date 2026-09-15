BEGIN;

CREATE SCHEMA IF NOT EXISTS retrieval;

CREATE TABLE IF NOT EXISTS retrieval.search_runs (
    id                         uuid PRIMARY KEY,
    query                      text NOT NULL,
    query_plan                 jsonb NOT NULL DEFAULT '{}'::jsonb,
    scope                      jsonb NOT NULL DEFAULT '{}'::jsonb,
    semantic_collection        text NOT NULL,
    embedding_model            text NOT NULL,
    top_k                      integer NOT NULL CHECK (top_k BETWEEN 1 AND 50),
    final_limit                integer NOT NULL CHECK (final_limit BETWEEN 1 AND 20),
    min_score                  double precision NOT NULL CHECK (min_score BETWEEN 0 AND 1),
    semantic_hit_count         integer NOT NULL DEFAULT 0 CHECK (semantic_hit_count >= 0),
    candidate_revision_count   integer NOT NULL DEFAULT 0 CHECK (candidate_revision_count >= 0),
    total_candidate_count      integer NOT NULL DEFAULT 0 CHECK (total_candidate_count >= 0),
    returned_count             integer NOT NULL DEFAULT 0 CHECK (returned_count >= 0),
    duration_ms                double precision CHECK (duration_ms IS NULL OR duration_ms >= 0),
    score_formula              text NOT NULL,
    status                     text NOT NULL DEFAULT 'completed',
    metrics                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    warnings                   jsonb NOT NULL DEFAULT '[]'::jsonb,
    error_message              text,
    started_at                 timestamptz NOT NULL DEFAULT now(),
    finished_at                timestamptz,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS retrieval.candidates (
    id                         uuid PRIMARY KEY,
    search_run_id              uuid NOT NULL REFERENCES retrieval.search_runs(id) ON DELETE CASCADE,
    rank                       integer,
    revision_id                uuid REFERENCES core.drawing_revisions(id),
    drawing_key                text,
    drawing_title              text,
    semantic_score             double precision NOT NULL CHECK (semantic_score BETWEEN 0 AND 1),
    exact_score                double precision NOT NULL CHECK (exact_score BETWEEN 0 AND 1),
    evidence_score             double precision NOT NULL CHECK (evidence_score BETWEEN 0 AND 1),
    final_score                double precision NOT NULL CHECK (final_score BETWEEN 0 AND 1),
    status                     text NOT NULL,
    source_types               text[] NOT NULL DEFAULT ARRAY[]::text[],
    source_feature_ids         jsonb NOT NULL DEFAULT '[]'::jsonb,
    semantic_hits              jsonb NOT NULL DEFAULT '[]'::jsonb,
    exact_result               jsonb NOT NULL DEFAULT '{}'::jsonb,
    warnings                   jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (search_run_id, revision_id),
    CHECK (status IN ('verified', 'partially_verified', 'semantic_candidate', 'conflict', 'rejected'))
);

CREATE TABLE IF NOT EXISTS retrieval.constraint_checks (
    id                         uuid PRIMARY KEY,
    search_run_id              uuid NOT NULL REFERENCES retrieval.search_runs(id) ON DELETE CASCADE,
    candidate_id               uuid NOT NULL REFERENCES retrieval.candidates(id) ON DELETE CASCADE,
    constraint_type             text NOT NULL,
    constraint_name             text NOT NULL,
    expected                   jsonb NOT NULL DEFAULT '{}'::jsonb,
    actual                     jsonb NOT NULL DEFAULT '{}'::jsonb,
    passed                     boolean NOT NULL,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, constraint_type, constraint_name),
    CHECK (constraint_type IN ('dimension', 'entity_type', 'provenance'))
);

CREATE INDEX IF NOT EXISTS ix_retrieval_search_runs_created
    ON retrieval.search_runs (created_at DESC);

CREATE INDEX IF NOT EXISTS ix_retrieval_search_runs_status
    ON retrieval.search_runs (status, created_at DESC);

CREATE INDEX IF NOT EXISTS ix_retrieval_candidates_run_rank
    ON retrieval.candidates (search_run_id, rank);

CREATE INDEX IF NOT EXISTS ix_retrieval_candidates_revision
    ON retrieval.candidates (revision_id);

CREATE INDEX IF NOT EXISTS ix_retrieval_candidates_status
    ON retrieval.candidates (status);

CREATE INDEX IF NOT EXISTS ix_retrieval_constraint_checks_run
    ON retrieval.constraint_checks (search_run_id, passed);

CREATE INDEX IF NOT EXISTS ix_retrieval_constraint_checks_candidate
    ON retrieval.constraint_checks (candidate_id, constraint_type);

COMMIT;
