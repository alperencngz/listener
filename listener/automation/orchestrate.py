"""Overnight autonomous implementation pipeline.

Iterates through features in IMPROVEMENTS.md (most impactful first).
For each feature:
  1. Planner agent   -> reads spec + codebase, writes implementation_plan_F{n}.md
  2. Implementor agent -> reads plan, writes code, tests
  3. Progress updater  -> verifies, updates progress.md, creates git commit

All agents run via Claude Code SDK with Max OAuth.

Usage:
    listener automate                  # run all features
    listener automate --start-from F4  # resume from F4
    python -m listener.automation.orchestrate
"""

import asyncio
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Trigger SDK monkey-patch (tolerates unknown message types)
import listener.claude.runner  # noqa: F401

from claude_code_sdk import ClaudeCodeOptions, query
from claude_code_sdk.types import ResultMessage

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent  # repo root
IMPROVEMENTS = PROJECT_DIR / "IMPROVEMENTS.md"
PROGRESS = PROJECT_DIR / "progress.md"
PLANS_DIR = PROJECT_DIR / "implementation_plans"

# ---------------------------------------------------------------------------
# Feature order (most impactful -> least, respects dependencies)
# ---------------------------------------------------------------------------

FEATURES = [
    ("F1", "Speaker Diarization"),
    ("F2", "Chat with Transcript"),
    ("F3", "Custom Analysis Recipes"),
    ("F5", "Click-to-Seek Audio Linkage"),
    ("F4", "Full-Text Search Across Meetings"),
    ("F7", "Noise Preprocessing"),
    ("F6", "Multi-Format Export"),
    ("F8", "Meeting Analytics Dashboard"),
    ("F10", "Webhooks & API for Automation"),
    ("F9", "Real-Time Live Transcription"),
]

# ---------------------------------------------------------------------------
# Logging — both console and file
# ---------------------------------------------------------------------------

LOG_DIR = PROJECT_DIR / "automation_logs"


def setup_logging():
    LOG_DIR.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_path = LOG_DIR / f"run_{ts}.log"

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s — %(message)s", datefmt="%H:%M:%S"
    )

    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    root = logging.getLogger("orchestrator")
    root.setLevel(logging.DEBUG)
    root.addHandler(fh)
    root.addHandler(ch)

    return root, log_path


# ---------------------------------------------------------------------------
# SDK runner — multi-turn with tools
# ---------------------------------------------------------------------------


async def run_agent(
    prompt: str,
    system_prompt: str,
    allowed_tools: list[str],
    max_turns: int,
    timeout_s: int,
    log: logging.Logger,
    label: str = "",
    model: str = "claude-opus-4-6",
) -> str:
    """Run a Claude Code SDK session with tool access.

    Follows the same safety patterns as listener/claude/runner.py:
    - Strips ANTHROPIC_API_KEY to force Max OAuth
    - Never break/return from inside the async generator
    - Collects ResultMessage after generator completes naturally
    """
    env_overrides: dict[str, str] = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        env_overrides["ANTHROPIC_API_KEY"] = ""

    options = ClaudeCodeOptions(
        system_prompt=system_prompt,
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        model=model,
        env=env_overrides,
    )

    async def _consume() -> ResultMessage | None:
        result: ResultMessage | None = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage) and result is None:
                result = message
        return result

    log.debug(
        "[%s] Starting agent (model=%s, max_turns=%d, timeout=%ds)",
        label, model, max_turns, timeout_s,
    )

    try:
        result = await asyncio.wait_for(_consume(), timeout=timeout_s)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Agent timed out after {timeout_s}s")
    except asyncio.CancelledError:
        raise RuntimeError("Agent cancelled by stale cancel scope")
    except Exception as exc:
        raise RuntimeError(f"Agent SDK error: {exc}") from exc

    if result is None:
        raise RuntimeError("No ResultMessage received")
    if result.is_error:
        raise RuntimeError(f"Agent returned error (subtype={result.subtype})")

    return result.result or ""


