# How Kinit actually works — plain-English walkthrough

This document exists so you never have to wonder "wait, is this real or is
this fake/hardcoded?" while looking at any screen. Every claim below is
checked against the actual code, not guessed.

---

## 1. The one-sentence idea

You clicked "Verified" on some AI-written code. Kinit's job is to find out
if you were *actually right* — by generating broken copies of that code,
seeing if your tests catch the breakage, and then, for the one bug that
slips through, making **you** personally predict what your own code does.

That's it. Everything else is machinery to make that fair and provable.

---

## 2. Glossary — every word you'll see on screen, defined once

| Term | What it means, in plain words |
|---|---|
| **Hunk** | A small chunk of code shown in a diff (the +/- block). We show you 3 fixed hunks of `ledger.py` to review. |
| **Target service / `ledger.py`** | The actual product code being tested: a small loyalty-points calculator (earn points, redeem points, etc). This is the thing you're "reviewing." |
| **Twin** | A full copy of `ledger.py` with exactly one bug planted in it by AI. Called a "twin" because it looks almost identical to the real file. |
| **Mutation testing** | The overall technique: deliberately break the code in small ways ("mutate" it) and see if your tests notice. If your tests never fail on any broken version, your tests aren't really checking anything. |
| **Killed** | A twin's bug was caught — when we ran your test suite against the broken twin, at least one test failed. 🟥 Good outcome (for you). |
| **Survived** | A twin's bug was **not** caught — your tests all passed even though the code is broken. 🟧 This is the actual "blind spot": a bug your tests can't see. |
| **Invalid** | The twin was broken so badly it didn't even run (e.g. couldn't `import` it). Doesn't count as a real test either way. |
| **Witness** | For a twin that *survived*, this is one concrete example input where we can *prove* the real code and the broken twin give different answers. It's the receipt that the "survived" bug is real, not theoretical. |
| **Probe** | The screen where you personally guess what your own (real, verified) code returns for the witness input, without being told which option is "real." |
| **Narrator** | A second, separate AI call that runs *after* everything above is already decided. Its only job is to write a plain-English caption for each twin and a short summary. It has zero say in scoring — it's a captioning layer, nothing more. |
| **Rehearsal mode** | Instead of asking the AI to invent 8 new twins live, replay a fixed set of 8 twins that were generated once, earlier, and saved to disk. Used so the demo still works with no internet. Everything *after* that (running tests, searching for witnesses) still executes for real. |
| **Symbiosis Index** | The final single score (0–100) combining "did your tests catch bugs" and "do you personally understand your code." ≥82 passes. |

---

## 3. Screen-by-screen: what's real, what's fixed, what's computed

### Screen 1 — `/review` ("the AI-generated PR")

**What you see:** 3 hunks of `ledger.py` with a caption each, and a
"Verified — I understand this" button per hunk.

**What's actually happening:**
- The 3 hunks are **read live from the real file**, every single time the
  page loads — `web/app.py` re-opens `target/loyaltyledger/ledger.py`,
  finds these 3 same conceptual regions by searching for marker text, and
  slices out whatever is *currently* there. It is not a fixed string stored
  in the app; if you edit `ledger.py`, the very next page load shows your
  edit. The 3 regions themselves are fixed (same 3 topics every time), but
  their contents are not:
  1. Idempotent event settlement (the duplicate-event / retry logic)
  2. Tier multipliers & rounding (how points are calculated) — this one is
     stitched together from *two* separate spots in the file (with a
     "⋮ unrelated code skipped ⋮" divider between them), because
     `TIER_MULTIPLIERS` and `_round_half_up` aren't next to each other in
     the real file — exactly like a real multi-hunk diff would show it.
  3. Redeem: insufficient-balance guard (can you spend exactly what you have?)
- Nothing is being "merged" in any real sense — this is a framing device.
  The real, unmodified `ledger.py` already exists; you're just being shown
  live slices of it and asked to certify you understand them.
- Clicking "Verified" **does** get recorded (which hunk, and how many
  milliseconds after the page loaded you clicked it). This timestamp shows
  up later on the Verdict screen but doesn't affect your score directly.
- The only thing this screen unlocks is the **Merge** and **Load rehearsal
  twins** buttons, which become clickable once all 3 are verified.

**Is it always the same?** The 3 topics are always the same, but the
*content* shown for each one is pulled live from the real file every time
— so it will only ever look identical across runs if `ledger.py` itself
hasn't changed between them.

---

### Screen 2 — `/forge` ("twins attack your code")

**What you see:** a live-updating feed of 8 twins, each getting a red
"killed" or amber "survived" tag, running counters, and (a few seconds
later) an italic quoted sentence under each twin.

**What's actually happening, step by step:**

