"""Meeting analysis using Claude Code SDK.

Takes a transcript and produces summary, action items,
key decisions, topics discussed, and a short title.
"""

import asyncio

from listener.claude.runner import run_claude_session

SYSTEM_PROMPT = """\
You are a meeting analyst. You receive a meeting transcript and produce \
a comprehensive, well-structured markdown analysis.

IMPORTANT: Respond in the SAME LANGUAGE as the transcript. \
If the meeting is in Turkish, write your analysis in Turkish. \
If in English, write in English. If mixed, use the dominant language."""

PROMPT_TEMPLATE = """\
Analyze this meeting transcript and produce:

## Summary
A concise summary (2-5 paragraphs) of what was discussed and decided.

## Action Items
A checklist of action items. For each, include:
- What needs to be done
- Who is responsible (if mentioned)
- Deadline (if mentioned)

If none were identified, say so explicitly.

## Key Decisions
Bullet list of decisions made during the meeting.

## Topics Discussed
Brief list of the main topics covered.

---

Transcript:

{transcript}"""

TITLE_SYSTEM_PROMPT = """\
You generate concise meeting titles. Return ONLY the title text. \
No quotes, no explanation, no punctuation at the end. Max 8 words. \
Respond in the dominant language of the transcript."""

TITLE_PROMPT_TEMPLATE = """\
Generate a short, descriptive title for this meeting.

Transcript (first portion):

{transcript}"""


async def analyze_transcript(
    transcript_text: str,
    model: str = "claude-sonnet-4-5",
) -> str:
    """Analyze a meeting transcript using Claude.

    Returns:
        Markdown-formatted analysis.
    """
    prompt = PROMPT_TEMPLATE.format(transcript=transcript_text)

    return await run_claude_session(
        prompt=prompt,
        system_prompt=SYSTEM_PROMPT,
        model=model,
        node_name="meeting_analysis",
    )


def analyze_transcript_sync(
    transcript_text: str,
    model: str = "claude-sonnet-4-5",
) -> str:
    """Synchronous wrapper for analyze_transcript."""
    return asyncio.run(analyze_transcript(transcript_text, model=model))


async def generate_title(
    transcript_text: str,
    model: str = "claude-sonnet-4-5",
) -> str:
    """Generate a short title for a meeting from its transcript."""
    snippet = transcript_text[:2000]
    prompt = TITLE_PROMPT_TEMPLATE.format(transcript=snippet)

    result = await run_claude_session(
        prompt=prompt,
        system_prompt=TITLE_SYSTEM_PROMPT,
        model=model,
        node_name="title_generation",
    )
    return result.strip().strip('"\'').rstrip(".")


def generate_title_sync(
    transcript_text: str,
    model: str = "claude-sonnet-4-5",
) -> str:
    """Synchronous wrapper for generate_title."""
    return asyncio.run(generate_title(transcript_text, model=model))
