"""agents/narrator.py — the Narrator.

The second agent, with a deliberately narrow, low-risk job: after a run
completes, read ONLY the deterministic artifacts already written to disk
(verdicts.jsonl, each twin's meta.json + witness.json, ledger.json) and ask
gemini-3.5-flash, in a single call, to describe what is already true. It
never re-executes anything and never sees the model that produced the bugs
in the first place — it can only narrate facts the harness/witness already
proved.

This agent is purely additive. `narrate()` never raises: any failure
(offline demo, quota, malformed JSON from the model) is caught and reported
as `None`, and every caller treats `None` as "render the UI exactly as if
narration had never been attempted."
"""

import argparse
import json
import os
import re
import sys

AGENTS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(AGENTS_DIR)

from google import genai  # noqa: E402
from google.genai import types  # noqa: E402

NARRATOR_MODEL = "gemini-3.5-flash"

SYSTEM_PROMPT = """You are the Narrator for Kinit, a system that tests whether a developer's
"I verified this code" claims survive adversarial twins. You receive ONLY
verified execution facts as JSON. Write: (1) one plain-English sentence per
twin explaining the planted bug class, (2) a 3-sentence "judgment report"
of what this developer caught, missed, and proved, referencing the witness
input concretely. NEVER speculate beyond the provided facts. NEVER give
advice. Output JSON: {"twin_lines": {...}, "report": "..."}"""


class NarrationError(RuntimeError):
    """Raised internally on any failure; narrate() always catches this."""


def _read_json(path, default=None):
    if not os.path.isfile(path):
        return default
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return default


def _read_verdicts(run_dir):
    path = os.path.join(run_dir, "verdicts.jsonl")
    verdicts = []
    if not os.path.isfile(path):
        return verdicts
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                verdicts.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return verdicts


def collect_facts(run_dir):
    """Gather ONLY deterministic, already-written artifacts for this run.

    No re-execution, no re-derivation — every fact here was already proven
    by twin_runner.py or witness.py before the Narrator ever sees it.
    """
    verdicts = _read_verdicts(run_dir)
    twins_dir = os.path.join(run_dir, "twins")

    twins = []
    for verdict in verdicts:
        twin_id = verdict["twin_id"]
        twin_dir = os.path.join(twins_dir, twin_id)
        meta = _read_json(os.path.join(twin_dir, "meta.json"), {}) or {}
        witness = _read_json(os.path.join(twin_dir, "witness.json"))
        twins.append(
            {
                "twin_id": twin_id,
                "status": verdict["status"],
                "tests_passed": verdict.get("tests_passed"),
                "tests_failed": verdict.get("tests_failed"),
                "description": meta.get("description"),
                "bug_family": meta.get("bug_family"),
                "witness": witness,
            }
        )

    ledger = _read_json(os.path.join(run_dir, "ledger.json"))
    return {"twins": twins, "ledger": ledger}


def _build_user_message(facts):
    return (
        "Verified execution facts (JSON) for one Kinit run. Every field here "
        "was produced by deterministic code (pytest, a diff-based mutation "
        "harness, and hypothesis-based differential fuzzing) — nothing was "
        "inferred or guessed.\n\n"
        f"{json.dumps(facts, indent=2, default=str)}\n\n"
        "Respond with the STRICT JSON object described in your instructions, "
        "and nothing else — no markdown fences, no commentary."
    )


def _parse_response(raw_text):
    text = raw_text.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]

    data = json.loads(text)
    if not isinstance(data, dict):
        raise NarrationError("narration response is not a JSON object")
    if not isinstance(data.get("twin_lines"), dict):
        raise NarrationError("twin_lines missing or not an object")
    if not isinstance(data.get("report"), str) or not data["report"].strip():
        raise NarrationError("report missing or not a non-empty string")
    return data


def narrate(run_dir, client=None):
    """Ask gemini-3.5-flash to narrate this run's already-written facts.

    Returns the parsed {"twin_lines": {...}, "report": "..."} dict on
    success, also writing it to <run_dir>/narration.json. Returns None on
    ANY failure — network, quota, bad JSON — since this agent is additive
    only and must never take the demo down with it.
    """
    try:
        facts = collect_facts(run_dir)
        if not facts["twins"]:
            raise NarrationError("no verdicts to narrate yet")

        active_client = client or genai.Client()
        response = active_client.models.generate_content(
            model=NARRATOR_MODEL,
            contents=_build_user_message(facts),
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.2,
            ),
        )
        if not response.text:
            raise NarrationError("empty response from narrator model")

        narration = _parse_response(response.text)
    except Exception as exc:  # noqa: BLE001 - additive-only, must never propagate
        print(f"[narrator] narration skipped: {exc!r}", file=sys.stderr)
        return None

    with open(os.path.join(run_dir, "narration.json"), "w") as fh:
        json.dump(narration, fh, indent=2)
    return narration


def main():
    parser = argparse.ArgumentParser(description="Narrate a completed Kinit run's deterministic artifacts.")
    parser.add_argument("--run-dir", required=True, help="Run directory with verdicts.jsonl, twins/, ledger.json.")
    args = parser.parse_args()

    narration = narrate(args.run_dir)
    if narration is None:
        print(json.dumps({"narrated": False}))
        return
    print(json.dumps({"narrated": True, **narration}, indent=2))


if __name__ == "__main__":
    main()
