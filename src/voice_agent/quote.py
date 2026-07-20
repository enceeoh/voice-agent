"""Structured job request -> quote estimate, grounded in the price book.

Knowledge delivery is inline-with-caching: both price-book docs sit in
the system prompt behind a cache_control breakpoint. load_pricebook()
is the seam — a retrieval layer could replace it without touching the
quote logic.

Usage:
    uv run python -m voice_agent.quote data/samples/job1.m4a
"""

import sys
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from pydantic import BaseModel

from .config import get_settings
from .extract import JobExtraction, extract_job
from .transcribe import transcribe_file

PRICEBOOK_DIR = Path(__file__).parent.parent.parent / "data" / "pricebook"


def load_pricebook() -> str:
    """The knowledge-delivery seam. Today: inline both docs verbatim."""
    price_book = (PRICEBOOK_DIR / "price-book.md").read_text()
    rules = (PRICEBOOK_DIR / "quoting-rules.md").read_text()
    return (
        f"<price_book>\n{price_book}\n</price_book>\n\n"
        f"<quoting_rules>\n{rules}\n</quoting_rules>"
    )


class QuoteItem(BaseModel):
    item_type: Literal["service", "inspection", "surcharge", "discount"]
    description: str
    # Ranges are native to estimates (e.g. scaffolding £650-900).
    # Fixed prices set low == high. None = cannot be priced yet (TBC).
    price_low_gbp: float | None
    price_high_gbp: float | None
    basis: str  # which price book / rules section justifies this item
    conditional: bool  # true if contingent on survey/inspection findings


class Quote(BaseModel):
    # Deliberately NO totals and NO sign-off flag: those are pure
    # computation over the line items, owned by code (compute_totals /
    # needs_director_signoff below). Three eval runs produced three
    # different model-computed totals semantics; judgment belongs to the
    # model, arithmetic does not.
    items: list[QuoteItem]
    lead_time_notes: list[str]
    deposit_terms: str | None
    to_confirm_with_customer: list[str]
    out_of_scope_notes: list[str]
    disclaimers: list[str]


DIRECTOR_SIGNOFF_THRESHOLD_GBP = 10_000


def compute_totals(quote: Quote) -> tuple[float, float]:
    """Deterministic totals. Low bound: best case — conditional items may
    not be needed, so they're excluded. High bound: everything, at the
    top of each range. Negative-priced discounts fold in naturally."""
    low = sum(
        i.price_low_gbp
        for i in quote.items
        if i.price_low_gbp is not None and not i.conditional
    )
    high = sum(i.price_high_gbp for i in quote.items if i.price_high_gbp is not None)
    return low, high


def needs_director_signoff(quote: Quote) -> bool:
    _, high = compute_totals(quote)
    return high > DIRECTOR_SIGNOFF_THRESHOLD_GBP


QUOTING_INSTRUCTIONS = """\
You prepare written estimates for Fuseworks Electrical from structured
job requests. Follow the price book and quoting rules exactly:

- Every price must come from the price book; cite the section in `basis`.
  Never invent a price. If an item cannot be priced from the information
  given, set prices to null and add what's needed to to_confirm_with_customer.
- Apply every quoting rule that the job request triggers: older-property
  consumer unit rule, access/location surcharges, bundle discounts, lead
  times, deposits.
- Where a discount is computable, express it as a line item with NEGATIVE
  prices (e.g. -259.0). Where it cannot be computed yet, use null prices
  and explain in the description.
- Do not compute totals — they are derived from your line items in code.
- Mark items contingent on survey or inspection findings as conditional.
- Never price out-of-scope work; note it in out_of_scope_notes instead.
- Estimates from voicemails are always estimates pending survey — include
  the mandated disclaimers.
- Where the request is ambiguous between priced options (e.g. tethered vs
  untethered, panel count), quote the option range and list the choice in
  to_confirm_with_customer rather than silently picking one.
"""


def build_quote(extraction: JobExtraction) -> tuple[Quote, object]:
    settings = get_settings()
    client = Anthropic(api_key=settings.anthropic_api_key)

    response = client.messages.parse(
        model=settings.anthropic_model,
        max_tokens=4000,
        system=[
            {"type": "text", "text": QUOTING_INSTRUCTIONS},
            {
                "type": "text",
                "text": load_pricebook(),
                # Stable prefix ends here; the per-voicemail extraction
                # below stays outside the cached span.
                "cache_control": {"type": "ephemeral"},
            },
        ],
        messages=[{
            "role": "user",
            "content": f"Job request:\n\n{extraction.model_dump_json(indent=2)}",
        }],
        output_format=Quote,
    )
    return response.parsed_output, response.usage


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m voice_agent.quote <audio-file>")
    transcription = transcribe_file(Path(sys.argv[1]))
    extraction = extract_job(transcription.text, transcription.confidence)
    quote, usage = build_quote(extraction)
    print(quote.model_dump_json(indent=2))
    print(
        f"\n[cache] wrote {usage.cache_creation_input_tokens} tokens, "
        f"read {usage.cache_read_input_tokens}, uncached {usage.input_tokens}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
