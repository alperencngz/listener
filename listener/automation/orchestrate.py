"""Overnight autonomous implementation pipeline.

Iterates through features in IMPROVEMENTS.md (most impactful first).
For each feature:
  1. Planner agent   -> reads spec + codebase, writes implementation_plan_F{n}.md
  2. Implementor agent -> reads plan, writes code, tests
  3. Progress updater  -> verifies what changed, appends to progress.md

All agents run via Claude Code SDK with Max OAuth.

Usage:
    listener automate                # run all features
    listener automate --start-from F4  # resume from F4
    python -m listener.automation.orchestrate
"""

import asyncio
import logging
import os
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

    fmt = logging.Formatter("[%(asctime)s] %(levelname)s — %(message)s", datefmt="%H:%M:%S")

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
        model="claude-sonnet-4-5",
        env=env_overrides,
    )

    async def _consume() -> ResultMessage | None:
        result: ResultMessage | None = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage) and result is None:
                result = message
        return result

    log.debug("[%s] Starting agent (max_turns=%d, timeout=%ds)", label, max_turns, timeout_s)

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
# Agent prompts
# ---------------------------------------------------------------------------

PLANNER_SYSTEM = (
    "You are an expert Python/Flask software architect. "
    "You create detailed, step-by-step implementation plans. "
    "You always read relevant source files before writing the plan. "
    "You write the plan file to disk when done."
)

IMPLEMENTOR_SYSTEM = (
    "You are an expert Python/Flask developer. "
    "You implement features by following a plan step-by-step. "
    "You read each file before modifying it. You test as you go. "
    "You write clean, working code that follows the existing codebase style. "
    "You NEVER leave TODOs, placeholders, or incomplete stubs. "
    "If you encounter an issue, you fix it rather than skipping."
)

PROGRESS_SYSTEM = (
    "You are a project progress tracker. "
    "You verify what was actually created or changed and update "
    "the progress log accurately and concisely."
)


def planner_prompt(fid: str, fname: str) -> str:
    return f"""\
You are creating a detailed implementation plan for feature {fid}: {fname}.

PROJECT ROOT: {PROJECT_DIR}

STEP 1 — Read context:
  a) Read {IMPROVEMENTS} and find the spec section for {fid}. It contains
     the full feature description, code snippets, library versions,
     files to create/modify, and architectural notes.
  b) Read the existing source files that will be affected (listed in the spec).
  c) Read {PROGRESS} (if it exists) to see what has already been implemented
     in previous features — you may need to build on their work.

STEP 2 — Write the plan:
  Create a file at: {PLANS_DIR / (fid.lower() + '_implementation_plan.md')}

  The plan MUST include:
  1. Pre-implementation checklist (files to read, things to verify)
  2. Ordered list of implementation tasks — for each task:
     - Exact file path (absolute)
     - Action: create / modify / append
     - Detailed description of what to write (use code snippets from IMPROVEMENTS.md)
     - Any dependencies on previous tasks
  3. Dependency installation steps (exact pip commands, pyproject.toml edits)
  4. Verification steps (import checks, basic smoke tests)
  5. Integration notes (how this feature connects to the web UI, CLI, etc.)

Be extremely specific. The implementing agent only sees this plan and the codebase —
it has no other context. Reference absolute file paths. Include code when helpful."""


def implementor_prompt(fid: str, fname: str) -> str:
    plan_path = PLANS_DIR / (fid.lower() + "_implementation_plan.md")
    return f"""\
You are implementing feature {fid}: {fname}.

PROJECT ROOT: {PROJECT_DIR}

STEP 1 — Read your implementation plan at:
  {plan_path}

STEP 2 — Follow the plan step by step:
  - Read each file before modifying it (never edit blind).
  - Use the Edit tool for modifications and Write tool for new files.
  - When the plan references code snippets, use them as a starting point.
  - After creating/editing Python files, run a quick import check:
      python -c "import listener.{{module}}"
  - If the plan says to update pyproject.toml, do it and then run:
      pip install -e {PROJECT_DIR}
  - If something fails, read the error, diagnose, and fix it.
    Do NOT skip steps or leave broken code.

STEP 3 — Final verification:
  - Run: python -c "from listener.web.app import app; print('OK')"
  - Run any specific tests mentioned in the plan.
  - If something doesn't import or compile, fix it before finishing.

RULES:
  - Do NOT leave TODOs, placeholder comments, or stub implementations.
  - Do NOT refactor unrelated code.
  - Do NOT skip a step because it seems hard — implement it fully.
  - If the feature is truly too large (e.g. F9 real-time streaming),
    implement the core functionality and note what remains at the end.
  - Keep the existing code style (see listener/claude/runner.py for patterns)."""