1. **8 broken copies of `ledger.py` get created.**
   - If you clicked **Merge**: an AI (Gemini) is asked, 8 separate times,
     to take the real file and inject *one* believable, subtle bug — never
     a syntax error, always something a real developer might plausibly
     write by mistake (wrong comparison, wrong order of operations, wrong
     rounding, etc). This is genuinely different every time you run it.
   - If you clicked **Load rehearsal twins**: no AI call happens at all.
     8 already-broken files, saved earlier from a real AI run, are copied
     in instead. These 8 are always the *same* 8 bugs every time you use
     rehearsal mode.
   - Either way, from this point on, everything is 100% real, local code —
     no AI involved, no internet needed.

2. **Each twin gets graded — for real.** Your twin is dropped into an
   isolated copy of the project, and your actual `pytest` test suite is run
   against it. If a test fails → **killed**. If every test still passes
   despite the planted bug → **survived**. This is real subprocess
   execution, not a lookup table — the killed/survived split you see is
   whatever your test suite actually does when run against that exact file.

3. **For every twin that survived, we hunt for a witness.** A separate tool
   (no AI — plain code using a fuzzing library called `hypothesis`) throws
   many different sequences of events at both the real code and the
   survived twin, looking for a case where they disagree. If it finds one,
   it shrinks it down to the smallest example and checks it's reproducible
   3 times before accepting it. This is what triggers the pulsing
   **"BLIND SPOT PROVEN"** banner — it means a bug was not just "theoretically
   uncaught" but concretely, provably exploitable.

4. **The italic quoted lines are decoration, added last.** Once all 8 twins
   are done, a *second*, unrelated AI call (the Narrator) is given the
   already-decided facts (which twins were killed/survived, what the bug
   was, what the witness input/output was) and asked to write one
   plain-English sentence per twin. This step can silently fail (no
   internet, quota, bad output) and the screen will look identical minus
   those quotes — it changes nothing about scoring.

**Is it always the same?**
- **Merge (live)**: No — different bugs, different killed/survived split,
  different witness, every time.
- **Rehearsal**: The 8 bugs themselves are always the same fixed set. But
  the *grading* (steps 2–3 above) is still executed fresh each time, so if
  you ever changed your test suite, rehearsal mode would immediately show
  different killed/survived results — it's not a canned video, it's a
  canned *input* with a live pipeline.

---

### Screen 3 — `/probe` (the actual test of *you*)

**What you see:** the input event(s) that broke one surviving twin, then
two boxes ("Output A" / "Output B") with real JSON in them, and a question:
"what does YOUR code actually return for this input?"

**What's actually happening:**
- Kinit picks the *first* twin from Screen 2 that both survived **and**
  had a witness found for it (in your example run, that was `twin_02`,
  the rounding bug).
- It takes that witness's input, and the two real outputs the witness
  finder already computed: what the real `ledger.py` returns, and what the
  broken twin returns. These are genuinely different (that's the whole
  point of a witness).
- It randomly decides — fresh each run — whether the real output goes in
  box A or box B, so you can't just always click the same button.
- You pick one. The server checks: did you pick the box that actually
  matches your real code's output? Right → recorded as correct (worth 50
  points later). Wrong → recorded as incorrect (worth 10 points later).

**Why this exists:** your test suite already told us it *can't* tell these
two apart (that's what "survived" means). This screen checks whether *you*
can, as the human who clicked "Verified." If you can't either, the bug
truly was invisible to both your tooling and your understanding — a real
blind spot, not just a test-coverage gap.

**Is it always the same?** No. The twin it's built from can differ (live
mode), and even when the twin is fixed (rehearsal mode), the left/right
placement is reshuffled every run, and obviously your own answer is
whatever you click.

**If no twin ever survives-with-a-witness:** the screen just tells you
"your test suite caught everything, nothing to probe" and you skip straight
to Verdict. This is a legitimately good outcome, not a bug in the app.

---

### Screen 4 — `/verdict` (the final scorecard)

**What you see:** a badge ("KINIT VERIFIED" or "UNVERIFIED"), a judgment
report paragraph, a table of the 3 hunks, a 4-number score panel, and a
"Show artifacts" button.

**What's actually happening:**

