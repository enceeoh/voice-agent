"""Render a Quote into human-facing text: customer SMS + office email.

Deliberately model-free: the data is already structured and grounded;
rendering is deterministic. Totals and the sign-off flag are computed in
code (quote.compute_totals) — the model never does arithmetic; the only
runtime guard left is item-level sanity (verify_items).

Usage:
    uv run python -m voice_agent.render data/samples/job1.m4a
"""

import sys
from pathlib import Path

from .extract import JobExtraction, extract_job
from .quote import Quote, build_quote, compute_totals, needs_director_signoff
from .transcribe import transcribe_file


class InvalidDiscount(Exception):
    """A discount line item carries a positive price."""


def verify_items(quote: Quote) -> None:
    """Item-level sanity: discounts must be negative-or-null priced.
    (Totals need no verification — code computes them; see quote.py.)"""
    for item in quote.items:
        if item.item_type == "discount" and item.price_low_gbp is not None:
            if item.price_low_gbp > 0 or (item.price_high_gbp or 0) > 0:
                raise InvalidDiscount(f"positive-priced discount: {item.description[:60]}")


def _money(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        return "TBC"
    if low == high:
        return f"£{low:,.0f}"
    return f"£{low:,.0f}-£{high:,.0f}"  # hyphen, not em-dash: stays GSM-7


def customer_sms(quote: Quote, extraction: JobExtraction) -> str:
    """<=2 SMS segments, friendly-tradesperson voice, GSM-7-safe chars."""
    verify_items(quote)
    low, high = compute_totals(quote)
    services = " + ".join(
        j.service.replace("_", " ") for j in extraction.jobs
    ) or "your job"
    biggest_caveat = (
        "we'd need an inspection first as your fuse box may need updating"
        if any(i.item_type == "inspection" for i in quote.items)
        else "subject to a free survey"
    )
    return (
        f"Hi, Fuseworks Electrical here - thanks for your message about the "
        f"{services}. Rough estimate: {_money(low, high)} "
        f"depending on options, {biggest_caveat}. "
        f"Can we call you to book a free survey? Reply or ring us back. Cheers!"
    )


def office_email(
    quote: Quote, extraction: JobExtraction, transcript: str
) -> tuple[str, str]:
    """Returns (subject, plain-text body)."""
    verify_items(quote)
    low, high = compute_totals(quote)

    lines: list[str] = []
    lines.append(f"NEW VOICEMAIL JOB — {extraction.summary}")
    lines.append("")
    lines.append(f"Caller: {extraction.caller_name or 'not given'}")
    lines.append(f"Callback: {extraction.callback_number or 'NOT GIVEN — check caller ID'}")
    if extraction.contact_notes:
        lines.append(f"Contact notes: {extraction.contact_notes}")
    if extraction.budget_gbp is not None:
        lines.append(f"Stated budget: £{extraction.budget_gbp:,.0f}")
    lines.append("")
    lines.append("ESTIMATE (pending survey)")
    for item in quote.items:
        flag = " [CONDITIONAL]" if item.conditional else ""
        lines.append(f"  {_money(item.price_low_gbp, item.price_high_gbp):>14}  "
                     f"{item.description}{flag}")
        lines.append(f"                  basis: {item.basis}")
    lines.append(f"  {'-' * 14}")
    lines.append(f"  {_money(low, high):>14}  TOTAL RANGE")
    if needs_director_signoff(quote):
        lines.append("")
        lines.append("  *** REQUIRES DIRECTOR SIGN-OFF before sending (may exceed £10,000) ***")
    lines.append("")
    lines.append("TO CONFIRM ON CALLBACK")
    for n, item in enumerate(quote.to_confirm_with_customer, 1):
        lines.append(f"  {n}. {item}")
    if quote.out_of_scope_notes:
        lines.append("")
        lines.append("OUT OF SCOPE / NOTES")
        for note in quote.out_of_scope_notes:
            lines.append(f"  - {note}")
    lines.append("")
    lines.append("LEAD TIMES")
    for note in quote.lead_time_notes:
        lines.append(f"  - {note}")
    if quote.deposit_terms:
        lines.append(f"  - {quote.deposit_terms}")
    lines.append("")
    lines.append("DISCLAIMERS (include in written estimate)")
    for d in quote.disclaimers:
        lines.append(f"  - {d}")
    lines.append("")
    lines.append("--- VOICEMAIL TRANSCRIPT ---")
    lines.append(transcript)

    subject = f"Voicemail job: {extraction.summary[:70]}"
    return subject, "\n".join(lines)


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: python -m voice_agent.render <audio-file>")
    transcription = transcribe_file(Path(sys.argv[1]))
    extraction = extract_job(transcription.text, transcription.confidence)
    quote, _usage = build_quote(extraction)

    sms = customer_sms(quote, extraction)
    print(f"=== CUSTOMER SMS ({len(sms)} chars) ===\n{sms}\n")
    subject, body = office_email(quote, extraction, transcription.text)
    print(f"=== OFFICE EMAIL ===\nSubject: {subject}\n\n{body}")


if __name__ == "__main__":
    main()
