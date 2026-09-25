"""Claude wrapper for meeting analysis.

Adapted from AbsolutePath/services/worker/claude/runner.py.
Provides run_claude_session() for single-turn calls. Two access modes, chosen
in ``~/.listener/config.yaml`` (``claude_auth``, see ``listener.settings``):

* ``max`` (default): the Claude Code SDK, which uses the Claude Code login on
  this machine (Max/Pro subscription, no per-token cost).
* ``api``: the Anthropic Python SDK with a saved API key, for people without
  Claude Code. Same prompts, same single-turn shape.

Key design decisions (inherited from AbsolutePath):
- max_turns=1: Single-turn generation, no tool use
- allowed_tools=[]: No tool access needed
- auth_mode="max": Uses Claude Max subscription (OAuth)
- Markdown fence stripping for JSON output
- 300s timeout for heavy analysis tasks
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

from claude_code_sdk import ClaudeCodeOptions, query
from claude_code_sdk._errors import MessageParseError
from claude_code_sdk.types import ResultMessage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SDK monkey-patch: tolerate unknown message types (e.g. rate_limit_event)
# ---------------------------------------------------------------------------

_SDK_PATCHED = False


def _patch_sdk_message_parser() -> None:
    """Monkey-patch SDK message parser to skip unknown message types.

    SDK may raise MessageParseError on unrecognized types like
    rate_limit_event. This replaces parse_message with a tolerant
    version that returns a harmless SystemMessage instead of raising.
    """
    global _SDK_PATCHED
    if _SDK_PATCHED:
        return

    from claude_code_sdk._internal import message_parser
    from claude_code_sdk._internal import client as sdk_client
    from claude_code_sdk.types import SystemMessage

    _original_parse = message_parser.parse_message

    def _tolerant_parse(data):
        try:
            return _original_parse(data)
        except MessageParseError as e:
            if "Unknown message type" in str(e):
                msg_type = data.get("type") if isinstance(data, dict) else "?"
                logger.debug("Skipping unknown SDK message type: %s", msg_type)
                return SystemMessage(subtype="unknown_skipped", data=data)
            raise

    message_parser.parse_message = _tolerant_parse
    sdk_client.parse_message = _tolerant_parse
    _SDK_PATCHED = True


_patch_sdk_message_parser()


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class ClaudeTaskError(Exception):
    """Base exception for Claude Code SDK session failures."""

    def __init__(self, detail: str, *, node_name: str = "") -> None:
        self.detail = detail
        self.node_name = node_name
        super().__init__(detail)


class ClaudeSchemaError(ClaudeTaskError):
    """Raised when structured output doesn't match the expected JSON schema."""

    def __init__(
        self,
        *,
        schema_errors: list[str],
        attempts: int,
        raw_output: str | None = None,
        node_name: str = "",
    ) -> None:
        self.schema_errors = schema_errors
        self.attempts = attempts
        self.raw_output = raw_output
        super().__init__(
            detail=f"Schema validation failed after {attempts} attempt(s): {schema_errors}",
            node_name=node_name,
        )


# ---------------------------------------------------------------------------
# Output validation
# ---------------------------------------------------------------------------


def validate_output(output: Any, schema: dict, *, node_name: str = "") -> dict:
    """Validate Claude output against a JSON Schema.

    Handles markdown fence stripping and string-to-dict coercion.
    """
    from jsonschema import validate as jsonschema_validate
    from jsonschema import Draft7Validator, ValidationError

    parsed = output
    if isinstance(output, str):
        stripped = output.strip()
        fence_match = re.search(
            r"```(?:json|python)?\s*\n([\s\S]*?)\n\s*```", stripped
        )
        if fence_match:
            stripped = fence_match.group(1).strip()

        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ClaudeSchemaError(
                schema_errors=[f"Not valid JSON: {exc}"],
                attempts=1,
                raw_output=output,
                node_name=node_name,
            ) from exc

    try:
        jsonschema_validate(instance=parsed, schema=schema)
    except ValidationError as exc:
        errors = [e.message for e in Draft7Validator(schema).iter_errors(parsed)]
        raise ClaudeSchemaError(
            schema_errors=errors or [str(exc.message)],
            attempts=1,
            raw_output=json.dumps(parsed) if not isinstance(output, str) else output,
            node_name=node_name,
        ) from exc

    return parsed


# ---------------------------------------------------------------------------
# SDK session runner
# ---------------------------------------------------------------------------