def progress_prompt(fid: str, fname: str, plan_ok: bool, impl_ok: bool) -> str:
    return f"""\
Update the progress log for feature {fid}: {fname}.

PROJECT ROOT: {PROJECT_DIR}
PROGRESS FILE: {PROGRESS}

Planner status: {"succeeded" if plan_ok else "FAILED"}
Implementor status: {"succeeded" if impl_ok else "FAILED" if plan_ok else "SKIPPED (planner failed)"}

STEPS:
1. Read {PROGRESS} (create it if it doesn't exist).
2. Check which files were actually created or modified for {fid}:
   - Look in {PLANS_DIR} for the plan file
   - Look in {PROJECT_DIR}/listener/ for new or changed .py files
   - Look at {PROJECT_DIR}/pyproject.toml for dependency changes
   - Check {PROJECT_DIR}/listener/web/templates/index.html for UI changes
3. Append a new section to {PROGRESS} with:
   - Feature: {fid}: {fname}
   - Timestamp: {datetime.now().strftime("%Y-%m-%d %H:%M")}
   - Status: completed / partial / failed
   - Files created (list)
   - Files modified (list)
   - Dependencies added (if any)
   - Issues or notes (if any)

Keep entries concise. Do NOT overwrite previous entries — append only."""


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
            f"**Pipeline started:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n---\n\n"
        )

    # Determine starting point
    features = list(FEATURES)
    if start_from:
        idx = next((i for i, (fid, _) in enumerate(features) if fid == start_from), None)
        if idx is None:
            log.error("Unknown feature: %s. Valid: %s", start_from, [f[0] for f in features])
            return
        features = features[idx:]
        log.info("Resuming from %s (skipping %d features)", start_from, idx)

    log.info("=" * 60)
    log.info("Listener overnight implementation pipeline")
    log.info("Features to implement: %d", len(features))
    log.info("Log file: %s", log_path)
    log.info("=" * 60)

    tools_readonly = ["Read", "Glob", "Grep", "Bash", "Write"]
    tools_full = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    for i, (fid, fname) in enumerate(features, 1):
        log.info("")
        log.info("=" * 60)
        log.info("[%d/%d] %s: %s", i, len(features), fid, fname)
        log.info("=" * 60)

        plan_ok = False
        impl_ok = False

        # ---- 1. Planner ----
        try:
            log.info("  [1/3] Planner starting...")
            result = await run_agent(
                prompt=planner_prompt(fid, fname),
                system_prompt=PLANNER_SYSTEM,
                allowed_tools=tools_readonly,
                max_turns=40,
                timeout_s=900,      # 15 min
                log=log,
                label=f"{fid}-planner",
            )
            plan_ok = True
            log.info("  [1/3] Planner done")
            log.debug("  Planner output: %s", result[:300] if result else "(empty)")
        except Exception as e:
            log.error("  [1/3] Planner FAILED: %s", e)

        # ---- 2. Implementor ----
        if plan_ok:
            try:
                log.info("  [2/3] Implementor starting...")
                result = await run_agent(
                    prompt=implementor_prompt(fid, fname),
                    system_prompt=IMPLEMENTOR_SYSTEM,
                    allowed_tools=tools_full,
                    max_turns=200,
                    timeout_s=2700,     # 45 min
                    log=log,
                    label=f"{fid}-implementor",
                )
                impl_ok = True
                log.info("  [2/3] Implementor done")
                log.debug("  Implementor output: %s", result[:300] if result else "(empty)")
            except Exception as e:
                log.error("  [2/3] Implementor FAILED: %s", e)
        else:
            log.warning("  [2/3] Skipping implementor (planner failed)")

        # ---- 3. Progress updater ----
        try:
            log.info("  [3/3] Updating progress...")
            await run_agent(
                prompt=progress_prompt(fid, fname, plan_ok, impl_ok),
                system_prompt=PROGRESS_SYSTEM,
                allowed_tools=tools_readonly,
                max_turns=20,
                timeout_s=300,      # 5 min
                log=log,
                label=f"{fid}-progress",
            )
            log.info("  [3/3] Progress updated")
        except Exception as e:
            log.error("  [3/3] Progress update FAILED: %s", e)

    log.info("")
    log.info("=" * 60)
    log.info("Pipeline finished at %s", datetime.now().strftime("%H:%M"))
    log.info("Review: %s", PROGRESS)
    log.info("Logs:   %s", log_path)
    log.info("=" * 60)


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
