"""agents/forger.py — the Forger.

Generates adversarial "twins" of target/loyaltyledger/ledger.py with Gemini,
then runs every twin through harness/twin_runner.py to see whether the
existing test suite catches ("kills") the injected bug.

Two call paths sit behind one function (`_call_model`) so swapping between
them is a single flag:

1. The experimental Interactions API (`client.interactions.create`) with
   model "antigravity-preview-05-2026" (https://ai.google.dev/gemini-api/docs/agents)
   — the intended path for this stage.
2. A plain `client.models.generate_content` call with "gemini-3.5-flash" —
   used automatically the moment the Interactions API errors for any reason
   (model not rolled out yet, API surface still moving, etc). This is the
   path already proven working in Verify Gate 0.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time

AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(AGENTS_DIR)
DEFAULT_SOURCE = os.path.join(REPO_ROOT, "target", "loyaltyledger", "ledger.py")
REHEARSAL_DIR = os.path.join(REPO_ROOT, "demo", "rehearsal_twins")

sys.path.insert(0, os.path.join(REPO_ROOT, "harness"))
import twin_runner  # noqa: E402

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

INTERACTIONS_MODEL = "antigravity-preview-05-2026"
FALLBACK_MODEL = "gemini-3.5-flash"

BUG_FAMILIES = ["order-of-operations", "boundary", "rounding", "rare-path"]

MAX_PARSE_ATTEMPTS = 3  # 1 try + up to 2 retries, per spec.

SYSTEM_PROMPT = """You are the Forger. Given a Python source file and a target region, produce a
"twin": a full modified copy of the file containing ONE plausible semantic bug
that a strong but hurried engineer could write. Rules:
- Change ONLY within or near the target region. Keep the file syntactically
  valid and importable.
- The bug must be SEMANTIC, not syntactic: wrong operation ORDER, wrong
  boundary (>= vs >), wrong rounding mode, state mutated before being
  recorded, a condition inverted only in a rare path. NEVER: renamed
  functions, syntax errors, removed functions, changed signatures.
