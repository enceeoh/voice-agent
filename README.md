# voice-agent

A production-deployed **voicemail-to-quote agent** for a (fictional) electrician
business. Ring the number, leave a rambling voicemail; the system transcribes
it, extracts a structured job request, prices it against a grounded price book,
and produces a customer SMS and an itemised office email — with a full audit
trail and a golden eval suite gating every change.

```
 caller ──phone──> Twilio ──webhook──> FastAPI (Railway)
                      │ (signature-validated, caller-ID captured)
                      v
        Deepgram STT ──> structured extraction ──> grounded quote
        (nova-3,          (strict schema:           (price book inlined
         confidence)       jobs/property/gaps)       w/ prompt caching)
                      │
                      v
        Postgres audit row + customer SMS + office email
```

## The pipeline, stage by stage

1. **Telephony** (`api.py`) — Twilio answers, greets (Polly voice), records.
   Every webhook is verified with hand-rolled `X-Twilio-Signature` HMAC
   validation. The recording callback smuggles the caller's number through a
   signed query param (Twilio's recording events don't carry `From`).
2. **Transcription** (`transcribe.py`) — Deepgram `nova-3` with `smart_format`
   (phone numbers arrive as digits, not word soup). Confidence is kept — a
   low score is a route-to-human signal, not noise.
3. **Extraction** (`extract.py`) — Claude with **strict structured outputs**:
   a Pydantic schema the API guarantees. Enum-typed services and property
   facts, `missing_info` as first-class output, a stated budget and
   contact constraints in typed fields. Extraction records what was *said* —
   it never applies pricing rules.
4. **Caller-ID merge** (`pipeline.py`) — most callers say "call me back" and
   never leave a number. Telephony metadata fills the gap; a number the
   caller chose to state wins over metadata.
5. **Quoting** (`quote.py`) — the price book and quoting rules are inlined in
   the system prompt behind a `cache_control` breakpoint (two documents fit;
   RAG is for corpora that don't). The model produces **line items only** —
   judgment. Totals and the £10k director-sign-off flag are **computed in
   code** — arithmetic. That split is load-bearing (see lessons).
6. **Rendering** (`render.py`) — deliberately model-free: GSM-7-safe
   two-segment SMS, itemised office email with per-line price-book citations
   and a numbered callback checklist. An enquiry that requests nothing gets
   an acknowledgement, never a "£0 estimate".
7. **Persistence** — every voicemail becomes a `jobs` row (transcript,
   extraction and quote as JSONB, rendered outputs, status lifecycle
   `received → quoted | failed`). Row-first semantics: a crash mid-pipeline
   leaves an auditable `failed` row, never a vanished voicemail.

## Evals

`evals/run_evals.py` runs 9 golden voicemails (real recordings, deliberately
messy) through the full pipeline and asserts **invariants, not exact text** —
a tiny declarative op vocabulary (`eq`, `set_eq`, `any_contains`, `len_eq`,
`gte`/`lte`) over the extraction and quote, plus universal guards (discounts
must be negative-priced; every priced item must cite a price-book basis).

The cases each target a failure class: clean baseline, degraded audio,
out-of-scope decline (gas boiler), a deliberately grey boundary case (humming
hot tub: supply fault-finding is in scope, appliance repair isn't — policy
encoded in the rules document, not the prompt), a £10k threshold straddler,
an ambiguity trap ("the fast charger" — must ask, not assume), a numbers
gauntlet, and an enquiry-only call harvested from the first live phone test.

## Things this project taught (the hard way)

- **Partition judgment from computation.** Three eval runs produced three
  different — individually defensible — totals semantics from the model.
  The fix wasn't a better prompt: totals and threshold flags were removed
  from the model's schema entirely and computed in code. If a task has one
  right answer, don't ask a sampler to produce it.
- **Evals are spec debuggers.** The arithmetic guard's first two "failures"
  were ambiguities in *our* spec (what does `conditional` mean? how do
  mutually exclusive options total?). Each failure became a one-sentence
  definition in the prompt and a permanent assertion.
- **The live path finds cases the lab never writes.** The first real phone
  call was an accidental enquiry-only voicemail (facts stated, nothing
  requested). Extraction handled it correctly; rendering didn't. The call's
  own recording became golden case #9.
- **Schema gaps don't lose data — they smuggle it.** Before `budget_gbp` and
  `contact_notes` existed, the model stuffed both into `property.notes`.
  If real callers keep volunteering a datum, give it a typed home.
- **Know when *not* to use the model.** Rendering is deterministic
  transformation of verified data: templates, no LLM. The price book fits in
  context: inline + cache, no RAG.
- **Telephony is gotchas all the way down.** Trial accounts, UK regulatory
  bundles, recording callbacks without caller ID, webhook signature
  validation, and a tunnel that was invisible to exactly one HTTP client on
  the internet (Twilio's TwiML fetcher vs. trycloudflare — bisected with a
  Twimlets echo, dissolved by deploying properly).

## Running it

```bash
docker compose up -d                # local Postgres (audit trail)
cp .env.example .env                # Anthropic, Deepgram, Twilio creds
uv sync
docker compose exec -T db psql -U voice -d voice < db/schema.sql

# file-based pipeline (no telephony needed)
uv run python -m voice_agent.pipeline path/to/voicemail.m4a "+447700900123"

# evals (golden recordings not committed — they're the author's voice)
uv run python evals/run_evals.py

# webhook server
uv run uvicorn voice_agent.api:app --port 8000
```

Deployed via the multi-stage `Dockerfile` (Railway: app + Postgres); point a
Twilio number's Voice webhook at `/voice` and set `PUBLIC_BASE_URL` so
signature validation reconstructs the exact public URL.

## Layout

```
src/voice_agent/    config, transcribe, extract, quote, render, pipeline, api
data/pricebook/     the grounding corpus (price book + quoting rules)
data/samples/       golden voicemail recordings (gitignored — real voice)
db/schema.sql       jobs audit table
evals/              golden cases + invariant runner + digest tool
```
