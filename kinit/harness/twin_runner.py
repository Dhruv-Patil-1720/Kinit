"""Twin runner: the deterministic mutation-testing harness.

Given a "twin" — either a unified diff against target/loyaltyledger/ or a
full replacement ledger.py source file — this module:

1. Copies target/loyaltyledger/ into an isolated temp directory.
2. Applies the twin's mutation to that copy (diff apply, or file swap).
3. Sanity-checks the mutated copy still imports (`python -c "import ledger"`).
4. Runs the copy's own pytest suite with a timeout.
5. Emits a verdict: "killed" (tests caught the mutation), "survived"
   (tests passed anyway — a coverage gap), or "invalid_patch" (the twin
   didn't even apply/import).

Every verdict is appended to <run_dir>/verdicts.jsonl and also returned as a
dict, so downstream tooling (the forger, a demo script) can consume either
the file or the return value.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

HARNESS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HARNESS_DIR)
TARGET_DIR = os.path.join(REPO_ROOT, "target", "loyaltyledger")

IMPORT_TIMEOUT_S = 15
TEST_TIMEOUT_S = 60

_IGNORE_PATTERNS = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache")

_SUMMARY_RE = {
    "passed": re.compile(r"(\d+) passed"),
    "failed": re.compile(r"(\d+) failed"),
    "error": re.compile(r"(\d+) error"),
}


def _new_twin_id(prefix, source_path):
    stem = os.path.splitext(os.path.basename(source_path))[0]
    return f"{prefix}-{stem}-{uuid.uuid4().hex[:8]}"


def _copy_target(dest_dir):
    shutil.copytree(TARGET_DIR, dest_dir, ignore=_IGNORE_PATTERNS)


def _apply_patch(patch_path, copy_dir):
    """Apply a unified diff to copy_dir. Tries `git apply` first (handles
    both `a/`/`b/` prefixed and bare paths robustly), falls back to the
    classic `patch -p1` if git isn't cooperating."""
    patch_path = os.path.abspath(patch_path)

    proc = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-p1", patch_path],
        cwd=copy_dir,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        return True

    try:
        with open(patch_path, "rb") as fh:
            patch_bytes = fh.read()
        proc2 = subprocess.run(
            ["patch", "-p1", "--batch", "--forward"],
            cwd=copy_dir,
            input=patch_bytes,
            capture_output=True,
        )
        return proc2.returncode == 0
    except FileNotFoundError:
        return False


def _sanity_import(copy_dir):
    try:
        proc = subprocess.run(
            [sys.executable, "-c", "import ledger"],
            cwd=copy_dir,
            capture_output=True,
            text=True,
            timeout=IMPORT_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def _parse_summary(stdout):
    passed = int(m.group(1)) if (m := _SUMMARY_RE["passed"].search(stdout)) else 0
    failed = int(m.group(1)) if (m := _SUMMARY_RE["failed"].search(stdout)) else 0
    failed += int(m.group(1)) if (m := _SUMMARY_RE["error"].search(stdout)) else 0
    return passed, failed


def _run_tests(copy_dir):
    start = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no"],
            cwd=copy_dir,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return 0, 0, duration, False

    duration = time.monotonic() - start
    passed, failed = _parse_summary(proc.stdout)
    return passed, failed, duration, True


def _evaluate(copy_dir, twin_id, run_dir):
    start = time.monotonic()

    if not _sanity_import(copy_dir):
        return _finalize(run_dir, twin_id, "invalid_patch", 0, 0, time.monotonic() - start)

    passed, failed, duration, completed = _run_tests(copy_dir)
    if not completed:
        return _finalize(run_dir, twin_id, "invalid_patch", passed, failed, duration)

    status = "survived" if failed == 0 else "killed"
    return _finalize(run_dir, twin_id, status, passed, failed, duration)


def _finalize(run_dir, twin_id, status, tests_passed, tests_failed, duration_s):
    verdict = {
        "twin_id": twin_id,
        "status": status,
        "tests_passed": tests_passed,
        "tests_failed": tests_failed,
        "duration_s": round(duration_s, 3),
    }
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "verdicts.jsonl"), "a") as fh:
        fh.write(json.dumps(verdict) + "\n")
    return verdict


def run_twin(patch_path, run_dir, twin_id=None):
    """Apply the unified diff at patch_path to an isolated copy of
    target/loyaltyledger/, test it, and return the verdict dict."""
    twin_id = twin_id or _new_twin_id("patch", patch_path)

    with tempfile.TemporaryDirectory(prefix="twin_") as tmp:
        copy_dir = os.path.join(tmp, "loyaltyledger")
        _copy_target(copy_dir)

        start = time.monotonic()
        if not _apply_patch(patch_path, copy_dir):
            return _finalize(run_dir, twin_id, "invalid_patch", 0, 0, time.monotonic() - start)

        return _evaluate(copy_dir, twin_id, run_dir)


def run_twin_from_source(source_path, run_dir, twin_id=None):
    """Evaluate a twin given as a full replacement ledger.py file (used by
    the forger, which generates whole files rather than diffs)."""
    twin_id = twin_id or _new_twin_id("src", source_path)

    with tempfile.TemporaryDirectory(prefix="twin_") as tmp:
        copy_dir = os.path.join(tmp, "loyaltyledger")
        _copy_target(copy_dir)
        shutil.copyfile(source_path, os.path.join(copy_dir, "ledger.py"))

        return _evaluate(copy_dir, twin_id, run_dir)


def main():
    parser = argparse.ArgumentParser(description="Run a single twin through the harness.")
    parser.add_argument("--patch", required=True, help="Path to a unified diff to apply.")
    parser.add_argument("--run-dir", required=True, help="Directory to write verdicts.jsonl into.")
    args = parser.parse_args()

    verdict = run_twin(args.patch, args.run_dir)
    print(json.dumps(verdict))


if __name__ == "__main__":
    main()
