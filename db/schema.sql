-- One row per voicemail processed. extraction/quote are JSONB snapshots
-- of the pipeline's structured outputs; columns exist only for fields
-- that get queried or filtered. caller_id/recording_url are populated
-- by the telephony layer (step 6); null for file-based ingestion.

CREATE TABLE IF NOT EXISTS jobs (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    status         TEXT NOT NULL DEFAULT 'received'
                   CHECK (status IN ('received', 'quoted', 'failed')),
    error          TEXT,
    caller_id      TEXT,          -- telephony From number, if any
    recording_url  TEXT,          -- telephony recording, if any
    audio_ref      TEXT NOT NULL, -- filename or recording SID
    stt_confidence REAL,
    transcript     TEXT,
    extraction     JSONB,
    quote          JSONB,
    sms_text       TEXT,
    email_subject  TEXT,
    email_body     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
