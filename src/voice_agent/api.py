"""Telephony webhook server.

Two endpoints Twilio calls:
  POST /voice              — call comes in: greet + record
  POST /webhooks/recording — recording ready: validate, download, pipeline

Security: every webhook is verified against X-Twilio-Signature —
HMAC-SHA1 over (exact public URL + form params sorted by key), keyed on
the auth token. Without this, anyone who finds the URL can inject fake
voicemails into the pipeline.
"""

import base64
import hashlib
import hmac
import logging
import tempfile
from pathlib import Path
from urllib.parse import quote, urlencode

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response

from .config import get_settings
from .pipeline import process_voicemail

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="voice-agent")

GREETING = (
    "Hello, you've reached Fuseworks Electrical. "
    "We can't take your call right now. Please leave your name, your number, "
    "and what you need done, and we'll text you an estimate and call you back. "
    "Speak after the beep."
)


def twilio_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Twilio's scheme: URL + params concatenated key+value in sorted key
    order, HMAC-SHA1 with the auth token, base64."""
    payload = url + "".join(k + v for k, v in sorted(params.items()))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


async def verify_twilio(request: Request) -> tuple[bool, dict[str, str]]:
    settings = get_settings()
    form = {k: str(v) for k, v in (await request.form()).items()}
    # Reconstruct the URL as Twilio saw it: our public base + path + query.
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    expected = twilio_signature(url, form, settings.twilio_auth_token)
    provided = request.headers.get("X-Twilio-Signature", "")
    return hmac.compare_digest(expected, provided), form


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/voice")
async def voice(request: Request) -> Response:
    ok, form = await verify_twilio(request)
    if not ok:
        return Response(status_code=403)

    # On inbound calls the customer is From; on outbound-api calls
    # (e.g. trial-account testing where Twilio dials the customer),
    # the customer is To.
    direction = form.get("Direction", "inbound")
    caller = form.get("To" if direction.startswith("outbound") else "From", "")
    # Recording callbacks don't include From — smuggle it via query param
    # (covered by the signature, so it can't be tampered with).
    callback_url = f"/webhooks/recording?{urlencode({'caller': caller})}"
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
  <Say voice="Polly.Amy">{GREETING}</Say>
  <Record maxLength="120" playBeep="true"
          recordingStatusCallback="{callback_url}"
          recordingStatusCallbackEvent="completed"/>
  <Say voice="Polly.Amy">Thank you. We'll be in touch shortly. Goodbye.</Say>
</Response>"""
    return Response(content=twiml, media_type="application/xml")


def run_pipeline_from_recording(recording_url: str, recording_sid: str, caller: str) -> None:
    settings = get_settings()
    audio = httpx.get(
        recording_url + ".mp3",
        auth=(settings.twilio_account_sid, settings.twilio_auth_token),
        timeout=60,
        follow_redirects=True,
    )
    audio.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp.write(audio.content)
        tmp_path = Path(tmp.name)
    try:
        job_id = process_voicemail(
            tmp_path, caller_id=caller or None, recording_url=recording_url
        )
        logger.info("processed recording %s -> job %s", recording_sid, job_id)
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/webhooks/recording")
async def recording_status(request: Request, background: BackgroundTasks) -> Response:
    ok, form = await verify_twilio(request)
    if not ok:
        return Response(status_code=403)
    if form.get("RecordingStatus") != "completed":
        return Response(status_code=204)

    caller = request.query_params.get("caller", "")
    background.add_task(
        run_pipeline_from_recording,
        form["RecordingUrl"],
        form.get("RecordingSid", "unknown"),
        caller,
    )
    # Return immediately — Twilio retries anything slower than ~15s.
    return Response(status_code=204)