- **Judgment report** — the Narrator's second piece of output: a
  3-sentence summary of what your tests caught vs. missed, written from
  the same already-decided facts. If the Narrator call failed earlier,
  this whole card is simply omitted (you'd never have seen it) — nothing
  else on the page changes.

- **Per-hunk ledger** — this is the part most worth understanding
  precisely, because it's the least obvious:
  - If you never clicked "Verified" on a hunk → **Untested**.
  - If you *did* verify it, Kinit checks: is this hunk's topic related to
    the *one* twin that made it to the Probe screen? (e.g. a "rounding" bug
    is tied to the "Tier multipliers & rounding" hunk; a "redeem
    boundary"/"order-of-operations" bug is tied to the other two hunks.)
    - If it's related **and** you answered the probe **wrong** → **Claimed
      — could not defend**. You said you understood this part, but when
      tested, you couldn't correctly predict its real behavior.
    - Otherwise → **Verified ✓ (proven)**. Either it wasn't the hunk under
      attack this round, or it was and you defended it correctly.
  - **Important honesty note:** only the *one* hunk connected to the probed
    twin ever gets a real chance to fail. The other two verified hunks get
    marked "proven" by default, simply because nothing contradicted them
    *this run* — not because they were independently, rigorously attacked.
    (See the improvement ideas below — this is a real simplification.)

- **Score panel**, using your actual example numbers:
  - **Override Value = 40 × (killed twins ÷ valid twins)**. You had 5
    killed out of 8 valid (0 invalid) → 40 × 5/8 = **25**. This rewards how
    much of the AI's attack your *existing tests* caught on their own,
    before any human stepped in.
  - **Probe Defense = 50 if your probe answer was correct, else 10.** You
    got **50**, meaning you correctly predicted your own code's behavior.
  - **Baseline = 10**, a flat participation floor, always the same.
  - **Symbiosis Index = 25 + 50 + 10 = 85** (rounded). Since 85 ≥ 82, you
    get the gold **★ KINIT VERIFIED** badge. Below 82 would show
    "UNVERIFIED — judgment gap found" instead.

- **Show artifacts** — this isn't decorative either. It lists the literal
  files on disk that back up everything the screen just told you:
  - `runs/<id>/twins/twin_XX/ledger.py` — the actual broken file for each twin
  - `runs/<id>/twins/twin_XX/meta.json` — the bug description + bug family
  - `runs/<id>/twins/twin_XX/witness.json` — the proof input/output for any survivor
  - `runs/<id>/verdicts.jsonl` — one line per twin: killed/survived/invalid
  - `runs/<id>/ledger.json` — the exact score/badge shown on this page
  - `runs/<id>/narration.json` — the Narrator's captions/report (if it ran)

  You can open any of these in a text editor right now and see exactly
  what the screen is summarizing — nothing on `/verdict` is "trust me."

---

## 4. Quick-reference: "is it always the same?"

| Screen | Always identical? |
|---|---|
| `/review` (the 3 hunks) | The 3 topics are fixed, but the code shown is read live from `ledger.py` — identical only if the file hasn't changed. |
| `/forge` bugs, live mode | No — fresh AI call, different bugs each time. |
| `/forge` bugs, rehearsal mode | Yes, same 8 saved bugs — but grading them is done live every time. |
| `/forge` killed/survived split | Depends on your test suite + which bugs came up. Will change if you edit `test_ledger.py`. |
| `/forge` italic captions | Best-effort AI text; can be missing, never scored. |
| `/probe` options | Real outputs computed live; left/right shuffled randomly each run. |
| `/verdict` score & badge | Fully determined by the above — nothing hardcoded, all arithmetic on real counts. |

---

## 5. Honest limitations / what could be improved at each stage

You asked for this directly, so here it is, no sugar-coating:

**Stage 1 — Review**
- ~~The 3 hunks are static.~~ **Fixed:** the 3 hunks are now read live from
  the real `ledger.py` on every page load, not stored as fixed text. The 3
  *topics* are still fixed (idempotency / rounding / redeem), so a further
  improvement would be picking which regions matter dynamically too (e.g.
  by diffing against git history) instead of via fixed marker text.
- "Verified" only measures click speed, not comprehension — nothing stops
  someone from clicking all 3 instantly without reading. A stronger version
  could ask one quick concrete question per hunk before allowing "Verified."

**Stage 2 — Forge**
- Only twins that *survive and get a witness* matter downstream. Survived
  twins where the witness-finder timed out (rare, but possible) are shown
  as "survived" but never explained or probed — a wasted signal.
- All 8 twins currently attack the same general area of the file
  (`settle_event`). A broader version would rotate which function/region
  gets targeted each run, so different parts of the code get exercised
  over multiple runs instead of always the same hot zone.

**Stage 3 — Probe**
- Only the *first* survived+witnessed twin is ever probed, even if
  multiple twins survived (in your run, twin_02, twin_06, and twin_07 all
  survived, but only twin_02 became the probe). A more thorough version
  would probe you on all of them, or let you pick which one, and average
  the score.
- It's a binary guess with no explanation required — someone could
  genuinely guess right by luck 50% of the time. A stronger version might
  ask you to also justify *why*, or run the probe twice with different
  twins before trusting the result.

**Stage 4 — Verdict**
- As noted above, only one hunk out of three ever gets a real chance to be
  marked "could not defend" — the other two are "proven" by default rather
  than independently verified. A more rigorous scoring model would tie
  *every* hunk to specific bug families and only call a hunk "proven" if a
  twin from its own family was specifically killed by a test that actually
  covers it (not just "nothing bad happened to it this run").
- The Symbiosis Index weights are fixed constants (40 / 50-or-10 / 10) with
  no configurability — reasonable for a demo, but arbitrary as a real
  metric. Worth being upfront about that if presenting this to judges.

**Narrator (cross-cutting)**
- It's currently fire-and-forget with no retry and no caching — if it fails
  once, that run just has no captions, permanently. A small improvement:
  let a user manually re-trigger narration for a completed run from the
  Verdict screen instead of only trying once automatically.