# ---------------------------------------------------------------------------
# System prompts — detailed enough for unattended overnight operation
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = """\
You are an expert Python/Flask software architect creating implementation plans \
for a meeting transcription tool called "Listener".

PROJECT ARCHITECTURE (know this before you start):
- Backend: Python 3.12, Flask web server on port 8642
- Audio: sounddevice for recording, faster-whisper (large-v3) for STT
- AI: Claude Code SDK with Max OAuth for analysis (listener/claude/runner.py)
- Frontend: SINGLE index.html file with embedded CSS and JS (no build step, no npm)
- State: file-based (transcripts/ directory), no database yet
- CLI: Click-based (listener/cli.py) with commands: record, transcribe, analyze, web, automate
- Web UI: Flask app at listener/web/app.py, template at listener/web/templates/index.html
- All Claude calls go through listener/claude/runner.py (run_claude_session for text, run_with_schema_validation for JSON)

PLANNING RULES:
1. ALWAYS read relevant source files before writing the plan — never assume file contents.
2. Your plan is the ONLY context the implementing agent has. Be exhaustive.
3. Include absolute file paths for everything.
4. Include code snippets from IMPROVEMENTS.md where available — don't make the implementor search.
5. Account for the fact that SHARED FILES (app.py, index.html, pyproject.toml) may have been \
modified by previous features. Tell the implementor to read them fresh.
6. For features that need UI: describe the exact HTML/CSS/JS to add to index.html. \
Remember it's a single file with inline styles and scripts.
7. Write the plan file to disk when done — this is your only deliverable."""

IMPLEMENTOR_SYSTEM = """\
You are an expert Python/Flask developer implementing features for "Listener", \
a meeting transcription tool.

PROJECT ARCHITECTURE:
- Project root: {project_dir}
- Backend: Python 3.12, Flask (listener/web/app.py)
- Frontend: SINGLE HTML file at listener/web/templates/index.html (CSS + JS inline, no build step)
- Claude SDK: listener/claude/runner.py (run_claude_session, run_with_schema_validation)
- CLI: listener/cli.py (Click-based)
- Dependencies: pyproject.toml (install with: pip install -e {project_dir})
- Feature spec: IMPROVEMENTS.md (detailed specs with code snippets for every feature)

IMPLEMENTATION RULES:
1. Read your implementation plan FIRST. Follow it step by step.
2. Read EVERY file before modifying it — shared files (app.py, index.html) may have been \
changed by previous features.
3. For new Python modules: always verify with "python -c 'import listener.module_name'"
4. For pyproject.toml changes: always run "pip install -e {project_dir}" afterward.
5. For HTML changes: add new elements, styles, and JS functions to the EXISTING single file. \
Do not create separate CSS/JS files.
6. If the plan is unclear on something, read the IMPROVEMENTS.md section for the feature — \
it has detailed specs, code snippets, library APIs, and exact schemas.
7. Create directories as needed (e.g., listener/recipes/, listener/export/).
8. NEVER leave TODOs, stubs, placeholder comments, or partial implementations.
9. At the END, verify the entire app still works: \
python -c "from listener.web.app import app; print('OK')"
10. If that fails, FIX IT before finishing. A broken app blocks all subsequent features.""".format(
    project_dir=PROJECT_DIR
)

PROGRESS_SYSTEM = """\
You are a project progress tracker and git commit author for the "Listener" project.

YOUR JOB:
1. Assess what the implementor actually changed (git status, git diff)
2. Verify the app still starts (critical — a broken build blocks everything)
3. Update progress.md with an accurate status entry
4. Create a clean git commit with a conventional commit message
5. If the app is broken, note it prominently in progress.md as a BLOCKER

GIT RULES:
- Use "git add <specific files>" — NEVER "git add -A" or "git add ."
- Never commit: .wav, .env, __pycache__/, transcripts/, automation_logs/
- Commit message format: feat(fN): short description
- Use a heredoc for multi-line commit messages
- If there are no changes, skip the commit and note it in progress.md"""


# ---------------------------------------------------------------------------
# Agent prompts
# ---------------------------------------------------------------------------


