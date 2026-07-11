# Kinit — proof of AI judgment

Kinit answers one question a human reviewer can't answer just by reading a
diff: when a developer clicks "I verified this code," did they actually
build a mental model of it, or did they just skim it? The target service —
`target/loyaltyledger/`, a small loyalty-points settlement engine — is AI
generated on the spot and reviewed hunk by hunk. Kinit then forges
adversarial "twins" of that same file, each with one plausible semantic bug,
and runs every twin through the reviewer's own test suite. A twin that
survives is a blind spot in the review, not just in the code.

Every step downstream of "the code exists" is either fully deterministic
(pytest, a diff/file-swap mutation harness, hypothesis-driven differential
fuzzing) or a narrowly-scoped AI call whose output is checked against those
deterministic facts before it ever reaches a screen. The one AI-vs-human
moment that actually counts — "what does your own verified code return for
this input?" — is asked directly of the reviewer on a concrete, reproducible
input the fuzzer found, and scored into a single number: the Symbiosis
Index.

## Architecture

```
target/loyaltyledger/ledger.py         <- the target service, built during
        |                                 the event, used as bait
        |  AI writes it; reviewer clicks "Verified" per hunk   (/review)
        v
+----------------------+
|   Forger agent        |  Gemini (Interactions/iAPI attempted first,
|  agents/forger.py      |  falls back to generate_content). Writes 8
+----------------------+  "twins": same file, one planted semantic bug.
        |
        v
+----------------------+
|   twin_runner.py       |  deterministic, zero AI: copy target -> apply
|   harness/             |  twin -> `pytest -q`. Verdict: killed / survived
+----------------------+  / invalid_patch. Every verdict -> verdicts.jsonl.
        |  survived twins only
        v
+----------------------+
|   witness.py           |  zero AI: hypothesis differential fuzzing of
|   harness/             |  event sequences until real and twin disagree;
+----------------------+  confirmed reproducible 3x -> witness.json.
        |  a concrete, reproducible divergence
        v
+----------------------+
|   /probe (web UI)      |  the human who clicked "Verified" predicts
+----------------------+  which of two outputs their OWN code returns.
        |
        v
+----------------------+
|   /verdict (web UI)    |  Symbiosis Index = 40*(killed/valid twins)
+----------------------+  + (50 if probe correct else 10) + 10 baseline
                          -> KINIT VERIFIED (>=82) or UNVERIFIED.

                 (in parallel, after each run completes)
+----------------------+
|   Narrator agent       |  gemini-3.5-flash, ONE call, reads ONLY the
|   agents/narrator.py   |  deterministic JSON already on disk (verdicts,
+----------------------+  witness, ledger) and narrates it in plain
                          English. Additive only — the UI renders fine
                          if this call fails or the network is down.
```

## Layout

```
kinit/
  target/loyaltyledger/
    ledger.py            # pure-function settlement core (dict in -> dict out)
    tests/test_ledger.py # pytest suite
  harness/
    twin_runner.py       # applies a twin (patch or full file), runs its tests, emits a verdict
    witness.py           # finds a concrete input where a surviving twin diverges from the real ledger
  agents/
    forger.py            # generates adversarial "twins" of ledger.py with Gemini
    narrator.py           # narrates a completed run's deterministic artifacts (additive only)
  demo/
    demo.py              # small runnable walkthrough
    hand_twins/           # two hand-written patches used to sanity-check the harness
    rehearsal_twins/       # a saved forger run, used as the offline/no-Wi-Fi demo fallback
  web/
    app.py                # FastAPI backend for the 4 demo screens
    static/                # plain HTML/CSS/JS (no framework, no build step)
  runs/                   # generated run artifacts (gitignored, kept via .gitkeep)
```

## `ledger.py` — the target service

Built during the event and used as the test subject, not as a product: a
tiny, pure-functional loyalty points settlement service. No I/O, no
classes, no mutation of inputs — every function returns a fresh copy of
state.

- `new_state() -> dict` — `{"balances": {}, "processed_events": {}, "history": []}`
- `settle_event(state, event) -> (new_state, result)` — settle a single event.
- `settle_batch(state, events) -> (final_state, results)` — settle a sequence of events in order.

### Event shape

```python
{
    "event_id": str,
    "user": str,
    "type": "earn" | "redeem",
    "amount": int,
    "tier": "basic" | "silver" | "gold",
    "retry": bool,
}
```

### Rules

- `earn`: credits `amount * multiplier` points, rounded half-up to an int
  (`basic` = 1.0x, `silver` = 1.5x, `gold` = 2.0x).
- `redeem`: deducts `amount` points; fails with `"insufficient"` if the
  user's balance is less than `amount`.
- Idempotency: every `event_id` is recorded in `processed_events` the first
  time it's seen (before any balance mutation is applied), regardless of
  whether that first attempt succeeded or failed. Any later event with the
  same `event_id` short-circuits to `"duplicate"` and returns the state
  unchanged.
- `amount <= 0` always yields `"invalid"`.

## Setup

Requires **Python 3.11+** and a Gemini API key (only needed for the live
Forger/Narrator calls — everything else, including the full web UI in
rehearsal mode, runs with no network at all).

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env   # paste your GEMINI_API_KEY in
```

## Running the tests

```bash
cd target/loyaltyledger
../../.venv/bin/python -m pytest -q
```

## Running the demo script

```bash
.venv/bin/python demo/demo.py
```

## Mutation-testing pipeline (CLI)

```bash
# Run a single hand-written twin through the harness
.venv/bin/python harness/twin_runner.py --patch demo/hand_twins/obvious.patch --run-dir runs/dev

# Forge + evaluate 8 AI-generated twins (needs GEMINI_API_KEY in .env)
.venv/bin/python agents/forger.py --n 8 --run-dir runs/dev

# Find a concrete input where a surviving twin diverges from the real ledger
.venv/bin/python harness/witness.py --twin runs/dev/twins/twin_06

# Narrate a completed run's deterministic artifacts (additive; needs GEMINI_API_KEY)
.venv/bin/python agents/narrator.py --run-dir runs/dev
```

## Web UI (the 4 demo screens)

```bash
.venv/bin/python -m uvicorn web.app:app --port 8000
```

Then open `http://localhost:8000/review`. The flow is a single "run" at a
time, state held in-memory plus `runs/<id>/` files on disk:

1. **`/review`** — the "AI-generated PR": 3 hardcoded diff hunks (idempotency,
   rounding, redeem check). Click "Verified" on all three to unlock Merge.
2. **`/forge`** — on Merge, the backend forges 8 twins with Gemini, runs each
   through `twin_runner.py`, then runs `witness.py` on every survivor.
   Progress streams live over SSE, and once the run finishes the Narrator
   agent adds one plain-English line per twin. "Load rehearsal twins" on the
   review screen runs the identical pipeline from `demo/rehearsal_twins/`
   instead of calling Gemini — the harness and witness finder still run
   live, so this path (and the whole UI) works with Wi-Fi off; only the
   Narrator's line is skipped.
3. **`/probe`** — for the surviving twin with a witness, guess which of two
   outputs your own (verified) code actually produces for the witness input.
4. **`/verdict`** — per-hunk ledger (proven / could-not-defend / untested)
   plus the score panel (`Override Value`, `Probe Defense`, `Symbiosis
   Index`), the final `KINIT VERIFIED` / `UNVERIFIED` badge, and the
   Narrator's 3-sentence judgment report. Writes `runs/<id>/ledger.json`.
   "Show artifacts" lists every file the run produced.

