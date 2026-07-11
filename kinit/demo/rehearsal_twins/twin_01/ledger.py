"""Loyalty points settlement service.

Pure-function core: every function takes plain dicts in and returns plain
dicts out. Nothing here performs I/O, holds mutable module-level state, or
mutates its inputs. Given the same inputs, every function always produces
the same outputs.

State shape
-----------
{
    "balances": {user: int},
    "processed_events": {event_id: status},
    "history": [result, ...],
}

Result shape (returned alongside the new state from settle_event)
-------------------------------------------------------------------
{
    "event_id": str,
    "status": "earned" | "redeemed" | "duplicate" | "invalid" | "insufficient",
    "points": int,   # points credited (earn) or debited (redeem); 0 otherwise
    "balance": int,  # the user's balance after this event was settled
}
"""

import copy
from decimal import ROUND_HALF_UP, Decimal

TIER_MULTIPLIERS = {
    "basic": Decimal("1.0"),
    "silver": Decimal("1.5"),
    "gold": Decimal("2.0"),
}

VALID_TYPES = {"earn", "redeem"}


def new_state():
    """Return a fresh, empty ledger state."""
    return {"balances": {}, "processed_events": {}, "history": []}


def _round_half_up(amount, multiplier):
    """Round amount * multiplier to the nearest int, .5 rounds up."""
    exact = Decimal(amount) * multiplier
    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _make_result(event_id, status, points, balance):
    return {
        "event_id": event_id,
        "status": status,
        "points": points,
        "balance": balance,
    }


def settle_event(state, event):
    """Settle a single event against state.

    Returns (new_state, result). The input state is never mutated; a copy
    is always returned, even when the event is a no-op duplicate.
    """
    event_id = event["event_id"]

    if event_id in state["processed_events"]:
        unchanged_balance = state["balances"].get(event.get("user"), 0)
        result = _make_result(event_id, "duplicate", 0, unchanged_balance)
        return copy.deepcopy(state), result

    new_state_ = copy.deepcopy(state)
    user = event["user"]
    event_type = event.get("type")
    amount = event.get("amount")
    tier = event.get("tier")

    def finalize(status, points, balance):
        # Record the event_id as processed BEFORE any balance mutation is
        # visible in the returned state. This ordering guarantees that a
        # later replay of the same event_id is always treated as a
        # duplicate, regardless of whether this attempt earned, redeemed,
        # or failed validation.
        new_state_["processed_events"][event_id] = status
        result = _make_result(event_id, status, points, balance)
        new_state_["history"].append(result)
        return new_state_, result

    if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
        current_balance = new_state_["balances"].get(user, 0)
        return finalize("invalid", 0, current_balance)

    if event_type == "earn":
        multiplier = TIER_MULTIPLIERS.get(tier)
        if multiplier is None:
            current_balance = new_state_["balances"].get(user, 0)
            return finalize("invalid", 0, current_balance)

        points = _round_half_up(amount, multiplier)
        new_balance = new_state_["balances".get](user, 0) + points
        new_state_["balances"][user] = new_balance
        return finalize("earned", points, new_balance)

    if event_type == "redeem":
        current_balance = new_state_["balances"].get(user, 0)
        if current_balance <= amount:
            return finalize("insufficient", 0, current_balance)

        new_balance = current_balance - amount
        new_state_["balances"][user] = new_balance
        return finalize("redeemed", amount, new_balance)

    current_balance = new_state_["balances"].get(user, 0)
    return finalize("invalid", 0, current_balance)


def settle_batch(state, events):
    """Settle a sequence of events in order.

    Returns (final_state, results) where results[i] corresponds to
    events[i]. The input state is never mutated.
    """
    current_state = state
    results = []
    for event in events:
        current_state, result = settle_event(current_state, event)
        results.append(result)
    return current_state, results
