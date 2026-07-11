"""Tests for the loyalty points settlement service.

Scope note: this suite covers the core documented behaviors (earn, tier
multipliers, redeem, insufficient balance, duplicate rejection, invalid
amounts, batches, immutability). It intentionally does NOT cover:
  * rounding behavior exactly at .5 boundaries,
  * the "retry" flag on events,
  * interleaved duplicate + redeem sequences.
Those are known gaps, not oversights.
"""

import copy

from ledger import new_state, settle_batch, settle_event


def make_event(event_id, user, type_, amount, tier="basic", retry=False):
    return {
        "event_id": event_id,
        "user": user,
        "type": type_,
        "amount": amount,
        "tier": tier,
        "retry": retry,
    }


def test_new_state_shape():
    state = new_state()
    assert state == {"balances": {}, "processed_events": {}, "history": []}


def test_basic_earn_credits_balance_at_1x():
    state = new_state()
    event = make_event("e1", "alice", "earn", 10, tier="basic")

    new, result = settle_event(state, event)

    assert result["status"] == "earned"
    assert result["points"] == 10
    assert new["balances"]["alice"] == 10


def test_silver_tier_earn_applies_1_5x_multiplier():
    state = new_state()
    event = make_event("e1", "alice", "earn", 10, tier="silver")

    new, result = settle_event(state, event)

    assert result["status"] == "earned"
    assert result["points"] == 15
    assert new["balances"]["alice"] == 15


def test_gold_tier_earn_applies_2x_multiplier():
    state = new_state()
    event = make_event("e1", "alice", "earn", 10, tier="gold")

    new, result = settle_event(state, event)

    assert result["status"] == "earned"
    assert result["points"] == 20
    assert new["balances"]["alice"] == 20


def test_redeem_success_deducts_balance():
    state = new_state()
    state, _ = settle_event(state, make_event("e1", "alice", "earn", 100, tier="basic"))

    state, result = settle_event(state, make_event("e2", "alice", "redeem", 40))

    assert result["status"] == "redeemed"
    assert result["points"] == 40
    assert state["balances"]["alice"] == 60


def test_redeem_of_exact_balance_succeeds_and_zeroes_out():
    state = new_state()
    state, _ = settle_event(state, make_event("e1", "alice", "earn", 25, tier="basic"))

    state, result = settle_event(state, make_event("e2", "alice", "redeem", 25))

    assert result["status"] == "redeemed"
    assert state["balances"]["alice"] == 0


def test_redeem_with_insufficient_balance_fails_without_mutating():
    state = new_state()
    state, _ = settle_event(state, make_event("e1", "alice", "earn", 10, tier="basic"))

    state, result = settle_event(state, make_event("e2", "alice", "redeem", 50))

    assert result["status"] == "insufficient"
    assert state["balances"]["alice"] == 10


def test_duplicate_event_id_is_rejected_and_state_unchanged():
    state = new_state()
    state, first_result = settle_event(state, make_event("e1", "alice", "earn", 10, tier="basic"))

    state, second_result = settle_event(state, make_event("e1", "alice", "earn", 10, tier="basic"))

    assert first_result["status"] == "earned"
    assert second_result["status"] == "duplicate"
    assert state["balances"]["alice"] == 10


def test_duplicate_rejection_holds_even_with_different_payload():
    state = new_state()
    state, _ = settle_event(state, make_event("e1", "alice", "earn", 10, tier="basic"))

    state, result = settle_event(state, make_event("e1", "alice", "earn", 999, tier="gold"))

    assert result["status"] == "duplicate"
    assert state["balances"]["alice"] == 10


def test_zero_amount_is_invalid():
    state = new_state()

    new, result = settle_event(state, make_event("e1", "alice", "earn", 0, tier="basic"))

    assert result["status"] == "invalid"
    assert new["balances"].get("alice", 0) == 0


def test_negative_amount_is_invalid():
    state = new_state()

    new, result = settle_event(state, make_event("e1", "alice", "redeem", -5))

    assert result["status"] == "invalid"
    assert new["balances"].get("alice", 0) == 0


def test_settle_batch_processes_events_in_order_across_users():
    state = new_state()
    events = [
        make_event("e1", "alice", "earn", 10, tier="basic"),
        make_event("e2", "alice", "earn", 10, tier="silver"),
        make_event("e3", "alice", "redeem", 5),
        make_event("e4", "bob", "earn", 10, tier="gold"),
    ]

    final_state, results = settle_batch(state, events)

    assert [r["status"] for r in results] == ["earned", "earned", "redeemed", "earned"]
    assert final_state["balances"]["alice"] == 20
    assert final_state["balances"]["bob"] == 20


def test_settle_event_never_mutates_input_state():
    state = new_state()
    state, _ = settle_event(state, make_event("e1", "alice", "earn", 10, tier="basic"))
    snapshot = copy.deepcopy(state)

    settle_event(state, make_event("e2", "alice", "redeem", 5))

    assert state == snapshot
