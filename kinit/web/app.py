"""web/app.py — the four demo screens (review -> forge -> probe -> verdict).

Vanilla HTML/JS + FastAPI + SSE. No frontend framework, no build step: every
page in web/static/ is a static file, and all dynamic content is fetched by
plain JS (fetch + EventSource) from the small JSON API defined here.

State lives in one module-level "current run" object plus runs/<id>/ files
on disk (twins/, verdicts.jsonl, witness.json, ledger.json) — this app only
ever tracks a single run at a time, matching a single hackathon demo table.

Run with: uvicorn web.app:app --port 8000
"""

import json
import os
import queue
import random
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

WEB_DIR = Path(__file__).resolve().parent
REPO_ROOT = WEB_DIR.parent
STATIC_DIR = WEB_DIR / "static"
REHEARSAL_DIR = REPO_ROOT / "demo" / "rehearsal_twins"
RUNS_DIR = REPO_ROOT / "runs"


def _load_dotenv(path: Path):
    """Tiny .env loader so `uvicorn web.app:app` picks up GEMINI_API_KEY
    without requiring the shell to `export` it first. Never overrides a
    variable already set in the real environment."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")

sys.path.insert(0, str(REPO_ROOT / "harness"))
sys.path.insert(0, str(REPO_ROOT / "agents"))
import twin_runner  # noqa: E402
import witness as witness_mod  # noqa: E402
import forger  # noqa: E402

HUNKS_ORDER = ["idempotency", "rounding", "redeem_check"]

# Maps a forger bug_family to the review hunk it actually lives in, so the
# verdict screen can tie a probe result back to a specific "Verified" claim.
HUNK_BUG_FAMILIES = {
    "idempotency": {"order-of-operations", "rare-path"},
    "rounding": {"rounding"},
    "redeem_check": {"boundary"},
}

HUNKS = [
    {
        "id": "idempotency",
        "title": "Idempotent event settlement",
        "file": "target/loyaltyledger/ledger.py",
        "hunk_header": "@@ settle_event: duplicate check + finalize() @@",
        "lines": [
            {"t": "ctx", "s": "def settle_event(state, event):"},
            {"t": "ctx", "s": '    event_id = event["event_id"]'},
            {"t": "add", "s": ""},
            {"t": "add", "s": '    if event_id in state["processed_events"]:'},
            {"t": "add", "s": '        unchanged_balance = state["balances"].get(event.get("user"), 0)'},
            {"t": "add", "s": '        result = _make_result(event_id, "duplicate", 0, unchanged_balance)'},
            {"t": "add", "s": "        return copy.deepcopy(state), result"},
            {"t": "add", "s": ""},
            {"t": "add", "s": "    def finalize(status, points, balance):"},
            {"t": "add", "s": '        new_state_["processed_events"][event_id] = status'},
            {"t": "add", "s": "        result = _make_result(event_id, status, points, balance)"},
            {"t": "add", "s": '        new_state_["history"].append(result)'},
            {"t": "add", "s": "        return new_state_, result"},
        ],
        "caption": (
            "Every event_id gets recorded as processed BEFORE we return \u2014 for every "
            "outcome, not just successes \u2014 so a retried event_id always short-circuits "
            "to \u2018duplicate\u2019, even one that previously failed."
        ),
    },
    {
        "id": "rounding",
        "title": "Tier multipliers & rounding",
        "file": "target/loyaltyledger/ledger.py",
        "hunk_header": "@@ TIER_MULTIPLIERS + _round_half_up @@",
        "lines": [
            {"t": "add", "s": "TIER_MULTIPLIERS = {"},
            {"t": "add", "s": '    "basic": Decimal("1.0"),'},
            {"t": "add", "s": '    "silver": Decimal("1.5"),'},
            {"t": "add", "s": '    "gold": Decimal("2.0"),'},
            {"t": "add", "s": "}"},
            {"t": "add", "s": ""},
            {"t": "add", "s": "def _round_half_up(amount, multiplier):"},
            {"t": "add", "s": '    """Round amount * multiplier to the nearest int, .5 rounds up."""'},
            {"t": "add", "s": "    exact = Decimal(amount) * multiplier"},
            {"t": "add", "s": '    return int(exact.quantize(Decimal("1"), rounding=ROUND_HALF_UP))'},
        ],
        "caption": (
            "Points = amount \u00d7 tier multiplier, rounded half-up via Decimal \u2014 not "
            "float math, not int() truncation."
        ),
    },
    {
        "id": "redeem_check",
        "title": "Redeem: insufficient-balance guard",
        "file": "target/loyaltyledger/ledger.py",
        "hunk_header": "@@ settle_event: redeem branch @@",
        "lines": [
            {"t": "add", "s": '    if event_type == "redeem":'},
            {"t": "add", "s": '        current_balance = new_state_["balances"].get(user, 0)'},
            {"t": "add", "s": "        if current_balance < amount:"},
            {"t": "add", "s": '            return finalize("insufficient", 0, current_balance)'},
            {"t": "add", "s": ""},
            {"t": "add", "s": "        new_balance = current_balance - amount"},
            {"t": "add", "s": '        new_state_["balances"][user] = new_balance'},
            {"t": "add", "s": '        return finalize("redeemed", amount, new_balance)'},
        ],
        "caption": (
            "Strict `<` \u2014 redeeming exactly your full balance succeeds and zeroes it "
            "out; only a shortfall is \u2018insufficient\u2019."
        ),
    },
]
assert [h["id"] for h in HUNKS] == HUNKS_ORDER


class ForgeErrorCandidate(Exception):
    pass


class RunState:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self.dir = RUNS_DIR / run_id
        self.lock = threading.Lock()

        self.hunks = {h: None for h in HUNKS_ORDER}  # hunk_id -> elapsed_ms once verified
        self.merged = False
        self.rehearsal = False
        self.pipeline_status = "idle"  # idle | running | done

        self.twins = []  # list of twin summary dicts, in forge order
        self.killed = 0
        self.survived = 0
        self.invalid = 0

        self.probe_twin_id = None
        self.probe_description = None
        self.probe_bug_family = None
        self.probe_events = None
        self.probe_real_output = None
        self.probe_twin_output = None
        self.probe_a_is_real = None
        self.probe_choice = None
        self.probe_correct = None

        self.events_log = []
        self.subscribers = []

    def emit(self, event: dict):
        event = {"ts": time.time(), **event}
        with self.lock:
            self.events_log.append(event)
            for q in self.subscribers:
                q.put(event)

    def subscribe(self):
        with self.lock:
            q = queue.Queue()
            self.subscribers.append(q)
            replay = list(self.events_log)
        return q, replay

    def record_twin(self, summary: dict):
        with self.lock:
            self.twins.append(summary)
            if summary["status"] == "killed":
                self.killed += 1
            elif summary["status"] == "survived":
                self.survived += 1
            else:
                self.invalid += 1

    def counts(self):
        return {
            "forged": len(self.twins),
            "killed": self.killed,
            "survived": self.survived,
            "invalid": self.invalid,
        }

    def set_probe(self, twin_id, description, bug_family, witness):
        self.probe_twin_id = twin_id
        self.probe_description = description
        self.probe_bug_family = bug_family
        self.probe_events = witness["input_events"]
        self.probe_real_output = witness["real_output"]
        self.probe_twin_output = witness["twin_output"]
        self.probe_a_is_real = random.choice([True, False])


CURRENT: Optional[RunState] = None
_current_lock = threading.Lock()


def get_current_run() -> RunState:
    if CURRENT is None:
        raise HTTPException(400, "No active run yet \u2014 load /review first")
    return CURRENT


def new_run() -> RunState:
    global CURRENT
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    run = RunState(run_id)
    os.makedirs(run.dir, exist_ok=True)
    with _current_lock:
        CURRENT = run
    return run


# --------------------------------------------------------------------------
# Twin pipeline (runs in a background thread, streams progress via SSE)
# --------------------------------------------------------------------------


def _iter_rehearsal_candidates():
    if not REHEARSAL_DIR.is_dir():
        return
    for name in sorted(os.listdir(REHEARSAL_DIR)):
        src_dir = REHEARSAL_DIR / name
        if not src_dir.is_dir():
            continue
        meta = {}
        meta_path = src_dir / "meta.json"
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                meta = {}
        yield {
            "twin_id": name,
            "description": meta.get("description", "(rehearsal twin, no description)"),
            "bug_family": meta.get("bug_family", "unknown"),
            "backend": meta.get("backend", "rehearsal:demo/rehearsal_twins"),
            "divergence_hint": meta.get("divergence_hint"),
            "src_dir": src_dir,
            "forge_error": None,
        }


def _iter_live_candidates(n=8):
    source_text = Path(forger.DEFAULT_SOURCE).read_text()
    hunk_range = forger._default_hunk_range(source_text)
    client = forger.genai.Client()

    for i in range(n):
        twin_id = f"twin_{i:02d}"
        bug_family = forger.BUG_FAMILIES[i % len(forger.BUG_FAMILIES)]
        user_message = forger._build_user_message(source_text, hunk_range, bug_family)
        try:
            twin, backend, _attempts = forger._forge_one(client, user_message, 1.0, True)
        except forger.ForgeError as exc:
            yield {
                "twin_id": twin_id,
                "description": f"forge failed: {exc}",
                "bug_family": bug_family,
                "backend": None,
                "divergence_hint": None,
                "src_dir": None,
                "twin_source": None,
                "forge_error": exc,
            }
            continue
        yield {
            "twin_id": twin_id,
            "description": twin["description"],
            "bug_family": bug_family,
            "backend": backend,
            "divergence_hint": twin.get("divergence_hint"),
            "src_dir": None,
            "twin_source": twin["twin_source"],
            "forge_error": None,
        }


def run_pipeline(run: RunState):
    twins_dir = run.dir / "twins"
    os.makedirs(twins_dir, exist_ok=True)
    run.emit({"type": "pipeline_start", "rehearsal": run.rehearsal})

    candidates = _iter_rehearsal_candidates() if run.rehearsal else _iter_live_candidates(n=8)

    for candidate in candidates:
        twin_id = candidate["twin_id"]
        run.emit(
            {
                "type": "twin_spawned",
                "twin_id": twin_id,
                "description": candidate["description"],
                "bug_family": candidate["bug_family"],
            }
        )

        if candidate["forge_error"] is not None:
            verdict = twin_runner._finalize(str(run.dir), twin_id, "invalid_patch", 0, 0, 0.0)
        else:
            twin_dir = twins_dir / twin_id
            os.makedirs(twin_dir, exist_ok=True)
            ledger_path = twin_dir / "ledger.py"

            if run.rehearsal:
                shutil.copyfile(candidate["src_dir"] / "ledger.py", ledger_path)
                src_meta = candidate["src_dir"] / "meta.json"
                if src_meta.is_file():
                    shutil.copyfile(src_meta, twin_dir / "meta.json")
            else:
                ledger_path.write_text(candidate["twin_source"])
                (twin_dir / "meta.json").write_text(
                    json.dumps(
                        {
                            "description": candidate["description"],
                            "bug_family": candidate["bug_family"],
                            "backend": candidate["backend"],
                            "divergence_hint": candidate["divergence_hint"],
                        },
                        indent=2,
                    )
                )

            verdict = twin_runner.run_twin_from_source(str(ledger_path), str(run.dir), twin_id=twin_id)

        summary = {
            "twin_id": twin_id,
            "description": candidate["description"],
            "bug_family": candidate["bug_family"],
            "backend": candidate["backend"],
            "status": verdict["status"],
            "tests_passed": verdict.get("tests_passed"),
            "tests_failed": verdict.get("tests_failed"),
            "has_witness": False,
        }
        run.record_twin(summary)
        run.emit(
            {
                "type": "twin_result",
                "twin_id": twin_id,
                "status": verdict["status"],
                "tests_passed": verdict.get("tests_passed"),
                "tests_failed": verdict.get("tests_failed"),
            }
        )

    for t in run.twins:
        if t["status"] != "survived":
            continue
        twin_dir = twins_dir / t["twin_id"]
        w = witness_mod.find_witness(str(twin_dir), timeout_s=45)
        if w is None:
            continue
        t["has_witness"] = True
        run.emit({"type": "witness_found", "twin_id": t["twin_id"], "first_divergence": w["first_divergence"]})
        if run.probe_twin_id is None:
            run.set_probe(t["twin_id"], t["description"], t["bug_family"], w)

    run.pipeline_status = "done"
    run.emit({"type": "pipeline_done", "counts": run.counts(), "probe_twin_id": run.probe_twin_id})


# --------------------------------------------------------------------------
# App + static pages
# --------------------------------------------------------------------------

app = FastAPI(title="kinit demo")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
def root():
    return RedirectResponse("/review")


@app.get("/review")
def review_page():
    new_run()
    return FileResponse(str(STATIC_DIR / "review.html"))


@app.get("/forge")
def forge_page():
    return FileResponse(str(STATIC_DIR / "forge.html"))


@app.get("/probe")
def probe_page():
    return FileResponse(str(STATIC_DIR / "probe.html"))


@app.get("/verdict")
def verdict_page():
    return FileResponse(str(STATIC_DIR / "verdict.html"))


# --------------------------------------------------------------------------
# API: review
# --------------------------------------------------------------------------


@app.get("/api/hunks")
def api_hunks():
    return {"hunks": HUNKS}


class VerifyPayload(BaseModel):
    hunk_id: str
    elapsed_ms: float


@app.post("/api/verify")
def api_verify(payload: VerifyPayload):
    run = get_current_run()
    if payload.hunk_id not in run.hunks:
        raise HTTPException(400, f"unknown hunk_id {payload.hunk_id!r}")
    run.hunks[payload.hunk_id] = payload.elapsed_ms
    return {"hunks": run.hunks, "all_verified": all(v is not None for v in run.hunks.values())}


@app.get("/api/review_status")
def api_review_status():
    run = get_current_run()
    return {
        "run_id": run.run_id,
        "hunks": run.hunks,
        "all_verified": all(v is not None for v in run.hunks.values()),
        "merged": run.merged,
        "pipeline_status": run.pipeline_status,
    }


# --------------------------------------------------------------------------
# API: forge
# --------------------------------------------------------------------------


class MergePayload(BaseModel):
    rehearsal: bool = False


@app.post("/api/merge")
def api_merge(payload: MergePayload):
    run = get_current_run()
    if not all(v is not None for v in run.hunks.values()):
        raise HTTPException(400, "all 3 hunks must be verified before merging")

    with run.lock:
        if run.pipeline_status != "idle":
            return {"status": run.pipeline_status, "run_id": run.run_id}
        run.merged = True
        run.rehearsal = bool(payload.rehearsal)
        run.pipeline_status = "running"

    thread = threading.Thread(target=run_pipeline, args=(run,), daemon=True)
    thread.start()
    return {"status": "running", "run_id": run.run_id, "rehearsal": run.rehearsal}


@app.get("/api/status")
def api_status():
    run = get_current_run()
    return {
        "run_id": run.run_id,
        "pipeline_status": run.pipeline_status,
        "rehearsal": run.rehearsal,
        "counts": run.counts(),
        "twins": run.twins,
        "probe_ready": run.probe_twin_id is not None,
    }


def _sse_format(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


@app.get("/events")
def sse_events():
    run = get_current_run()
    q, replay = run.subscribe()

    def gen():
        for ev in replay:
            yield _sse_format(ev)
        while True:
            try:
                ev = q.get(timeout=1.0)
            except queue.Empty:
                if run.pipeline_status == "done":
                    return
                yield ": heartbeat\n\n"
                continue
            yield _sse_format(ev)
            if ev.get("type") == "pipeline_done":
                return

    return StreamingResponse(gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# API: probe
# --------------------------------------------------------------------------


@app.get("/api/probe_data")
def api_probe_data():
    run = get_current_run()
    if run.probe_twin_id is None:
        return {"available": False, "pipeline_status": run.pipeline_status}

    option_a = run.probe_real_output if run.probe_a_is_real else run.probe_twin_output
    option_b = run.probe_twin_output if run.probe_a_is_real else run.probe_real_output

    return {
        "available": True,
        "twin_id": run.probe_twin_id,
        "description": run.probe_description,
        "input_events": run.probe_events,
        "option_a": option_a,
        "option_b": option_b,
        "already_answered": run.probe_choice is not None,
        "choice": run.probe_choice,
        "correct": run.probe_correct,
    }


class ProbeChoicePayload(BaseModel):
    choice: str  # "A" or "B"


@app.post("/api/probe_choice")
def api_probe_choice(payload: ProbeChoicePayload):
    run = get_current_run()
    if run.probe_twin_id is None:
        raise HTTPException(400, "no probe available for this run")
    choice = payload.choice.strip().upper()
    if choice not in ("A", "B"):
        raise HTTPException(400, "choice must be 'A' or 'B'")

    is_real_chosen = (choice == "A") == bool(run.probe_a_is_real)
    run.probe_choice = choice
    run.probe_correct = is_real_chosen
    return {"choice": choice, "correct": is_real_chosen}


# --------------------------------------------------------------------------
# API: verdict
# --------------------------------------------------------------------------


@app.get("/api/verdict")
def api_verdict():
    run = get_current_run()

    total_valid = run.killed + run.survived
    override_value = 40 * (run.killed / total_valid) if total_valid else 0.0

    if run.probe_correct is None:
        probe_defense = 10
    else:
        probe_defense = 50 if run.probe_correct else 10

    baseline = 10
    symbiosis_index = round(override_value + probe_defense + baseline)
    badge = "KINIT VERIFIED" if symbiosis_index >= 82 else "UNVERIFIED \u2014 judgment gap found"

    hunks_ledger = {}
    for hunk_id in HUNKS_ORDER:
        if run.hunks.get(hunk_id) is None:
            hunks_ledger[hunk_id] = "untested"
            continue
        related = run.probe_bug_family in HUNK_BUG_FAMILIES.get(hunk_id, set())
        if related and run.probe_correct is False:
            hunks_ledger[hunk_id] = "claimed"
        else:
            hunks_ledger[hunk_id] = "proven"

    result = {
        "run_id": run.run_id,
        "rehearsal": run.rehearsal,
        "hunks": hunks_ledger,
        "hunk_elapsed_ms": run.hunks,
        "twins_summary": {
            "total": len(run.twins),
            "killed": run.killed,
            "survived": run.survived,
            "invalid": run.invalid,
            "total_valid": total_valid,
        },
        "probe": {
            "twin_id": run.probe_twin_id,
            "bug_family": run.probe_bug_family,
            "choice": run.probe_choice,
            "correct": run.probe_correct,
        },
        "score": {
            "override_value": round(override_value, 1),
            "probe_defense": probe_defense,
            "baseline": baseline,
            "symbiosis_index": symbiosis_index,
        },
        "badge": badge,
    }

    with open(run.dir / "ledger.json", "w") as fh:
        json.dump(result, fh, indent=2)

    return result


@app.get("/api/artifacts")
def api_artifacts():
    run = get_current_run()
    paths = []

    twins_dir = run.dir / "twins"
    if twins_dir.is_dir():
        for name in sorted(os.listdir(twins_dir)):
            twin_dir = twins_dir / name
            if not twin_dir.is_dir():
                continue
            for fname in ("ledger.py", "meta.json", "witness.json"):
                fpath = twin_dir / fname
                if fpath.is_file():
                    paths.append(str(fpath.relative_to(REPO_ROOT)))

    for fname in ("verdicts.jsonl", "ledger.json"):
        fpath = run.dir / fname
        if fpath.is_file():
            paths.append(str(fpath.relative_to(REPO_ROOT)))

    return {"run_dir": str(run.dir.relative_to(REPO_ROOT)), "paths": paths}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("web.app:app", host="127.0.0.1", port=8000, reload=False)
