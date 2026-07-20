"""Golden-voicemail eval runner: audio -> pipeline -> invariant assertions.

Each evals/golden/*.json declares assertions over the extraction and the
quote. Two guards additionally run on every case: totals arithmetic
(verify_totals) and no-priced-item-without-basis.

Usage:
    uv run python evals/run_evals.py            # all golden cases
    uv run python evals/run_evals.py job1       # one case
"""

import json
import sys
from pathlib import Path

from voice_agent.extract import extract_job
from voice_agent.quote import build_quote, compute_totals, needs_director_signoff
from voice_agent.render import InvalidDiscount, verify_items
from voice_agent.transcribe import transcribe_file

GOLDEN_DIR = Path(__file__).parent / "golden"
SAMPLES_DIR = Path(__file__).parent.parent / "data" / "samples"


def resolve(data: object, path: str) -> object:
    """Dotted-path resolver; a segment like items[*] maps over a list."""
    current = [data]
    for segment in path.split("."):
        mapped = segment.endswith("[*]")
        key = segment.removesuffix("[*]")
        next_level = []
        for node in current:
            value = node[key] if isinstance(node, dict) else getattr(node, key)
            if mapped:
                next_level.extend(value)
            else:
                next_level.append(value)
        current = next_level
    return current if len(current) > 1 or "[*]" in path else current[0]


def check(assertion: dict, data: object) -> tuple[bool, str]:
    value = resolve(data, assertion["path"])
    op, expected = assertion["op"], assertion.get("value")
    if op == "eq":
        ok = value == expected
    elif op == "is_null":
        ok = value is None
    elif op == "gte":
        ok = value >= expected
    elif op == "len_eq":
        ok = len(value) == expected
    elif op == "set_eq":
        ok = set(value) == set(expected)
    elif op == "contains":
        ok = expected in value
    elif op == "any_contains":
        ok = any(expected.lower() in str(item).lower() for item in value)
    else:
        return False, f"unknown op {op!r}"
    return ok, f"{assertion['path']} {op} {expected!r} (got {_brief(value)})"


def _brief(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 60 else text[:57] + "..."


def run_case(golden_path: Path) -> bool:
    case = json.loads(golden_path.read_text())
    print(f"=== {golden_path.stem}: {case['name']}")

    transcription = transcribe_file(SAMPLES_DIR / case["audio"])
    extraction = extract_job(transcription.text, transcription.confidence)
    quote, _ = build_quote(extraction)

    # Computed fields (code-owned arithmetic) merged in so goldens can
    # assert on them alongside model-owned fields.
    low, high = compute_totals(quote)
    quote_data = quote.model_dump() | {
        "total_low_gbp": low,
        "total_high_gbp": high,
        "needs_director_signoff": needs_director_signoff(quote),
    }

    passed = True
    for target, data in (("extraction", extraction.model_dump()), ("quote", quote_data)):
        for assertion in case.get(target, []):
            ok, detail = check(assertion, data)
            passed &= ok
            print(f"  {'PASS' if ok else 'FAIL'}  [{target}] {detail}")

    # Universal guards — every case, no declaration needed.
    try:
        verify_items(quote)
        print("  PASS  [guard] discount items negative-or-null priced")
    except InvalidDiscount as err:
        passed = False
        print(f"  FAIL  [guard] {err}")

    unbased = [i.description[:40] for i in quote.items
               if i.price_low_gbp is not None and not i.basis.strip()]
    if unbased:
        passed = False
        print(f"  FAIL  [guard] priced items without basis: {unbased}")
    else:
        print("  PASS  [guard] every priced item cites a basis")

    print(f"  => {'PASSED' if passed else 'FAILED'}\n")
    return passed


def main() -> None:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    cases = sorted(GOLDEN_DIR.glob("*.json"))
    if only:
        cases = [c for c in cases if c.stem == only]
    if not cases:
        sys.exit("no golden cases found")

    results = [run_case(c) for c in cases]
    print(f"cases passed: {sum(results)}/{len(results)}")
    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