def planner_prompt(fid: str, fname: str) -> str:
    return f"""\
Create a detailed implementation plan for feature {fid}: {fname}.

PROJECT ROOT: {PROJECT_DIR}

============================
STEP 1 — Explore the codebase
============================

a) Run: ls -la {PROJECT_DIR}/listener/
   This shows you the current module structure. Note any new modules added by previous features.

b) Read the feature specification:
   - File: {IMPROVEMENTS}
   - Search for the section "### {fid}: {fname}" — it contains the full spec with
     code snippets, library versions, exact APIs, files to create/modify, schemas, etc.
   - Also read Section 5 "Dependency Summary" and Section 6 "Architecture Notes"

c) Read the key source files that will be affected:
   - {PROJECT_DIR}/listener/web/app.py (Flask backend — ALL features modify this)
   - {PROJECT_DIR}/listener/web/templates/index.html (Frontend — most features modify this)
   - {PROJECT_DIR}/listener/cli.py (CLI commands)
   - {PROJECT_DIR}/listener/analyzer.py (Claude analysis — several features modify this)
   - {PROJECT_DIR}/listener/transcriber.py (Whisper transcription)
   - {PROJECT_DIR}/pyproject.toml (dependencies)
   - Any other files listed in the IMPROVEMENTS.md spec for this feature

d) Read {PROGRESS} (if it exists) to understand what previous features have already
   been implemented. This is critical — you may need to build on their work, and
   shared files (app.py, index.html) will already contain their additions.

============================
STEP 2 — Write the plan
============================

Create: {PLANS_DIR / (fid.lower() + '_implementation_plan.md')}

Structure the plan EXACTLY like this:

# Implementation Plan: {fid} — {fname}

## Pre-Implementation Checklist
- Files the implementor must read first (absolute paths)
- Things to verify (e.g., "check if pyannote.audio is already in pyproject.toml")

## Dependencies
- Exact packages to add to pyproject.toml (with version constraints)
- Exact pip install command
- Any external requirements (e.g., HuggingFace token, font files)

## Implementation Tasks

### Task 1: [descriptive name]
- **File:** [absolute path]
- **Action:** create / modify
- **Details:** [exactly what to write or change — include code snippets]
- **Verification:** [command to verify this task, e.g., python -c "import ..."]

### Task 2: ...
[continue for ALL tasks]

## Frontend Changes (if applicable)
- **File:** {PROJECT_DIR}/listener/web/templates/index.html
- Describe each addition: new HTML elements, CSS rules, JS functions
- Describe where in the file to add them (e.g., "add after the existing tabs div")
- Include the actual HTML/CSS/JS code to add

## Flask Endpoint Changes (if applicable)
- New routes to add to app.py
- Include the full Python code for each endpoint

## Final Verification
- Commands to run to verify everything works end-to-end
- Expected output for each command

IMPORTANT:
- Include ALL code snippets from IMPROVEMENTS.md relevant to this feature.
- The implementor cannot ask questions — the plan must be self-contained.
- Use ABSOLUTE paths for every file reference.
- If this feature depends on a previous feature (e.g., F8 depends on F1),
  describe how to handle the case where the dependency wasn't implemented."""


def implementor_prompt(fid: str, fname: str) -> str:
    plan_path = PLANS_DIR / (fid.lower() + "_implementation_plan.md")
    return f"""\
Implement feature {fid}: {fname}.

PROJECT ROOT: {PROJECT_DIR}

============================
STEP 1 — Read your plan and context
============================

a) Read your implementation plan:
   {plan_path}

b) Read the feature spec in IMPROVEMENTS.md for additional context:
   {IMPROVEMENTS}
   Search for "### {fid}: {fname}" — this has detailed code snippets, APIs, and schemas
   that supplement the plan.

c) Read the current state of shared files (these may have been modified by prior features):
   - {PROJECT_DIR}/listener/web/app.py
   - {PROJECT_DIR}/listener/web/templates/index.html
   - {PROJECT_DIR}/pyproject.toml

d) Read {PROGRESS} to understand what has already been implemented.

============================
STEP 2 — Implement step by step
============================

Follow the plan's Implementation Tasks in order. For each task:

1. READ the target file first (or verify the directory exists for new files)
2. Make the change using Edit (for modifications) or Write (for new files)
3. Verify with the task's verification command (usually a Python import check)
4. If something fails: read the error, diagnose, fix it. Do NOT move on with broken code.

Key patterns to follow:
- New Flask endpoints go in listener/web/app.py
- New Claude SDK calls use listener/claude/runner.py (run_claude_session or run_with_schema_validation)
- Frontend changes go in listener/web/templates/index.html (single file, inline CSS+JS)
- New CLI commands go in listener/cli.py using Click decorators
- New dependencies go in pyproject.toml then run: pip install -e {PROJECT_DIR}
- New directories: create with Bash (mkdir -p) before writing files into them

============================
STEP 3 — Final verification
============================

After ALL tasks are done, run these checks:

a) Verify the entire app still imports:
   python -c "from listener.web.app import app; print('App OK')"

b) Verify CLI still works:
   listener --help

c) Run any feature-specific checks from the plan.

d) If ANYTHING is broken, fix it. A broken app blocks all subsequent features.
   This is the most critical rule.

============================
RULES
============================
- NEVER leave TODOs, placeholder comments, pass statements, or "implement later" stubs.
- NEVER refactor or modify code unrelated to this feature.
- NEVER create separate .css or .js files — everything goes in index.html.
- If the feature needs a new directory (listener/recipes/, listener/export/, etc.),
  create it with mkdir -p.
- If you need to download external files (e.g., fonts for PDF export),
  describe how but skip the actual download — note it in your final output.
- If the feature is very large (e.g., F9 Real-Time Streaming), implement the core
  functionality fully and note what advanced aspects remain."""


