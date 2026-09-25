"""Click commands for meeting memory (``listener memory ...``).

Defines ``memory_group``; ``listener/cli.py`` registers it. Transcripts and
meta files are read from ``--output-dir`` (default ``./transcripts``).
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from listener import memory

DEFAULT_OUTPUT_DIR = "./transcripts"

_output_dir_option = click.option(
    "--output-dir", default=DEFAULT_OUTPUT_DIR, show_default=True,
    type=click.Path(file_okay=False, path_type=Path), help="Directory with transcript files.",
)


def _read_transcript(output_dir: Path, session_id: str) -> str:
    path = output_dir / f"{session_id}_transcript.md"
    if not path.exists():
        raise click.ClickException(f"Transcript not found: {path}")
    return path.read_text(encoding="utf-8")


def _read_meta(output_dir: Path, session_id: str) -> dict:
    path = output_dir / f"{session_id}_meta.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _echo_json(value: object) -> None:
    click.echo(json.dumps(value, ensure_ascii=False, indent=2))


def _task_line(task: dict) -> str:
    box = "[x]" if task["status"] == "done" else "[ ]"
    line = f"{box} {task['id']}  {task['text']}"
    if task.get("owner"):
        line += f"  — {task['owner']}"
    if task.get("deadline"):
        line += f"  — {task['deadline']}"
    if task.get("stale"):
        line += "  (stale)"
    return f"{line}  ({task['session_id']})"


@click.group(name="memory")
def memory_group() -> None:
    """Grounded meeting memory: generate, browse, search and ask across meetings."""


@memory_group.command("generate")
@click.argument("session_id")
@_output_dir_option
def generate(session_id: str, output_dir: Path) -> None:
    """Generate (or re-generate) the memory of one meeting with Claude."""
    transcript = _read_transcript(output_dir, session_id)
    meta = _read_meta(output_dir, session_id)
    try:
        record = memory.generate_memory(
            session_id, transcript,
            title=str(meta.get("title") or ""), language=str(meta.get("language") or ""),
            transcripts_dir=output_dir,
        )
    except memory.MemoryGenerationError as exc:
        raise click.ClickException(f"Memory generation failed: {exc}") from exc
    tasks = record["tasks"]
    click.echo(f"Memory generated for {session_id} (generation {record['generation_count']}):")
    click.echo(f"  key points: {len(record['key_points'])}, decisions: {len(record['decisions'])}, "
               f"open questions: {len(record['open_questions'])}")
    click.echo(f"  tasks: {len(tasks)} ({sum(t['status'] == 'open' for t in tasks)} open, "
               f"{sum(t['stale'] for t in tasks)} stale)")
    if record["grounding_notes"]:
        click.echo(f"  grounding notes: {len(record['grounding_notes'])}")
    json_path, md_path = memory.memory_file_paths(output_dir, session_id)
    click.echo(f"Files written: {json_path}, {md_path}")


@memory_group.command("show")
@click.argument("session_id")
@click.option("--json", "as_json", is_flag=True, help="Print the raw record as JSON.")
def show(session_id: str, as_json: bool) -> None:
    """Show the memory of one meeting."""
    record = memory.get_memory(session_id)
    if record is None:
        raise click.ClickException(f"No memory for {session_id}. Run: listener memory generate {session_id}")
    if as_json:
        _echo_json(record)
    else:
        click.echo(memory.render_memory_markdown(record), nl=False)


@memory_group.command("tasks")
@click.option("--open", "only_open", is_flag=True, help="Only open tasks.")
@click.option("--done", "only_done", is_flag=True, help="Only done tasks.")
@click.option("--meeting", "session_ids", multiple=True, metavar="SID", help="Filter by meeting (repeatable).")
@click.option("--project", "project_id", default=None, metavar="ID", help="Filter by project.")
@click.option("--include-stale/--no-stale", default=True, show_default=True)
def tasks(only_open: bool, only_done: bool, session_ids: tuple[str, ...],
          project_id: str | None, include_stale: bool) -> None:
    """List to-dos across meetings."""
    if only_open and only_done:
        raise click.UsageError("--open and --done are mutually exclusive.")
    status = "open" if only_open else "done" if only_done else None
    rows = memory.list_tasks(
        session_ids=list(session_ids) or None, status=status,
        project_id=project_id, include_stale=include_stale,
    )
    if not rows:
        click.echo("No tasks found.")
        return
    for task in rows:
        click.echo(_task_line(task))


@memory_group.command("task-done")
@click.argument("task_id")
def task_done(task_id: str) -> None:
    """Mark a task as done."""
    try:
        task = memory.update_task(task_id, status="done")
    except KeyError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_task_line(task))


@memory_group.command("task-edit")
@click.argument("task_id")
@click.option("--text", default=None, help="New task text.")
@click.option("--owner", default=None, help="New owner (empty string clears).")
@click.option("--deadline", default=None, help="New deadline (empty string clears).")
def task_edit(task_id: str, text: str | None, owner: str | None, deadline: str | None) -> None:
    """Edit a task's text, owner or deadline (manual edits win over the AI)."""
    if text is None and owner is None and deadline is None:
        raise click.UsageError("Give at least one of --text, --owner, --deadline.")
    try:
        task = memory.update_task(task_id, text=text, owner=owner, deadline=deadline)
    except (KeyError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(_task_line(task))


@memory_group.command("ask")
@click.argument("question")
@click.option("--meeting", "session_ids", multiple=True, metavar="SID", help="Meeting to ask about (repeatable).")
@click.option("--project", "project_id", default=None, metavar="ID", help="Ask across a project's meetings.")
@click.option("--max-chars", default=40000, show_default=True, type=int, help="Context budget in characters.")
@_output_dir_option
def ask(question: str, session_ids: tuple[str, ...], project_id: str | None,
        max_chars: int, output_dir: Path) -> None:
    """Ask a question across selected meetings (answers come only from meeting memory)."""
    if not session_ids and project_id is None:
        raise click.UsageError("Select meetings with --meeting SID or a project with --project ID.")
    try:
        result = memory.ask(
            question, list(session_ids), project_id=project_id,
            max_chars=max_chars, transcripts_dir=output_dir,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(result["answer"])
    click.echo("\nSources:")
    for source in result["sources"]:
        extra = f" ({source['source']}{', truncated' if source['truncated'] else ''})"
        click.echo(f"  [{source['label']}]{extra}")
    if result["omitted"]:
        click.echo(f"Omitted (over budget): {', '.join(result['omitted'])}")


@memory_group.command("search")
@click.argument("query")
@click.option("--limit", default=20, show_default=True, type=int)
def search(query: str, limit: int) -> None:
    """Full-text search across meeting memory."""
    hits = memory.search_memory(query, limit=limit)
    if not hits:
        click.echo("No matches.")
        return
    for hit in hits:
        title = hit["title"] or "(untitled)"
        click.echo(f"{hit['session_id']}  {title}")
        click.echo(f"    {hit['snippet']}")


@memory_group.group("projects")
def projects() -> None:
    """Group meetings into projects."""


@projects.command("list")
def projects_list() -> None:
    """List projects and their meetings."""
    rows = memory.list_projects()
    if not rows:
        click.echo("No projects.")
        return
    for project in rows:
        click.echo(f"{project['id']}  {project['name']}  ({len(project['session_ids'])} meetings)")
        for sid in project["session_ids"]:
            click.echo(f"    {sid}")


@projects.command("create")
@click.argument("name")
@click.option("--meeting", "session_ids", multiple=True, metavar="SID", help="Meeting to include (repeatable).")
def projects_create(name: str, session_ids: tuple[str, ...]) -> None:
    """Create a project."""
    try:
        project = memory.create_project(name, session_ids)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Created project {project['id']}: {project['name']} ({len(project['session_ids'])} meetings)")


@projects.command("delete")
@click.argument("project_id")
def projects_delete(project_id: str) -> None:
    """Delete a project (meetings and their memory are kept)."""
    if memory.delete_project(project_id):
        click.echo(f"Deleted project {project_id}")
    else:
        raise click.ClickException(f"Unknown project: {project_id}")