async def run_claude_session(
    prompt: str,
    *,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-5",
    allowed_tools: list[str] | None = None,
    max_turns: int = 1,
    auth_mode: str | None = None,
    node_name: str = "",
) -> str:
    """Run a single-turn Claude call.

    ``auth_mode`` is ``"max"`` (Claude Code SDK) or ``"api"`` (Anthropic SDK with
    a saved key); ``None`` reads the user's choice from the config file.

    IMPORTANT: Do NOT break/return from inside ``async for message in query()``.
    Let the generator complete naturally to avoid GeneratorExit issues.

    Returns:
        The text output string from Claude.

    Raises:
        ClaudeTaskError: If session fails or returns no result.
    """
    if auth_mode is None:
        from listener import settings
        auth_mode = settings.claude_auth_mode()
    if auth_mode == "api":
        return await _run_api_session(prompt, system_prompt=system_prompt, model=model, node_name=node_name)

    env_overrides: dict[str, str] = {}
    if os.environ.get("ANTHROPIC_API_KEY"):
        env_overrides["ANTHROPIC_API_KEY"] = ""  # force the OAuth login, never a stray key

    options = ClaudeCodeOptions(
        system_prompt=system_prompt or None,
        allowed_tools=allowed_tools if allowed_tools is not None else [],
        permission_mode="bypassPermissions",
        max_turns=max_turns,
        model=model,
        env=env_overrides,
    )

    async def _consume_query() -> ResultMessage | None:
        result: ResultMessage | None = None
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage) and result is None:
                result = message
        return result

    result_message: ResultMessage | None = None
    try:
        result_message = await asyncio.wait_for(_consume_query(), timeout=300)
    except asyncio.TimeoutError:
        raise ClaudeTaskError(
            "SDK call timed out after 300s",
            node_name=node_name,
        )
    except asyncio.CancelledError:
        raise ClaudeTaskError(
            "SDK call cancelled by stale cancel scope",
            node_name=node_name,
        )
    except Exception as exc:
        raise ClaudeTaskError(
            f"SDK error: {exc}",
            node_name=node_name,
        ) from exc

    if result_message is None:
        raise ClaudeTaskError(
            "No ResultMessage received from Claude session",
            node_name=node_name,
        )

    if result_message.is_error:
        raise ClaudeTaskError(
            f"Session error (subtype={result_message.subtype})",
            node_name=node_name,
        )

    if result_message.result is None:
        raise ClaudeTaskError(
            "ResultMessage has no result text",
            node_name=node_name,
        )

    return result_message.result


async def _run_api_session(prompt: str, *, system_prompt: str, model: str, node_name: str) -> str:
    """Single-turn call through the Anthropic SDK using the saved API key."""
    from listener import settings

    api_key = settings.anthropic_api_key()
    if not api_key:
        raise ClaudeTaskError(
            "No Anthropic API key is saved. Add one in Settings, or switch to the Claude Code login.",
            node_name=node_name,
        )
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ClaudeTaskError("The 'anthropic' package is not installed.", node_name=node_name) from exc

    client = anthropic.AsyncAnthropic(api_key=api_key)
    request = {
        "model": model,
        "max_tokens": 16384,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system_prompt:
        request["system"] = system_prompt
    try:
        message = await asyncio.wait_for(client.messages.create(**request), timeout=300)
    except asyncio.TimeoutError:
        raise ClaudeTaskError("API call timed out after 300s", node_name=node_name)
    except Exception as exc:
        raise ClaudeTaskError(f"API error: {exc}", node_name=node_name) from exc

    text = "".join(
        getattr(block, "text", "") for block in getattr(message, "content", []) or []
        if getattr(block, "type", "") == "text"
    )
    if not text.strip():
        raise ClaudeTaskError("Empty response from the API", node_name=node_name)
    return text


# ---------------------------------------------------------------------------
# Schema-validated session runner with retry
# ---------------------------------------------------------------------------


async def run_with_schema_validation(
    prompt: str,
    schema: dict,
    *,
    system_prompt: str = "",
    model: str = "claude-sonnet-4-5",
    node_name: str = "",
    auth_mode: str | None = None,
    max_retries: int = 3,
) -> dict:
    """Run a Claude session with JSON schema validation and self-correction retry.

    Returns:
        The validated output dict matching the schema.

    Raises:
        ClaudeTaskError: If the session itself fails.
        ClaudeSchemaError: If all retries are exhausted.
    """
    augmented_system = (
        f"{system_prompt}\n\n"
        "Return your response as valid JSON matching the schema provided. "
        "Do NOT wrap your response in markdown code fences. "
        "Do NOT include any text outside the JSON object."
    )

    current_prompt = prompt
    last_error: ClaudeSchemaError | None = None

    for attempt in range(1, max_retries + 1):
        try:
            raw_output = await run_claude_session(
                current_prompt,
                system_prompt=augmented_system,
                model=model,
                auth_mode=auth_mode,
                node_name=node_name,
            )
        except ClaudeTaskError as exc:
            logger.warning(
                "SDK call failed attempt %d/%d: %s (node=%s)",
                attempt, max_retries, exc.detail, node_name,
            )
            if attempt < max_retries:
                continue
            raise

        try:
            validated = validate_output(raw_output, schema, node_name=node_name)
            return validated
        except ClaudeSchemaError as exc:
            last_error = exc
            logger.warning(
                "Schema validation failed attempt %d/%d: %s (node=%s)",
                attempt, max_retries, exc.schema_errors, node_name,
            )
            if attempt < max_retries:
                error_detail = "\n".join(exc.schema_errors)
                current_prompt = (
                    f"{prompt}\n\n"
                    f"PREVIOUS ATTEMPT FAILED VALIDATION. Fix these errors:\n"
                    f"{error_detail}\n"
                    f"Previous output (DO NOT repeat these mistakes):\n"
                    f"{exc.raw_output}"
                )

    raise ClaudeSchemaError(
        schema_errors=last_error.schema_errors if last_error else ["unknown"],
        attempts=max_retries,
        raw_output=last_error.raw_output if last_error else None,
        node_name=node_name,
    )
