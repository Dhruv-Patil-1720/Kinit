"""harness/witness.py — the Witness Finder.

Given a twin that "survived" the test suite, find a concrete input on which
the real ledger.py and the twin diverge: the runnable proof that the twin
really does contain a bug the suite missed. Pure code, zero AI:

1. Always try the twin's own divergence_hint.event_sequence (from
   forger-generated meta.json) first — usually fires in well under a
   second.
2. If that doesn't diverge (or there's no hint), fall back to hypothesis-
   driven fuzzing of small event sequences, letting hypothesis's own
   shrinking minimize whatever divergence it finds.
3. Confirm the divergence reproduces 3x before writing it out, so
   witness.json is never a fluke.

If nothing diverges inside the time budget, the twin is discarded — per
spec, that's a correct outcome, not an error.
"""

import argparse
import importlib.util
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HARNESS_DIR)
REAL_LEDGER_PATH = os.path.join(REPO_ROOT, "target", "loyaltyledger", "ledger.py")

EVENT_IDS = ["e1", "e2", "e3"]
USERS = ["u1", "u2"]
TYPES = ["earn", "redeem"]
TIERS = ["basic", "silver", "gold"]

MAX_EXAMPLES = 3000

_event_strategy = st.fixed_dictionaries(
    {
        "event_id": st.sampled_from(EVENT_IDS),
        "user": st.sampled_from(USERS),
        "type": st.sampled_from(TYPES),
        "amount": st.integers(min_value=1, max_value=500),
        "tier": st.sampled_from(TIERS),
        "retry": st.booleans(),
    }
)
_event_sequence_strategy = st.lists(_event_strategy, min_size=1, max_size=6)


class Divergence(AssertionError):
    """Raised the moment real and twin outputs disagree on some input."""

    def __init__(self, events, real_output, twin_output, message):
        super().__init__(message)
        self.events = events
        self.real_output = real_output
        self.twin_output = twin_output
        self.message = message


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _run_settle_batch(module, events):
    """Returns (output, error). output is None if settle_batch raised."""
    try:
        state, results = module.settle_batch(module.new_state(), events)
        return {"final_state": state, "results": results}, None
    except Exception as exc:  # noqa: BLE001 - a twin can raise almost anything
        return None, exc


def _describe_divergence(real_output, twin_output, real_error, twin_error):
    if real_error is not None or twin_error is not None:
        if real_error is not None and twin_error is None:
            return f"real raised {real_error!r} but twin ran fine"
        if twin_error is not None and real_error is None:
            return f"twin raised {twin_error!r} but real ran fine"
        return f"both raised, differently: real={real_error!r} twin={twin_error!r}"

    real_state, twin_state = real_output["final_state"], twin_output["final_state"]
    real_results, twin_results = real_output["results"], twin_output["results"]

    if real_state.get("balances") != twin_state.get("balances"):
        return f"final balances differ: real={real_state.get('balances')} twin={twin_state.get('balances')}"
    if real_state.get("processed_events") != twin_state.get("processed_events"):
        return (
            f"processed_events differ: real={real_state.get('processed_events')} "
            f"twin={twin_state.get('processed_events')}"
        )
    if len(real_results) != len(twin_results):
        return f"result count differs: real={len(real_results)} twin={len(twin_results)}"
    for i, (r, t) in enumerate(zip(real_results, twin_results)):
        if r != t:
            return f"results[{i}] differs: real={r} twin={t}"
    if real_state.get("history") != twin_state.get("history"):
        return "history log differs (balances/processed_events/results all matched)"
    return "outputs differ in an unspecified way"


def _compare(real_mod, twin_mod, events):
    real_output, real_error = _run_settle_batch(real_mod, events)
    twin_output, twin_error = _run_settle_batch(twin_mod, events)

    if real_error is not None and twin_error is not None:
        return  # both blew up; not a clean witness either way

    if real_error is not None or twin_error is not None or real_output != twin_output:
        message = _describe_divergence(real_output, twin_output, real_error, twin_error)
        raise Divergence(events, real_output, twin_output, message)