def progress_prompt(fid: str, fname: str, plan_ok: bool, impl_ok: bool) -> str:
    return f"""\
Update the progress log and create a git commit for feature {fid}: {fname}.

PROJECT ROOT: {PROJECT_DIR}
PROGRESS FILE: {PROGRESS}
PLAN FILE: {PLANS_DIR / (fid.lower() + '_implementation_plan.md')}

Planner status: {"succeeded" if plan_ok else "FAILED"}
Implementor status: {"succeeded" if impl_ok else "FAILED" if plan_ok else "SKIPPED (planner failed)"}

============================
STEP 1 — Assess what changed
============================

a) Run: git status
b) Run: git diff --stat
c) List which files were created or modified for this feature.
   Check:
   - {PLANS_DIR}/ for the plan file
   - {PROJECT_DIR}/listener/ for new or changed .py files
   - {PROJECT_DIR}/listener/web/templates/index.html for UI changes
   - {PROJECT_DIR}/listener/recipes/ for YAML files
   - {PROJECT_DIR}/listener/export/ for export modules
   - {PROJECT_DIR}/pyproject.toml for dependency changes

============================
STEP 2 — Verify app health
============================

Run: python -c "from listener.web.app import app; print('APP OK')"
Run: listener --help

If EITHER fails, note this as a **BLOCKER** in progress.md.
This is critical information for debugging in the morning.

============================
STEP 3 — Update progress.md
============================

Read {PROGRESS}, then APPEND (do NOT overwrite) a new section:

---

## {fid}: {fname}

**Timestamp:** [current time]
**Status:** completed / partial / failed
**App health:** OK / BROKEN (note the error)

**Files created:**
- [list each new file]

**Files modified:**
- [list each modified file]

**Dependencies added:**
- [list any new packages in pyproject.toml]

**Notes:**
- [any issues, warnings, or observations]

---

============================
STEP 4 — Git commit
============================

a) Stage files:
   git add <file1> <file2> ...
   - Add ALL new and modified files related to {fid}
   - Add the implementation plan file from {PLANS_DIR}/
   - Add the updated progress.md
   - NEVER add: .wav, __pycache__/, .env, transcripts/, automation_logs/
   - NEVER use "git add -A" or "git add ."

b) Commit with a conventional message using a heredoc:
   git commit -m "$(cat <<'EOF'
   feat({fid.lower()}): {fname.lower()}

   [2-3 sentence description of what was implemented]

   Files: N new, M modified
   EOF
   )"

c) If there are NO changes to commit (nothing was implemented), skip the commit
   and note "No changes to commit" in progress.md.

============================
STEP 5 — Verify commit
============================

Run: git log --oneline -5
Confirm the new commit appears at the top."""


# ---------------------------------------------------------------------------
# Auto-resume: detect completed features from progress.md
# ---------------------------------------------------------------------------


def get_completed_features() -> set[str]:
    """Parse progress.md and return feature IDs marked as completed."""
    if not PROGRESS.exists():
        return set()

    completed = set()
    content = PROGRESS.read_text()
    current_fid = None

    for line in content.split("\n"):
        # Match feature headers: "## F1: Speaker Diarization"
        m = re.match(r"^##\s+(F\d+):", line)
        if m:
            current_fid = m.group(1)
        # Match status: "**Status:** completed"
        if current_fid and "**Status:**" in line and "completed" in line.lower():
            completed.add(current_fid)
            current_fid = None

    return completed


# ---------------------------------------------------------------------------
# Main orchestration loop
# ---------------------------------------------------------------------------


