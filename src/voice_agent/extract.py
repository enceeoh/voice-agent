"""Voicemail transcript -> structured job request.

Uses strict structured outputs: the API guarantees the response
validates against the Pydantic schema — no JSON parsing, no retries
on malformed output.

Usage:
    uv run python -m voice_agent.extract data/samples/job1.m4a
"""

import sys
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from pydantic import BaseModel

from .config import get_settings
from .transcribe import transcribe_file


class PropertyDetails(BaseModel):
    property_type: Literal["terraced", "semi_detached", "detached", "flat", "unknown"]
    location: str | None  # as stated, e.g. "Central London"
    consumer_unit_age: Literal["pre_2000", "post_2000", "unknown"]
    notes: str | None  # any other property facts stated verbatim-ish


class RequestedJob(BaseModel):
    service: Literal[
        "ev_charger",
        "solar_pv",
        "battery_storage",
        "consumer_unit",
        "eicr_inspection",
        "rewire",
        "sockets_or_lighting",
        "other",
    ]
    details: str  # what the caller actually said about this job


class JobExtraction(BaseModel):
    caller_name: str | None
    callback_number: str | None  # from the audio only; telephony caller-ID
    #                              is merged in later, at the webhook layer
    property: PropertyDetails
    jobs: list[RequestedJob]
    out_of_scope_requests: list[str]  # things we clearly don't do (plumbing etc.)
    missing_info: list[str]  # facts needed for a quote that the caller didn't give
    summary: str  # one sentence for the job ticket


EXTRACTION_PROMPT = """\
You extract structured job requests from voicemail transcripts left for an
electrician business. The transcript is verbatim speech-to-text: expect
disfluencies, repetitions, and mid-sentence corrections — read through them.

Rules:
- Record only what the caller actually said. Do not infer services they
  didn't ask for, and do not apply pricing or policy rules.
- Anything not stated is null/unknown. Never invent a name or number.
- "Late nineties" or similar dates before 2000 -> consumer_unit_age pre_2000.
- missing_info: list the specific facts a quoting electrician would still
  need (e.g. callback number, roof size or panel count, parking situation).
- out_of_scope_requests: anything requested that an electrician clearly
  does not do (gas, plumbing, roofing repair, appliances).
"""


def extract_job(transcript: str, stt_confidence: float) -> JobExtraction:
    settings = get_settings()
    client = Anthropic(api_key=settings.anthropic_api_key)

    response = client.messages.parse(
        model=settings.anthropic_model,
        max_tokens=2000,
        system=EXTRACTION_PROMPT,
        messages=[{
            "role": "user",
            "content": (
                f"Voicemail transcript (STT confidence {stt_confidence:.2f}):\n\n"
                f"{transcript}"
            ),
        }],
        output_format=JobExtraction,
    )
    return response.parsed_output


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m voice_agent.extract <audio-file>")
    transcription = transcribe_file(Path(sys.argv[1]))
    extraction = extract_job(transcription.text, transcription.confidence)
    print(extraction.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
