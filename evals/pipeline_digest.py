"""Run samples through the full pipeline and print a compact review digest.

Used when authoring golden files: human judges the digest, golden encodes
the judgment.

Usage:
    uv run python evals/pipeline_digest.py job2 job3 ...
"""

import sys
from pathlib import Path

from voice_agent.extract import extract_job
from voice_agent.quote import build_quote, compute_totals, needs_director_signoff
from voice_agent.transcribe import transcribe_file

SAMPLES = Path(__file__).parent.parent / "data" / "samples"


def digest(stem: str) -> None:
    print(f"\n{'=' * 70}\n{stem}\n{'=' * 70}")
    t = transcribe_file(SAMPLES / f"{stem}.m4a")
    print(f"[stt conf {t.confidence:.3f}, {t.duration_seconds:.0f}s]")
    print(f"TRANSCRIPT: {t.text}\n")

    e = extract_job(t.text, t.confidence)
    print(f"caller={e.caller_name!r}  callback={e.callback_number!r}")
    if e.contact_notes or e.budget_gbp is not None:
        print(f"contact_notes={e.contact_notes!r}  budget={e.budget_gbp!r}")
    print(f"property: {e.property.property_type} / cu_age={e.property.consumer_unit_age} / {e.property.location}")
    for j in e.jobs:
        print(f"  job: {j.service} — {j.details[:70]}")
    for o in e.out_of_scope_requests:
        print(f"  OUT-OF-SCOPE: {o[:70]}")
    print(f"  missing_info ({len(e.missing_info)}): {'; '.join(m[:40] for m in e.missing_info[:4])}")

    q, _ = build_quote(e)
    low, high = compute_totals(q)
    print(f"QUOTE: £{low:,.0f}-£{high:,.0f}  signoff={needs_director_signoff(q)}  items={len(q.items)}")
    for i in q.items:
        rng = "TBC" if i.price_low_gbp is None else f"£{i.price_low_gbp:,.0f}-£{i.price_high_gbp:,.0f}"
        print(f"  [{i.item_type}{'*' if i.conditional else ''}] {rng}  {i.description[:60]}")
    if q.out_of_scope_notes:
        print(f"  scope notes: {q.out_of_scope_notes[0][:80]}")


if __name__ == "__main__":
    for stem in sys.argv[1:]:
        digest(stem)
