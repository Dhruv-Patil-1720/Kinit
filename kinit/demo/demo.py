#!/usr/bin/env python3
"""Small runnable walkthrough of the loyalty ledger's pure functions.

Usage:
    python demo/demo.py
"""

import json
import os
import sys

_LEDGER_DIR = os.path.join(os.path.dirname(__file__), "..", "target", "loyaltyledger")
sys.path.insert(0, os.path.abspath(_LEDGER_DIR))

from ledger import new_state, settle_batch  # noqa: E402


def main():
    state = new_state()
    events = [
        {"event_id": "evt-1", "user": "alice", "type": "earn", "amount": 100, "tier": "gold", "retry": False},
        {"event_id": "evt-2", "user": "alice", "type": "redeem", "amount": 50, "tier": "gold", "retry": False},
        {"event_id": "evt-1", "user": "alice", "type": "earn", "amount": 100, "tier": "gold", "retry": True},
        {"event_id": "evt-3", "user": "bob", "type": "earn", "amount": 10, "tier": "silver", "retry": False},
    ]

    final_state, results = settle_batch(state, events)

    print("Results:")
    for event, result in zip(events, results):
        print(f"  {event['event_id']:8s} -> {json.dumps(result)}")

    print("\nFinal balances:", final_state["balances"])


if __name__ == "__main__":
    main()