- The twin should look MORE careful than the original, not less.
- Output STRICT JSON: {"twin_source": "<entire modified file>",
  "description": "<one line, e.g. 'balance mutated before idempotency record'>",
  "divergence_hint": {"event_sequence": [<events likely to expose the bug>]}}"""


class ForgeError(RuntimeError):
    """Raised when a twin could not be produced after all retries."""


def _default_hunk_range(source_text):
    """Target the settle_event function by default — that's where the
    idempotency/rounding/boundary logic worth attacking actually lives."""
    lines = source_text.splitlines()
    start = end = None
    for i, line in enumerate(lines, start=1):
        if line.startswith("def settle_event"):
            start = i
        elif start is not None and line.startswith("def ") and i > start:
            end = i - 1
            break
    if start is None:
        return 1, len(lines)
    return start, end or len(lines)


def _build_user_message(source_text, hunk_range, bug_family):
    lines = source_text.splitlines()
    start, end = hunk_range
    start = max(1, start)
    end = min(len(lines), end)
    hunk_text = "\n".join(f"{i:>4}| {lines[i - 1]}" for i in range(start, end + 1))

    return (
        f"Target region: lines {start}-{end} of ledger.py (line numbers are for "
        "reference only; do not include them in twin_source).\n\n"
        f"--- target region ---\n{hunk_text}\n--- end target region ---\n\n"
        f"Full file (ledger.py):\n```python\n{source_text}\n```\n\n"
        f"This round's requested bug family: {bug_family}. Prefer a bug of this "
        "family if it fits naturally in or near the target region; otherwise use "
        "your judgement within the Rules.\n\n"
        "Respond with the STRICT JSON object described in your instructions, and "
        "nothing else — no markdown fences, no commentary."
    )


def _call_interactions(client, user_message, temperature):
    # antigravity-preview-05-2026 is a managed agent, not a plain model — the
    # Interactions API rejects it under `model=` ("Use it as an agent
    # instead") and expects it under `agent=`. Managed agents also don't
    # accept generation_config (they run with their own preset config), so
    # `temperature` only applies on the generate_content fallback path.
    #
    # Decided 12:XX (per the stage-3 time-box): beyond `agent=`, this
    # endpoint also demands a required `environment` — it's built for
    # multi-step, file-operating agent tasks (see AgentOption's docstring:
    # "perform multi-step tasks that require reasoning, file operations, and
    # tool use"), not one-shot text/JSON generation. That's a structural
    # mismatch with forge_twins' use case, not a transient error, so this
    # call is expected to keep failing and fall through to
    # generate_content below — which is the intended, permanent path here.
    interaction = client.interactions.create(
        agent=INTERACTIONS_MODEL,
        input=user_message,
        system_instruction=SYSTEM_PROMPT,
    )
    text = getattr(interaction, "output_text", None)
    if not text:
        raise RuntimeError("Interactions API returned no output_text")
    return text


def _call_generate_content(client, user_message, temperature):
    response = client.models.generate_content(
        model=FALLBACK_MODEL,
        contents=user_message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=temperature,
        ),
    )
    if not response.text:
        raise RuntimeError("generate_content returned empty text")
    return response.text


def _call_model(client, user_message, temperature, prefer_interactions=True):
    """One function, one flag: prefer_interactions picks the primary path,
    but any failure there transparently falls back to generate_content."""
    if prefer_interactions:
        try:
            text = _call_interactions(client, user_message, temperature)
            return text, f"interactions:{INTERACTIONS_MODEL}"
        except Exception as exc:
            print(
                f"[forger] Interactions API unavailable ({exc!r}); "
                "falling back to generate_content.",
                file=sys.stderr,
            )
    text = _call_generate_content(client, user_message, temperature)
    return text, f"generate_content:{FALLBACK_MODEL}"


def _parse_twin_json(raw_text):
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text)


def _validate_twin_schema(twin):
    if not isinstance(twin, dict):
        raise ValueError("twin JSON is not an object")
    twin_source = twin.get("twin_source")
    if not isinstance(twin_source, str) or not twin_source.strip():
        raise ValueError("twin_source missing or empty")
    if not isinstance(twin.get("description"), str) or not twin["description"].strip():
        raise ValueError("description missing or empty")
    hint = twin.get("divergence_hint")
    if not isinstance(hint, dict) or not isinstance(hint.get("event_sequence"), list):
        raise ValueError("divergence_hint.event_sequence missing")


def _forge_one(client, user_message, temperature, prefer_interactions):
    last_error = None
    for attempt in range(1, MAX_PARSE_ATTEMPTS + 1):
        try:
            raw_text, backend = _call_model(client, user_message, temperature, prefer_interactions)
            twin = _parse_twin_json(raw_text)
            _validate_twin_schema(twin)
            return twin, backend, attempt
        except Exception as exc:
            last_error = exc
            print(f"[forger] parse/validate attempt {attempt} failed: {exc!r}", file=sys.stderr)
    raise ForgeError(f"no valid twin JSON after {MAX_PARSE_ATTEMPTS} attempts: {last_error!r}")


def forge_twins(source_path, hunk_range, n=8, run_dir="runs/dev", prefer_interactions=True, temperature=1.0):
    """Generate n twins of source_path, evaluate each through the harness,
    and return a list of result dicts (also printed as a summary table)."""
    with open(source_path) as fh:
        source_text = fh.read()

    twins_dir = os.path.join(run_dir, "twins")
    os.makedirs(twins_dir, exist_ok=True)

    client = genai.Client()
    results = []

    for i in range(n):
        twin_id = f"twin_{i:02d}"
        bug_family = BUG_FAMILIES[i % len(BUG_FAMILIES)]
        user_message = _build_user_message(source_text, hunk_range, bug_family)

        twin_dir = os.path.join(twins_dir, twin_id)
        os.makedirs(twin_dir, exist_ok=True)

        try:
            twin, backend, attempts = _forge_one(client, user_message, temperature, prefer_interactions)
        except ForgeError as exc:
            verdict = twin_runner._finalize(run_dir, twin_id, "invalid_patch", 0, 0, 0.0)
            results.append(
                {
                    **verdict,
                    "description": f"forge failed: {exc}",
                    "bug_family": bug_family,
                    "backend": None,
                    "attempts": MAX_PARSE_ATTEMPTS,
                }
            )
            continue

        ledger_path = os.path.join(twin_dir, "ledger.py")
        with open(ledger_path, "w") as fh:
            fh.write(twin["twin_source"])
        with open(os.path.join(twin_dir, "meta.json"), "w") as fh:
            json.dump(
                {
                    "description": twin["description"],
                    "divergence_hint": twin["divergence_hint"],
                    "bug_family": bug_family,
                    "backend": backend,
                    "attempts": attempts,
                },
                fh,
                indent=2,
            )

        verdict = twin_runner.run_twin_from_source(ledger_path, run_dir, twin_id=twin_id)
        results.append(
            {
                **verdict,
                "description": twin["description"],
                "bug_family": bug_family,
                "backend": backend,
                "attempts": attempts,
            }
        )

    return results


def _print_summary(results):
    print(f"\n{'twin_id':<10} {'status':<14} {'bug_family':<20} description")
    print("-" * 90)
    for r in results:
        print(f"{r['twin_id']:<10} {r['status']:<14} {r['bug_family']:<20} {r['description']}")

    valid = sum(1 for r in results if r["status"] in ("killed", "survived"))
    killed = sum(1 for r in results if r["status"] == "killed")
    survived = sum(1 for r in results if r["status"] == "survived")
    invalid = sum(1 for r in results if r["status"] == "invalid_patch")
    print("-" * 90)
    print(f"total={len(results)} valid={valid} killed={killed} survived={survived} invalid={invalid}\n")


def _save_rehearsal_twins(run_dir):
    twins_src = os.path.join(run_dir, "twins")
    if not os.path.isdir(twins_src):
        return
    if os.path.isdir(REHEARSAL_DIR):
        shutil.rmtree(REHEARSAL_DIR)
    shutil.copytree(twins_src, REHEARSAL_DIR)


def main():
    parser = argparse.ArgumentParser(description="Forge and evaluate adversarial ledger.py twins.")
    parser.add_argument("--n", type=int, default=8, help="Number of twins to forge.")
    parser.add_argument("--run-dir", required=True, help="Directory to write twins/ and verdicts.jsonl into.")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="Source file to attack.")
    parser.add_argument("--temperature", type=float, default=1.0)
    parser.add_argument(
        "--no-interactions",
        action="store_true",
        help="Skip the Interactions API and go straight to generate_content.",
    )
    parser.add_argument("--no-rehearsal-save", action="store_true", help="Don't copy twins/ to demo/rehearsal_twins/.")
    args = parser.parse_args()

    with open(args.source) as fh:
        hunk_range = _default_hunk_range(fh.read())

    start = time.monotonic()
    results = forge_twins(
        args.source,
        hunk_range,
        n=args.n,
        run_dir=args.run_dir,
        prefer_interactions=not args.no_interactions,
        temperature=args.temperature,
    )
    duration = time.monotonic() - start

    _print_summary(results)
    print(f"[forger] done in {duration:.1f}s")

    if not args.no_rehearsal_save:
        _save_rehearsal_twins(args.run_dir)
        print(f"[forger] saved this run's twins to {REHEARSAL_DIR}")


if __name__ == "__main__":
    main()
