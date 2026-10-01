"""User-defined tags for meetings.

Tags are short labels the user creates ("client", "trai", "1:1", ...) with an
optional note that explains what the tag means. A meeting is tagged once the
audio exists, before transcription, and the tags travel with the meeting:

* the vocabulary (name + note) lives in ``config.yaml`` under ``tags``;
* a meeting's tags live in its ``<id>_meta.json`` as ``tags: [names]``;
* every Claude call about the meeting (analysis, memory generation, Ask)
  receives the tag names with their notes as *user-provided context*, so the
  agent knows what kind of meeting it is looking at. Tags are guidance from
  the user, never content from the transcript.
"""

from __future__ import annotations

import re
from pathlib import Path

from listener import settings

MAX_NAME_LEN = 40
MAX_NOTE_LEN = 300
MAX_TAGS_PER_MEETING = 20
MAX_NOTES_LEN = 4000

_WS = re.compile(r"\s+")


class TagError(ValueError):
    """Invalid tag name or note."""


def normalize_name(name: object) -> str:
    """Trim, collapse whitespace, strip a leading '#'. Raises TagError when unusable."""
    if not isinstance(name, str):
        raise TagError("tag name must be text")
    cleaned = _WS.sub(" ", name.strip().lstrip("#").strip())
    if not cleaned:
        raise TagError("tag name cannot be empty")
    if len(cleaned) > MAX_NAME_LEN:
        raise TagError(f"tag name is too long (max {MAX_NAME_LEN} characters)")
    if any(ch in cleaned for ch in ",\n\r\t"):
        raise TagError("tag name cannot contain commas or line breaks")
    return cleaned


def _clean_note(note: object) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise TagError("tag note must be text")
    cleaned = _WS.sub(" ", note.strip())
    if len(cleaned) > MAX_NOTE_LEN:
        raise TagError(f"tag note is too long (max {MAX_NOTE_LEN} characters)")
    return cleaned


# ---------------------------------------------------------------------------
# Vocabulary (config.yaml)
# ---------------------------------------------------------------------------


def list_tags() -> list[dict]:
    """All tags the user has created, as ``[{"name", "note"}]`` in creation order."""
    raw = settings.load_config().get("tags")
    out: list[dict] = []
    seen: set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        try:
            name = normalize_name(item.get("name"))
        except TagError:
            continue
        if name.casefold() in seen:
            continue
        seen.add(name.casefold())
        note = item.get("note")
        out.append({"name": name, "note": _WS.sub(" ", str(note).strip()) if isinstance(note, str) else ""})
    return out


def _save_tags(tags: list[dict]) -> None:
    settings.update_config(tags=[{"name": t["name"], "note": t.get("note", "")} for t in tags] or None)


def find_tag(name: str) -> dict | None:
    wanted = normalize_name(name).casefold()
    return next((t for t in list_tags() if t["name"].casefold() == wanted), None)


def upsert_tag(name: str, note: object = None, *, rename_to: str | None = None) -> dict:
    """Create a tag, or update its note (and optionally its name). Returns the tag."""
    clean = normalize_name(name)
    tags = list_tags()
    existing = next((t for t in tags if t["name"].casefold() == clean.casefold()), None)
    # Without an explicit rename the stored spelling wins ("client" stays "client"
    # even when the note is edited as "CLIENT").
    new_name = normalize_name(rename_to) if rename_to else (existing["name"] if existing else clean)
    if new_name.casefold() != clean.casefold() and any(t["name"].casefold() == new_name.casefold() for t in tags):
        raise TagError(f"a tag named “{new_name}” already exists")
    if existing is None:
        existing = {"name": new_name, "note": _clean_note(note)}
        tags.append(existing)
    else:
        existing["name"] = new_name
        if note is not None:
            existing["note"] = _clean_note(note)
    _save_tags(tags)
    return dict(existing)


def delete_tag(name: str) -> bool:
    """Remove a tag from the vocabulary. Meetings keep the name (see ``strip_tag``)."""
    wanted = normalize_name(name).casefold()
    tags = list_tags()
    kept = [t for t in tags if t["name"].casefold() != wanted]
    if len(kept) == len(tags):
        return False
    _save_tags(kept)
    return True