def _load_hint_events(twin_dir):
    meta_path = os.path.join(twin_dir, "meta.json")
    if not os.path.isfile(meta_path):
        return []
    try:
        with open(meta_path) as fh:
            meta = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return []

    raw_events = (meta.get("divergence_hint") or {}).get("event_sequence") or []
    events = []
    for i, item in enumerate(raw_events):
        if not isinstance(item, dict):
            continue  # forger sometimes appends a free-text explanation too
        events.append(
            {
                "event_id": item.get("event_id", EVENT_IDS[i % len(EVENT_IDS)]),
                "user": item.get("user", "u1"),
                "type": item.get("type", "earn"),
                "amount": item.get("amount", 100),
                "tier": item.get("tier", "basic"),
                "retry": item.get("retry", False),
            }
        )
    return events


def _make_fuzzer(real_mod, twin_mod):
    @settings(
        max_examples=MAX_EXAMPLES,
        deadline=None,
        database=None,
        derandomize=True,
        suppress_health_check=list(HealthCheck),
    )
    @given(events=_event_sequence_strategy)
    def fuzz(events):
        _compare(real_mod, twin_mod, events)

    return fuzz


def _fuzz_for_divergence(real_mod, twin_mod, budget_s):
    if budget_s <= 0:
        return None
    fuzz = _make_fuzzer(real_mod, twin_mod)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fuzz)
        try:
            future.result(timeout=budget_s)
        except FutureTimeoutError:
            return None
        except Divergence as exc:
            return exc
    return None


def _confirm_reproducible(real_mod, twin_mod, events, expected_message, times=3):
    for _ in range(times):
        try:
            _compare(real_mod, twin_mod, events)
        except Divergence as exc:
            if exc.message != expected_message:
                return False
        else:
            return False  # didn't diverge this time -- not reliable
    return True


def _write_witness(twin_dir, divergence):
    witness = {
        "input_events": divergence.events,
        "real_output": divergence.real_output,
        "twin_output": divergence.twin_output,
        "first_divergence": divergence.message,
    }
    with open(os.path.join(twin_dir, "witness.json"), "w") as fh:
        json.dump(witness, fh, indent=2, default=str)
    return witness


def find_witness(twin_dir, timeout_s=45):
    """Search for a concrete input on which the twin diverges from the real
    ledger. Returns the witness dict (and writes <twin_dir>/witness.json) on
    success, or None if nothing diverged inside the time budget."""
    start = time.monotonic()
    twin_dir = os.path.abspath(twin_dir)
    twin_path = os.path.join(twin_dir, "ledger.py")

    token = uuid.uuid4().hex[:8]
    real_mod = _load_module(REAL_LEDGER_PATH, f"witness_real_{token}")
    twin_mod = _load_module(twin_path, f"witness_twin_{token}")

    divergence = None

    hint_events = _load_hint_events(twin_dir)
    if hint_events:
        try:
            _compare(real_mod, twin_mod, hint_events)
        except Divergence as exc:
            divergence = exc

    if divergence is None:
        remaining = timeout_s - (time.monotonic() - start)
        divergence = _fuzz_for_divergence(real_mod, twin_mod, remaining)

    if divergence is None:
        return None

    if not _confirm_reproducible(real_mod, twin_mod, divergence.events, divergence.message):
        return None

    return _write_witness(twin_dir, divergence)


def main():
    parser = argparse.ArgumentParser(description="Find a concrete input where a twin diverges from the real ledger.")
    parser.add_argument("--twin", required=True, help="Path to a twin directory containing ledger.py.")
    parser.add_argument("--timeout", type=float, default=45.0, dest="timeout_s")
    args = parser.parse_args()

    witness = find_witness(args.twin, timeout_s=args.timeout_s)
    if witness is None:
        print(json.dumps({"witness_found": False}))
        return
    print(json.dumps({"witness_found": True, **witness}, indent=2))


if __name__ == "__main__":
    main()
