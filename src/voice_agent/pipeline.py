"""The one entry point: voicemail audio in, persisted job out.

Runs transcribe -> extract -> quote -> render and stores every stage's
output in the jobs table. This is what the telephony webhook (step 6)
calls; the CLI below is the file-based equivalent.

Failure semantics: the job row is created first with status 'received',
so a crash mid-pipeline leaves an auditable 'failed' row with the error —
never a silently vanished voicemail.

Usage:
    uv run python -m voice_agent.pipeline data/samples/job1.m4a
"""

import sys
import traceback
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from .config import get_settings
from .extract import extract_job
from .quote import build_quote
from .render import customer_sms, office_email
from .transcribe import transcribe_file


def process_voicemail(
    audio_path: Path,
    caller_id: str | None = None,
    recording_url: str | None = None,
) -> str:
    """Run the full pipeline; returns the job id. Never raises — failures
    are recorded on the job row."""
    settings = get_settings()
    with psycopg.connect(settings.database_url) as conn:
        row = conn.execute(
            """
            INSERT INTO jobs (audio_ref, caller_id, recording_url)
            VALUES (%s, %s, %s) RETURNING id
            """,
            (audio_path.name, caller_id, recording_url),
        ).fetchone()
        job_id = str(row[0])
        conn.commit()

        try:
            transcription = transcribe_file(audio_path)
            extraction = extract_job(transcription.text, transcription.confidence)

            # Caller-ID merge: the number most voicemails forget to say.
            # Audio-stated number wins (it's what the caller *chose* to
            # give); telephony metadata fills the gap.
            if extraction.callback_number is None and caller_id:
                extraction.callback_number = caller_id
                extraction.missing_info = [
                    m for m in extraction.missing_info
                    if "number" not in m.lower() and "callback" not in m.lower()
                ]

            quote, _usage = build_quote(extraction)
            sms = customer_sms(quote, extraction)
            subject, body = office_email(quote, extraction, transcription.text)

            conn.execute(
                """
                UPDATE jobs SET
                    status = 'quoted',
                    stt_confidence = %s,
                    transcript = %s,
                    extraction = %s,
                    quote = %s,
                    sms_text = %s,
                    email_subject = %s,
                    email_body = %s
                WHERE id = %s
                """,
                (
                    transcription.confidence,
                    transcription.text,
                    Jsonb(extraction.model_dump()),
                    Jsonb(quote.model_dump()),
                    sms,
                    subject,
                    body,
                    job_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.execute(
                "UPDATE jobs SET status = 'failed', error = %s WHERE id = %s",
                (traceback.format_exc()[-2000:], job_id),
            )
            conn.commit()

    return job_id


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python -m voice_agent.pipeline <audio-file> [caller-id]")
    caller_id = sys.argv[2] if len(sys.argv) > 2 else None
    job_id = process_voicemail(Path(sys.argv[1]), caller_id=caller_id)
    print(f"job: {job_id}")


if __name__ == "__main__":
    main()