def ensure_tags(names: list[str]) -> list[str]:
    """Normalise names, add unknown ones to the vocabulary, return canonical names."""
    tags = list_tags()
    by_key = {t["name"].casefold(): t for t in tags}
    canonical: list[str] = []
    added = False
    for raw in names:
        clean = normalize_name(raw)
        key = clean.casefold()
        if key not in by_key:
            by_key[key] = {"name": clean, "note": ""}
            tags.append(by_key[key])
            added = True
        name = by_key[key]["name"]
        if name not in canonical:
            canonical.append(name)
    if added:
        _save_tags(tags)
    return canonical


# ---------------------------------------------------------------------------
# Per-meeting tags (meta.json)
# ---------------------------------------------------------------------------


def parse_tag_list(value: object) -> list[str]:
    """Accept a list of names or a comma-separated string; returns normalised names."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        parts = [p for p in value.split(",")]
    elif isinstance(value, list):
        parts = value
    else:
        raise TagError("tags must be a list of names or a comma-separated string")
    names: list[str] = []
    for part in parts:
        if isinstance(part, str) and not part.strip():
            continue
        clean = normalize_name(part)
        if clean.casefold() not in {n.casefold() for n in names}:
            names.append(clean)
    if len(names) > MAX_TAGS_PER_MEETING:
        raise TagError(f"a meeting can have at most {MAX_TAGS_PER_MEETING} tags")
    return names


def meeting_tags(meta: dict | None) -> list[str]:
    """The tag names stored on a meeting's meta (unknown shapes yield [])."""
    raw = (meta or {}).get("tags")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            try:
                name = normalize_name(item)
            except TagError:
                continue
            if name.casefold() not in {n.casefold() for n in out}:
                out.append(name)
    return out


def set_meeting_tags(output_dir: Path, session_id: str, names: list[str]) -> list[str]:
    """Store tags on a meeting; names not in the vocabulary are created."""
    from listener.pipeline import update_meta
    canonical = ensure_tags(parse_tag_list(names))
    update_meta(output_dir, session_id, tags=canonical)
    return canonical


def strip_tag(output_dir: Path, name: str) -> int:
    """Remove a tag from every meeting's meta under ``output_dir``; returns the count."""
    from listener.pipeline import META_LOCK, read_meta, update_meta
    wanted = normalize_name(name).casefold()
    changed = 0
    for meta_path in sorted(Path(output_dir).glob("*_meta.json")):
        session_id = meta_path.name[: -len("_meta.json")]
        with META_LOCK:
            current = meeting_tags(read_meta(output_dir, session_id))
            kept = [n for n in current if n.casefold() != wanted]
            if len(kept) != len(current):
                update_meta(output_dir, session_id, tags=kept)
                changed += 1
    return changed


# ---------------------------------------------------------------------------
# What the agents see
# ---------------------------------------------------------------------------


def describe(names: list[str]) -> list[str]:
    """One line per tag: ``name — note`` (or just ``name``), notes from the vocabulary."""
    notes = {t["name"].casefold(): t["note"] for t in list_tags()}
    lines = []
    for name in names:
        note = notes.get(name.casefold(), "")
        lines.append(f"{name} — {note}" if note else name)
    return lines


def prompt_block(names: list[str]) -> str:
    """Context paragraph for Claude prompts, or '' when the meeting has no tags."""
    lines = describe(names)
    if not lines:
        return ""
    return ("Tags the user attached to this meeting before processing (context about what kind of "
            "meeting this is; they are guidance from the user, not part of the transcript):\n"
            + "\n".join(f"- {line}" for line in lines))


def clean_notes(notes: object) -> str:
    """Free-text notes the user wrote for Claude; trimmed and bounded."""
    if notes is None:
        return ""
    if not isinstance(notes, str):
        raise TagError("notes must be text")
    cleaned = notes.replace("\r\n", "\n").strip()
    if len(cleaned) > MAX_NOTES_LEN:
        raise TagError(f"notes are too long (max {MAX_NOTES_LEN} characters)")
    return cleaned


def meeting_notes(meta: dict | None) -> str:
    raw = (meta or {}).get("notes")
    return raw.strip() if isinstance(raw, str) else ""


def context_block(names: list[str], notes: str | None = None) -> str:
    """Everything the user gave as guidance for one meeting: tags (with notes) and free-text notes."""
    parts = []
    tags_part = prompt_block(names)
    if tags_part:
        parts.append(tags_part)
    text = (notes or "").replace("\r\n", "\n").strip()
    if text:
        parts.append("Notes the user wrote about this meeting before processing (context and guidance "
                     "from the user, not part of the transcript):\n" + text)
    return "\n\n".join(parts)
