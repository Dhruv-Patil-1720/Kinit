# kinit — loyaltyledger

A tiny, pure-functional loyalty points settlement service.

Requires **Python 3.11+**.

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
  demo/
    demo.py              # small runnable walkthrough
    hand_twins/           # two hand-written patches used to sanity-check the harness
    rehearsal_twins/       # a saved forger run, used as the offline/no-Wi-Fi demo fallback
  web/
    app.py                # FastAPI backend for the 4 demo screens
    static/                # plain HTML/CSS/JS (no framework, no build step)
  runs/                   # generated run artifacts (gitignored, kept via .gitkeep)
```

## `ledger.py`

No I/O, no classes, no mutation of inputs. Every function returns a fresh
copy of state.

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

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Running the tests

```bash
cd target/loyaltyledger
../../.venv/bin/python -m pytest -q
```

## Running the demo

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
   Progress streams live over SSE. "Load rehearsal twins" on the review
   screen runs the identical pipeline from `demo/rehearsal_twins/` instead of
   calling Gemini — the harness and witness finder still run live, so this
   path works with Wi-Fi off.
3. **`/probe`** — for the surviving twin with a witness, guess which of two
   outputs your own (verified) code actually produces for the witness input.
4. **`/verdict`** — per-hunk ledger (proven / could-not-defend / untested)
   plus the score panel (`Override Value`, `Probe Defense`, `Symbiosis
   Index`) and the final `KINIT VERIFIED` / `UNVERIFIED` badge. Writes
   `runs/<id>/ledger.json`. "Show artifacts" lists every file the run
   produced.
