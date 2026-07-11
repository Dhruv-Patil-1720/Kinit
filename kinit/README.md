# kinit — loyaltyledger

A tiny, pure-functional loyalty points settlement service.

Requires **Python 3.11+**.

## Layout

```
kinit/
  target/loyaltyledger/
    ledger.py            # pure-function settlement core (dict in -> dict out)
    tests/test_ledger.py # pytest suite
  runs/                  # generated run artifacts (gitignored, kept via .gitkeep)
  demo/
    demo.py              # small runnable walkthrough
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