async def run_pipeline(start_from: str | None = None):
    log, log_path = setup_logging()
    PLANS_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize progress file
    if not PROGRESS.exists():
        PROGRESS.write_text(
            f"# Implementation Progress\n\n"
            f"**Pipeline started:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
            f"---\n\n"
        )

    # Auto-resume: skip features already completed in progress.md
    completed = get_completed_features()
    features = list(FEATURES)

    if start_from:
        idx = next(
            (i for i, (fid, _) in enumerate(features) if fid == start_from), None
        )
        if idx is None:
            log.error(
                "Unknown feature: %s. Valid: %s",
                start_from,
                [f[0] for f in features],
            )
            sys.exit(1)
        features = features[idx:]
        log.info("--start-from %s: skipping %d features", start_from, idx)

    # Filter out already-completed features
    remaining = [(fid, fn) for fid, fn in features if fid not in completed]

    if completed:
        log.info("Already completed (from progress.md): %s", sorted(completed))
    if not remaining:
        log.info("All features are already completed! Nothing to do.")
        sys.exit(0)

    log.info("=" * 60)
    log.info("Listener overnight implementation pipeline")
    log.info("Remaining features: %d / %d", len(remaining), len(FEATURES))
    log.info("Models: Opus (planner + implementor), Sonnet (progress)")
    log.info("Log file: %s", log_path)
    log.info("=" * 60)

    features = remaining

    # Maximum tool access — bypassPermissions auto-approves everything.
    # List every tool the agents could conceivably need so nothing is blocked.
    all_tools = [
        "Read", "Write", "Edit", "Bash", "Glob", "Grep",
        "WebSearch", "WebFetch",
    ]

    for i, (fid, fname) in enumerate(features, 1):
        log.info("")
        log.info("=" * 60)
        log.info("[%d/%d] %s: %s", i, len(features), fid, fname)
        log.info("Started at %s", datetime.now().strftime("%H:%M:%S"))
        log.info("=" * 60)

        plan_ok = False
        impl_ok = False

        # ---- 1. Planner (Opus) ----
        try:
            log.info("  [1/3] Planner starting (opus)...")
            result = await run_agent(
                prompt=planner_prompt(fid, fname),
                system_prompt=PLANNER_SYSTEM,
                allowed_tools=all_tools,
                max_turns=80,
                timeout_s=1800,  # 30 min
                log=log,
                label=f"{fid}-planner",
                model="claude-opus-4-6",
            )
            plan_ok = True
            log.info("  [1/3] Planner done at %s", datetime.now().strftime("%H:%M:%S"))
            log.debug("  Planner output: %s", result[:500] if result else "(empty)")
        except Exception as e:
            log.error("  [1/3] Planner FAILED: %s", e)

        # ---- 2. Implementor (Opus) ----
        if plan_ok:
            try:
                log.info("  [2/3] Implementor starting (opus)...")
                result = await run_agent(
                    prompt=implementor_prompt(fid, fname),
                    system_prompt=IMPLEMENTOR_SYSTEM,
                    allowed_tools=all_tools,
                    max_turns=400,
                    timeout_s=5400,  # 90 min
                    log=log,
                    label=f"{fid}-implementor",
                    model="claude-opus-4-6",
                )
                impl_ok = True
                log.info(
                    "  [2/3] Implementor done at %s",
                    datetime.now().strftime("%H:%M:%S"),
                )
                log.debug(
                    "  Implementor output: %s", result[:500] if result else "(empty)"
                )
            except Exception as e:
                log.error("  [2/3] Implementor FAILED: %s", e)
        else:
            log.warning("  [2/3] Skipping implementor (planner failed)")

        # ---- 3. Progress updater + git commit (Sonnet) ----
        try:
            log.info("  [3/3] Updating progress & committing (sonnet)...")
            await run_agent(
                prompt=progress_prompt(fid, fname, plan_ok, impl_ok),
                system_prompt=PROGRESS_SYSTEM,
                allowed_tools=all_tools,
                max_turns=40,
                timeout_s=600,  # 10 min
                log=log,
                label=f"{fid}-progress",
                model="claude-sonnet-4-5",
            )
            log.info(
                "  [3/3] Progress updated at %s", datetime.now().strftime("%H:%M:%S")
            )
        except Exception as e:
            log.error("  [3/3] Progress update FAILED: %s", e)

        log.info(
            "  Feature %s finished at %s (plan=%s, impl=%s)",
            fid,
            datetime.now().strftime("%H:%M:%S"),
            "OK" if plan_ok else "FAIL",
            "OK" if impl_ok else "FAIL",
        )

    # Check if all features are now complete
    final_completed = get_completed_features()
    all_fids = {fid for fid, _ in FEATURES}
    still_remaining = all_fids - final_completed

    log.info("")
    log.info("=" * 60)
    log.info("Pipeline finished at %s", datetime.now().strftime("%H:%M"))
    log.info("Completed: %d / %d features", len(final_completed), len(FEATURES))
    log.info("Review: %s", PROGRESS)
    log.info("Logs:   %s", log_path)

    if still_remaining:
        log.info("Uncompleted: %s", sorted(still_remaining))
        log.info("Exit 1 — retry loop will re-run for remaining features")
        log.info("=" * 60)
        sys.exit(1)
    else:
        log.info("All features completed!")
        log.info("=" * 60)
        sys.exit(0)


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Overnight implementation pipeline")
    parser.add_argument(
        "--start-from",
        type=str,
        default=None,
        help="Feature ID to start from (e.g. F4). Skips earlier features.",
    )
    args = parser.parse_args()
    asyncio.run(run_pipeline(start_from=args.start_from))


if __name__ == "__main__":
    main()
