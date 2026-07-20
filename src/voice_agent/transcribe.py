"""Speech-to-text via Deepgram's pre-recorded audio API.

Usage:
    uv run python -m voice_agent.transcribe data/samples/job1.m4a
"""

import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from .config import get_settings

DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"

# Map file extensions to MIME types Deepgram understands.
CONTENT_TYPES = {
    ".m4a": "audio/mp4",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
}


@dataclass
class Transcription:
    text: str
    confidence: float  # 0-1, Deepgram's own estimate; low values should
    #                    route to a human, not an auto-quote
    duration_seconds: float


def transcribe_file(path: Path) -> Transcription:
    settings = get_settings()
    content_type = CONTENT_TYPES.get(path.suffix.lower())
    if content_type is None:
        raise ValueError(f"Unsupported audio format: {path.suffix}")

    response = httpx.post(
        DEEPGRAM_URL,
        params={
            "model": "nova-3",
            "smart_format": "true",  # punctuation + number/phone formatting
        },
        headers={
            "Authorization": f"Token {settings.deepgram_api_key}",
            "Content-Type": content_type,
        },
        content=path.read_bytes(),
        timeout=60.0,
    )
    response.raise_for_status()
    body = response.json()

    alternative = body["results"]["channels"][0]["alternatives"][0]
    return Transcription(
        text=alternative["transcript"],
        confidence=alternative["confidence"],
        duration_seconds=body["metadata"]["duration"],
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m voice_agent.transcribe <audio-file>")
    result = transcribe_file(Path(sys.argv[1]))
    print(f"duration:   {result.duration_seconds:.1f}s")
    print(f"confidence: {result.confidence:.3f}")
    print(f"---\n{result.text}")


if __name__ == "__main__":
    main()
