# Implementation Plan: F3 — Custom Analysis Recipes

## Pre-Implementation Checklist

Files the implementor MUST read fresh before starting (they may have been modified by previous features):

- `/Users/alperencngzz/Desktop/listener/listener/web/app.py` — Flask backend (modified by F1, F2, F8, F9, F10)
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — Single-file frontend (modified by F1, F2, F8, F9, F10)
- `/Users/alperencngzz/Desktop/listener/listener/analyzer.py` — Claude analysis module (will be modified)
- `/Users/alperencngzz/Desktop/listener/listener/cli.py` — CLI commands (will be modified)
- `/Users/alperencngzz/Desktop/listener/pyproject.toml` — Dependencies

Things to verify:
- `pyyaml>=6.0` is already in `pyproject.toml` (added by F10 for webhooks config). **No new dependency needed.**
- The `listener/recipes/` directory does NOT exist yet — must be created.
- The `~/.listener/recipes/` directory may not exist — code must handle this gracefully.

## Dependencies

**No new packages needed.** `pyyaml>=6.0` is already in `pyproject.toml` (line 22).

## Implementation Tasks

### Task 1: Create the Recipe data model and loader module

- **File:** `/Users/alperencngzz/Desktop/listener/listener/recipes.py`
- **Action:** CREATE
- **Details:**

```python
"""Custom analysis recipe loader.

Loads built-in recipes from listener/recipes/*.yaml and
user-defined recipes from ~/.listener/recipes/*.yaml.

Recipe = system prompt + user prompt template that tells Claude
how to analyze a meeting transcript.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, asdict

BUILTIN_DIR = Path(__file__).parent / "recipes"
CUSTOM_DIR = Path.home() / ".listener" / "recipes"


@dataclass
class Recipe:
    id: str
    name: str
    category: str
    system_prompt: str
    description: str = ""
    user_prompt_template: str = "Analyze this meeting transcript:\n\n{transcript}"
    model: str = "claude-sonnet-4-5"
    is_builtin: bool = False

    def to_dict(self) -> dict:
        """Serialize for API responses (exclude system_prompt for brevity)."""
        return {
            "id": self.id,
            "name": self.name,
            "category": self.category,
            "description": self.description,
            "model": self.model,
            "is_builtin": self.is_builtin,
        }


def load_recipes() -> list[Recipe]:
    """Load all recipes from built-in and custom directories."""
    recipes = []
    for directory, builtin in [(BUILTIN_DIR, True), (CUSTOM_DIR, False)]:
        if not directory.exists():
            continue
        for f in sorted(directory.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text())
                for entry in data.get("recipes", []):
                    entry["is_builtin"] = builtin
                    recipes.append(Recipe(**entry))
            except Exception:
                continue  # Skip malformed files
    return recipes


def get_recipe(recipe_id: str) -> Recipe | None:
    """Look up a recipe by its ID. Returns None if not found."""
    for r in load_recipes():
        if r.id == recipe_id:
            return r
    return None
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.recipes import load_recipes, get_recipe; print('recipes.py OK')"`

### Task 2: Create built-in recipe YAML definitions

- **File:** `/Users/alperencngzz/Desktop/listener/listener/recipes/default.yaml`
- **Action:** CREATE (must also create the `listener/recipes/` directory)
- **Details:**

First create the directory:
```bash
mkdir -p /Users/alperencngzz/Desktop/listener/listener/recipes
```

Then create `default.yaml` with the following content. All 8 built-in recipes are in a single YAML file. Each system prompt instructs Claude to respond in the same language as the transcript.

```yaml
recipes:
  - id: standard_summary
    name: Standard Summary
    category: general
    description: Comprehensive meeting summary with action items, decisions, and topics
    system_prompt: |
      You are a meeting analyst. You receive a meeting transcript and produce
      a comprehensive, well-structured markdown analysis.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.
      If the meeting is in Turkish, write your analysis in Turkish.
      If in English, write in English. If mixed, use the dominant language.

      Produce the following sections:

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
    user_prompt_template: "Analyze this meeting transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: sales_call
    name: Sales Call Debrief
    category: sales
    description: Deal summary, signals, objections, competitive mentions, next steps
    system_prompt: |
      You are a sales analyst reviewing a sales call transcript. Produce a
      structured debrief in markdown.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.

      Produce the following sections:

      ## Deal Summary
      Brief overview of the deal/opportunity discussed: prospect name, stage, product/service discussed.

      ## Signals & Sentiment
      - Positive buying signals observed
      - Negative or hesitation signals
      - Overall prospect sentiment (Positive / Neutral / Cautious / Negative)

      ## Objections Raised
      List each objection and how it was (or wasn't) addressed.

      ## Competitive Mentions
      Any competitors mentioned, what was said about them, and positioning opportunities.

      ## Next Steps
      Concrete next steps with owners and timelines.

      ## Coaching Notes
      Suggestions for the sales rep: what went well, what could improve, missed opportunities.
    user_prompt_template: "Analyze this sales call transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: sprint_retro
    name: Sprint Retrospective
    category: engineering
    description: Sprint overview, what went well, what didn't, action items, team morale
    system_prompt: |
      You are an agile coach analyzing a sprint retrospective meeting transcript.
      Produce a structured retrospective summary in markdown.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.

      Produce the following sections:

      ## Sprint Overview
      Brief summary of what was discussed about the sprint.

      ## What Went Well
      Bullet list of positive items the team raised.

      ## What Didn't Go Well
      Bullet list of pain points, blockers, or frustrations.

      ## Action Items for Next Sprint
      Checklist of improvement actions with owners (if mentioned).

      ## Team Morale
      Overall assessment of team mood and energy based on the discussion.
    user_prompt_template: "Analyze this sprint retrospective transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: one_on_one
    name: 1:1 Meeting Notes
    category: management
    description: Status updates, blockers, feedback, career development, action items
    system_prompt: |
      You are a management coach analyzing a 1:1 meeting transcript between
      a manager and their direct report. Produce structured meeting notes in markdown.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.

      Produce the following sections:

      ## Status Updates
      Key updates shared during the meeting.

      ## Blockers & Challenges
      Issues raised that need attention or help.

      ## Feedback Exchanged
      Any feedback given in either direction (manager to report or vice versa).

      ## Career & Development
      Career goals, growth areas, or development topics discussed.

      ## Action Items
      Checklist of follow-ups with owners and deadlines (if mentioned).
    user_prompt_template: "Analyze this 1:1 meeting transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: customer_discovery
    name: Customer Discovery
    category: research
    description: Pain points with quotes, current workflow, feature requests, opportunity assessment
    system_prompt: |
      You are a product researcher analyzing a customer discovery interview transcript.
      Produce structured research notes in markdown.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.

      Produce the following sections:

      ## Participant Profile
      Role, company type, and relevant context about the interviewee.

      ## Pain Points
      List each pain point with direct quotes from the transcript (include timestamps like [MM:SS] where available).

      ## Current Workflow
      How the participant currently handles the problem/process discussed.

      ## Feature Requests
      Any explicit feature requests or wishes expressed.

      ## Quotes Worth Saving
      Memorable or insightful quotes with timestamps.

      ## Opportunity Assessment
      Your assessment of the opportunity: problem severity, willingness to pay, urgency.
    user_prompt_template: "Analyze this customer discovery interview transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: interview_debrief
    name: Interview Scorecard
    category: hiring
    description: Candidate assessment, technical skills, culture fit, recommendation
    system_prompt: |
      You are a hiring manager reviewing an interview transcript. Produce a
      structured interview scorecard in markdown.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.

      Produce the following sections:

      ## Candidate Overview
      Name (if mentioned), role being interviewed for, interview type (technical, behavioral, etc.).

      ## Technical / Skill Assessment
      Evaluation of technical skills or domain knowledge demonstrated.

      ## Communication & Culture
      Assessment of communication style, collaboration signals, and cultural fit indicators.

      ## Strengths
      Key strengths observed during the interview.

      ## Concerns
      Any red flags, gaps, or areas of concern.

      ## Questions They Asked
      Notable questions the candidate asked (can indicate engagement and priorities).

      ## Recommendation
      Overall recommendation: **Hire** / **No Hire** / **Further Discussion Needed**
      with brief justification.
    user_prompt_template: "Analyze this interview transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: decision_log
    name: Decision Log
    category: project
    description: Structured log of decisions made, alternatives considered, and open questions
    system_prompt: |
      You are a project manager analyzing a meeting transcript to extract decisions.
      Produce a structured decision log in markdown.

      IMPORTANT: Respond in the SAME LANGUAGE as the transcript.

      For each decision identified, create a section with:

      ## Decisions

      For each decision:
      ### Decision: [short title]
      - **What:** What was decided
      - **Why:** Rationale or context
      - **Alternatives Considered:** Other options discussed (if any)
      - **Owner:** Who is responsible for executing
      - **Timeline:** When it should be done (if mentioned)
      - **Dependencies:** Any blockers or prerequisites

      ## Open Questions
      List questions that were raised but not resolved.
      If no decisions were explicitly made, state that clearly and list
      the topics that were discussed without resolution.
    user_prompt_template: "Extract decisions from this meeting transcript:\n\n{transcript}"
    model: claude-sonnet-4-5

  - id: email_draft
    name: Follow-up Email Draft
    category: general
    description: Ready-to-send follow-up email with summary, action items, and next steps
    system_prompt: |
      You are a professional communication assistant. Based on the meeting transcript,
      draft a follow-up email that could be sent to meeting participants.

      IMPORTANT: Write the email in the SAME LANGUAGE as the transcript.

      Format your output as:

      ## Subject Line
      A clear, concise email subject.

      ## Email Body
      Write a professional but friendly email that includes:
      - Brief greeting
      - 3-5 bullet summary of what was discussed
      - Action items with owners (as a checklist)
      - Next meeting date/time (if mentioned)
      - Closing

      Keep it concise — this should be ready to copy-paste and send.
    user_prompt_template: "Draft a follow-up email for this meeting:\n\n{transcript}"
    model: claude-sonnet-4-5
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.recipes import load_recipes; rs = load_recipes(); print(f'Loaded {len(rs)} recipes'); [print(f'  {r.id}: {r.name} ({r.category})') for r in rs]"`
  - Expected: `Loaded 8 recipes` followed by all 8 recipe names.

### Task 3: Modify analyzer.py to accept a recipe parameter

- **File:** `/Users/alperencngzz/Desktop/listener/listener/analyzer.py`
- **Action:** MODIFY
- **Details:**

The existing `analyze_transcript` and `analyze_transcript_sync` functions currently use hardcoded `SYSTEM_PROMPT` and `PROMPT_TEMPLATE`. We need to add an optional `recipe_id` parameter. When provided, the recipe's `system_prompt` and `user_prompt_template` are used instead.

**Read the current file first** — it currently has:
- `SYSTEM_PROMPT` (line 11-17)
- `PROMPT_TEMPLATE` (line 19-43)
- `analyze_transcript(transcript_text, model)` (line 58-74)
- `analyze_transcript_sync(transcript_text, model)` (line 77-82)

Make these changes:

1. Add a `recipe_id` parameter to `analyze_transcript`:

Replace the `analyze_transcript` function (lines 58-74) with:

```python
async def analyze_transcript(
    transcript_text: str,
    model: str = "claude-sonnet-4-5",
    recipe_id: str | None = None,
) -> str:
    """Analyze a meeting transcript using Claude.

    Args:
        transcript_text: The full transcript text.
        model: Claude model to use.
        recipe_id: Optional recipe ID. If provided, uses the recipe's
            system_prompt and user_prompt_template instead of defaults.

    Returns:
        Markdown-formatted analysis.
    """
    system = SYSTEM_PROMPT
    prompt_tmpl = PROMPT_TEMPLATE

    if recipe_id:
        from listener.recipes import get_recipe
        recipe = get_recipe(recipe_id)
        if recipe:
            system = recipe.system_prompt
            prompt_tmpl = recipe.user_prompt_template
            if recipe.model:
                model = recipe.model

    prompt = prompt_tmpl.format(transcript=transcript_text)

    return await run_claude_session(
        prompt=prompt,
        system_prompt=system,
        model=model,
        node_name="meeting_analysis",
    )
```

2. Update `analyze_transcript_sync` (lines 77-82) to pass through `recipe_id`:

```python
def analyze_transcript_sync(
    transcript_text: str,
    model: str = "claude-sonnet-4-5",
    recipe_id: str | None = None,
) -> str:
    """Synchronous wrapper for analyze_transcript."""
    return asyncio.run(analyze_transcript(transcript_text, model=model, recipe_id=recipe_id))
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.analyzer import analyze_transcript_sync; print('analyzer.py OK')"`

### Task 4: Add `/api/recipes` endpoint to Flask app

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** MODIFY
- **Details:**

**Read this file fresh before editing** — it has been modified by F1, F2, F8, F9, F10.

Add a new API endpoint section. Place it after the existing `api_view` or `api_download` routes (around line 237), before the Chat section:

```python
# ---------------------------------------------------------------------------
# Recipes API (F3)
# ---------------------------------------------------------------------------

@app.route("/api/recipes")
def api_recipes():
    """List all available analysis recipes."""
    from listener.recipes import load_recipes
    recipes = load_recipes()
    return jsonify([r.to_dict() for r in recipes])
```

No other endpoint changes are needed — the recipe selection is passed via the existing `/api/start` endpoint (see Task 5).

### Task 5: Integrate recipe_id into the recording pipeline

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** MODIFY
- **Details:**

**In the `api_start()` function** (currently around line 74), extract the `recipe_id` from the request body. Find the line:
```python
live_transcription = data.get("live_transcription", False)
```
Add after it:
```python
recipe_id = data.get("recipe_id") or None
```

In the `_state.update({...})` block inside `api_start()`, add `"_recipe_id"` to the state dict. Find:
```python
"_skip_analysis": skip_analysis,
```
Add after it:
```python
"_recipe_id": recipe_id,
```

Also add `"_recipe_id"` to the `_INTERNAL_KEYS` set (line 41):
```python
_INTERNAL_KEYS = {"start_time", "_audio_path", "_language", "_model_size", "_skip_analysis", "_recipe_id"}
```

**In the `api_stop()` function** (around line 142), extract `recipe_id` from state. Find:
```python
skip_analysis = _state["_skip_analysis"]
```
Add after it:
```python
recipe_id = _state.get("_recipe_id")
```

Update the thread creation to pass `recipe_id`. Change:
```python
threading.Thread(
    target=_process_recording,
    args=(audio_path, session_id, language, model_size, skip_analysis),
    daemon=True,
).start()
```
To:
```python
threading.Thread(
    target=_process_recording,
    args=(audio_path, session_id, language, model_size, skip_analysis, recipe_id),
    daemon=True,
).start()
```

**In the `_process_recording` function** (around line 412), add the `recipe_id` parameter and pass it to `analyze_transcript_sync`. Change the function signature from:
```python
def _process_recording(audio_path, session_id, language, model_size, skip_analysis):
```
To:
```python
def _process_recording(audio_path, session_id, language, model_size, skip_analysis, recipe_id=None):
```

Find the call to `analyze_transcript_sync` (around line 506):
```python
analysis = analyze_transcript_sync(transcript_text)
```
Change it to:
```python
analysis = analyze_transcript_sync(transcript_text, recipe_id=recipe_id)
```

Also store the recipe info in the meta.json. Find where `meta = {` is built (around line 489). Add a `"recipe_id"` field:
```python
meta = {
    "title": title,
    "language": result.language,
    "language_probability": result.language_probability,
    "duration": result.duration,
    "speakers": speaker_info,
    "analytics": analytics_data,
    "recipe_id": recipe_id,
}
```

- **Verification:** `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; print('app.py OK')"` and then `cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; c=app.test_client(); r=c.get('/api/recipes'); print(r.status_code, r.json[:3] if len(r.json)>3 else r.json)"` (should return 200 with recipe list)

### Task 6: Add recipe dropdown to the frontend settings card

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** MODIFY
- **Details:**

**Read this file fresh before editing.**

#### 6a. Add CSS for recipe dropdown

In the `<style>` section, before the closing `</style>` tag (currently around line 405), add:

```css
/* ---- Recipe Picker (F3) ---- */
.recipe-select{
  padding:7px 10px;border:1px solid #e2e8f0;border-radius:8px;
  font-size:13px;background:#fff;color:#0f172a;outline:none;
  transition:border .2s;cursor:pointer;width:100%;
}
.recipe-select:focus{border-color:#6366f1}
.recipe-select:disabled{opacity:.45;cursor:not-allowed}
.recipe-desc{font-size:11px;color:#94a3b8;margin-top:2px;min-height:15px}
.recipe-badge{
  display:inline-block;font-size:10px;font-weight:600;
  padding:1px 6px;border-radius:4px;margin-left:6px;
  text-transform:uppercase;letter-spacing:.3px;
}
.recipe-badge.builtin{background:#dbeafe;color:#3b82f6}
.recipe-badge.custom{background:#dcfce7;color:#16a34a}
.v-recipe{
  display:inline-flex;align-items:center;gap:4px;
  font-size:11px;color:#6366f1;background:#eef2ff;
  padding:2px 8px;border-radius:4px;font-weight:600;
}
```

#### 6b. Add recipe select to the settings card HTML

In the settings card, find the existing settings-row div (around line 418):
```html
<div class="settings-row">
```

After the Language `<div class="fg">` block (which ends around line 429 with `</select></div>`), and BEFORE the `<div class="cb-row">` for skip-analysis, add:

```html
<div class="fg" style="min-width:200px">
  <label for="recipe">Analysis Recipe</label>
  <select id="recipe" class="recipe-select">
    <option value="">Loading...</option>
  </select>
  <div class="recipe-desc" id="recipe-desc"></div>
</div>
```

#### 6c. Add JavaScript for recipe loading and selection

In the `<script>` section, add a new section after the `loadDevices()` function (around line 532) and before the `// Record` section:

```javascript
// ===========================================================================
// Recipes (F3)
// ===========================================================================
let _recipes = [];

async function loadRecipes(){
  try{
    const r=await f('/api/recipes');
    _recipes=await r.json();
    const s=Q('#recipe');
    let opts='<option value="">Standard Summary (default)</option>';
    const cats={};
    _recipes.forEach(rec=>{
      if(!cats[rec.category])cats[rec.category]=[];
      cats[rec.category].push(rec);
    });
    // Group by category
    const catOrder=['general','sales','engineering','management','research','hiring','project'];
    const catLabels={general:'General',sales:'Sales',engineering:'Engineering',management:'Management',research:'Research',hiring:'Hiring',project:'Project'};
    catOrder.forEach(cat=>{
      const items=cats[cat];
      if(!items)return;
      opts+=`<optgroup label="${catLabels[cat]||cat}">`;
      items.forEach(rec=>{
        // Skip standard_summary in grouped list since it's the default
        if(rec.id==='standard_summary')return;
        const badge=rec.is_builtin?' (built-in)':' (custom)';
        opts+=`<option value="${rec.id}">${esc(rec.name)}${badge}</option>`;
      });
      opts+='</optgroup>';
    });
    s.innerHTML=opts;
    s.onchange=function(){
      const desc=Q('#recipe-desc');
      const sel=_recipes.find(r=>r.id===this.value);
      desc.textContent=sel?sel.description:'Comprehensive meeting summary with action items, decisions, and topics';
    };
    // Trigger initial description
    Q('#recipe-desc').textContent='Comprehensive meeting summary with action items, decisions, and topics';
  }catch(e){
    console.error('Failed to load recipes:',e);
    Q('#recipe').innerHTML='<option value="">Standard Summary (default)</option>';
  }
}
```

#### 6d. Call loadRecipes() on init

Find the init IIFE (around line 515):
```javascript
(async()=>{
  await loadDevices();
  await loadSessions();
  loadWebhooks();
  setInterval(poll, 1200);
})();
```

Change it to:
```javascript
(async()=>{
  await loadDevices();
  await loadRecipes();
  await loadSessions();
  loadWebhooks();
  setInterval(poll, 1200);
})();
```

#### 6e. Pass recipe_id in the start request

Find the `doStart()` function. It builds a `body` object (around line 544):
```javascript
const body={
    device:val('device')!==''?parseInt(val('device')):null,
    language:val('language')||null,
    skip_analysis:Q('#skip-analysis').checked,
    live_transcription:liveEnabled,
};
```

Add `recipe_id` to the body:
```javascript
const body={
    device:val('device')!==''?parseInt(val('device')):null,
    language:val('language')||null,
    skip_analysis:Q('#skip-analysis').checked,
    live_transcription:liveEnabled,
    recipe_id:val('recipe')||null,
};
```

#### 6f. Disable recipe dropdown during recording/processing

In the `setUI()` function, find the block that disables controls (around line 574):
```javascript
const lock=state==='recording'||state==='processing';
Q('#device').disabled=lock;
Q('#language').disabled=lock;
Q('#skip-analysis').disabled=lock;
Q('#live-transcription').disabled=lock;
```

Add:
```javascript
Q('#recipe').disabled=lock;
```

#### 6g. Show selected recipe in the viewer meta line

In the `openViewer()` function, after the meta line is built (around line 658), update it to show the recipe used. Find:
```javascript
if(sess.language) meta+=`<span>${sess.language.toUpperCase()}</span>`;
Q('#v-meta').innerHTML=meta;
```

Change to:
```javascript
if(sess.language) meta+=`<span>${sess.language.toUpperCase()}</span>`;
if(sess.recipe_id){
  const rec=_recipes.find(r=>r.id===sess.recipe_id);
  const recName=rec?rec.name:sess.recipe_id;
  meta+=`<span class="v-recipe">${esc(recName)}</span>`;
}
Q('#v-meta').innerHTML=meta;
```

#### 6h. Include recipe_id in sessions API response

This requires the meta.json to contain `recipe_id` (handled in Task 5). In the sessions list rendering on the frontend, the `recipe_id` is available via `sess.recipe_id` if it was stored in meta.

**In app.py's `api_sessions()` function**, find where meta is parsed (around line 200):
```python
session_map[sid]["title"] = meta.get("title", "")
session_map[sid]["duration"] = meta.get("duration", 0)
session_map[sid]["language"] = meta.get("language", "")
```

Add after:
```python
session_map[sid]["recipe_id"] = meta.get("recipe_id", "")
```

Also add `"recipe_id": ""` to the default session dict creation. Find:
```python
session_map[sid] = {"id": sid, "files": {}, "title": "", "duration": 0, "language": ""}
```

Change ALL occurrences (there are ~3) to:
```python
session_map[sid] = {"id": sid, "files": {}, "title": "", "duration": 0, "language": "", "recipe_id": ""}
```

There are 3 places in `api_sessions()` where this default dict is created — update all of them.

### Task 7: Add recipe option to CLI commands

- **File:** `/Users/alperencngzz/Desktop/listener/listener/cli.py`
- **Action:** MODIFY
- **Details:**

**Read this file fresh before editing.**

#### 7a. Add `--recipe` option to the `record` command

Find the `record_cmd` function decorators (around line 54). Add a new option after `--no-diarize`:

```python
@click.option("--recipe", "-r", default=None,
              help="Analysis recipe ID (run 'listener recipes' to list)")
```

Update the function signature to include `recipe`:
```python
def record_cmd(device, language, model_size, no_analyze, output_dir, hf_token, no_diarize, recipe):
```

Pass `recipe` to `_run_pipeline`. Find the call (around line 143):
```python
_run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
              hf_token=hf_token, no_diarize=no_diarize)
```

Change to:
```python
_run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
              hf_token=hf_token, no_diarize=no_diarize, recipe_id=recipe)
```

#### 7b. Add `--recipe` option to the `transcribe` command

Find the `transcribe_cmd` decorators (around line 151). Add the same option after `--no-diarize`:

```python
@click.option("--recipe", "-r", default=None,
              help="Analysis recipe ID (run 'listener recipes' to list)")
```

Update the signature:
```python
def transcribe_cmd(audio_file, language, model_size, no_analyze, output_dir, hf_token, no_diarize, recipe):
```

Update the call to `_run_pipeline`:
```python
_run_pipeline(audio_file, language, model_size, no_analyze, output_dir, timestamp,
              hf_token=hf_token, no_diarize=no_diarize, recipe_id=recipe)
```

#### 7c. Add `--recipe` option to the `analyze` command

Find the `analyze_cmd` (around line 176). Add:
```python
@click.option("--recipe", "-r", default=None,
              help="Analysis recipe ID (run 'listener recipes' to list)")
```

Update signature:
```python
def analyze_cmd(transcript_file, output, model, recipe):
```

Update the analysis call:
```python
analysis = analyze_transcript_sync(text, model=model, recipe_id=recipe)
```

#### 7d. Add a `recipes` command to list available recipes

Add after the `devices` command (around line 48):

```python
# -----------------------------------------------------------------------
# listener recipes
# -----------------------------------------------------------------------

@cli.command()
def recipes():
    """List available analysis recipes."""
    from listener.recipes import load_recipes

    all_recipes = load_recipes()
    if not all_recipes:
        click.echo("No recipes found.")
        return

    # Group by category
    categories = {}
    for r in all_recipes:
        if r.category not in categories:
            categories[r.category] = []
        categories[r.category].append(r)

    click.echo("Available analysis recipes:\n")
    for cat in sorted(categories.keys()):
        click.echo(f"  [{cat.upper()}]")
        for r in categories[cat]:
            tag = "built-in" if r.is_builtin else "custom"
            click.echo(f"    {r.id:24s} {r.name} ({tag})")
            if r.description:
                click.echo(f"    {'':24s} {r.description}")
        click.echo()

    click.echo("Use: listener record --recipe <id>")
    click.echo("  or: listener analyze --recipe <id> transcript.md")
```

#### 7e. Update `_run_pipeline` to accept and pass recipe_id

Find the `_run_pipeline` function (around line 243). Update signature from:
```python
def _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=None, no_diarize=False):
```
To:
```python
def _run_pipeline(audio_path, language, model_size, no_analyze, output_dir, timestamp,
                  hf_token=None, no_diarize=False, recipe_id=None):
```

Find the call to `analyze_transcript_sync` inside `_run_pipeline` (around line 300):
```python
analysis = analyze_transcript_sync(transcript_text)
```
Change to:
```python
analysis = analyze_transcript_sync(transcript_text, recipe_id=recipe_id)
```

Also, if a recipe is selected, show it to the user. After the `click.echo("\n--- Analysis ---\n")` line, add:
```python
if recipe_id:
    from listener.recipes import get_recipe
    rec = get_recipe(recipe_id)
    if rec:
        click.echo(f"Using recipe: {rec.name}")
    else:
        click.echo(f"Warning: recipe '{recipe_id}' not found, using default analysis")
        recipe_id = None
```

- **Verification:**
  - `cd /Users/alperencngzz/Desktop/listener && python -m listener recipes` — should list 8 recipes
  - `cd /Users/alperencngzz/Desktop/listener && python -m listener record --help` — should show `--recipe` option
  - `cd /Users/alperencngzz/Desktop/listener && python -m listener analyze --help` — should show `--recipe` option

## Frontend Changes Summary

All changes are in `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`:

| Change | Location | What |
|---|---|---|
| CSS | Before `</style>` | Recipe-related styles (`.recipe-select`, `.recipe-desc`, `.recipe-badge`, `.v-recipe`) |
| HTML | Inside settings card, after Language dropdown | Recipe dropdown `<select id="recipe">` with description div |
| JS function | After `loadDevices()` | `loadRecipes()` function that fetches `/api/recipes` and populates dropdown |
| JS init | IIFE | Call `await loadRecipes()` after `loadDevices()` |
| JS doStart | body object | Add `recipe_id: val('recipe') || null` |
| JS setUI | lock controls | Add `Q('#recipe').disabled = lock` |
| JS openViewer | meta line | Show recipe name badge if session has `recipe_id` |

## Flask Endpoint Changes Summary

All changes are in `/Users/alperencngzz/Desktop/listener/listener/web/app.py`:

| Change | What |
|---|---|
| New endpoint | `GET /api/recipes` — returns list of recipe dicts |
| Modified `api_start()` | Extract `recipe_id` from request body, store in `_state` |
| Modified `api_stop()` | Extract `recipe_id` from state, pass to processing thread |
| Modified `_process_recording()` | Accept `recipe_id` param, pass to `analyze_transcript_sync()`, store in meta.json |
| Modified `api_sessions()` | Include `recipe_id` in session data from meta.json |
| Modified `_INTERNAL_KEYS` | Add `"_recipe_id"` |

## Final Verification

Run these commands in order:

```bash
# 1. Verify imports
cd /Users/alperencngzz/Desktop/listener
python -c "from listener.recipes import load_recipes, get_recipe, Recipe; print('recipes.py OK')"

# 2. Verify recipes load correctly
python -c "from listener.recipes import load_recipes; rs=load_recipes(); print(f'{len(rs)} recipes loaded'); assert len(rs)==8, f'Expected 8, got {len(rs)}';"

# 3. Verify get_recipe works
python -c "from listener.recipes import get_recipe; r=get_recipe('sales_call'); print(f'Found: {r.name}, category: {r.category}'); assert r is not None"

# 4. Verify analyzer accepts recipe_id
python -c "from listener.analyzer import analyze_transcript_sync; import inspect; sig=inspect.signature(analyze_transcript_sync); assert 'recipe_id' in sig.parameters; print('analyzer.py OK')"

# 5. Verify Flask app loads
python -c "from listener.web.app import app; print('app.py OK')"

# 6. Verify /api/recipes endpoint
python -c "
from listener.web.app import app
c=app.test_client()
r=c.get('/api/recipes')
assert r.status_code==200
data=r.get_json()
print(f'/api/recipes returns {len(data)} recipes')
assert len(data)==8
print('Recipes:', [d[\"id\"] for d in data])
"

# 7. Verify CLI recipes command
python -m listener recipes

# 8. Verify CLI flags
python -m listener record --help | grep -A1 recipe
python -m listener analyze --help | grep -A1 recipe

# 9. Start web server and visually verify recipe dropdown appears
python -m listener web
# Then open http://127.0.0.1:8642 and check:
# - Recipe dropdown appears in Settings card
# - Dropdown is populated with 8 recipes grouped by category
# - Description updates when selecting a recipe
# - Dropdown is disabled during recording
```

Expected outcomes:
- 8 built-in recipes load from `listener/recipes/default.yaml`
- `/api/recipes` returns JSON array of 8 recipe objects
- Recipe dropdown in UI shows all recipes grouped by category
- Selecting a recipe changes the description text below the dropdown
- Starting a recording with a recipe passes `recipe_id` through the pipeline
- Analysis output uses the selected recipe's system prompt and user prompt template
- CLI commands accept `--recipe` / `-r` flag
- `listener recipes` command lists all available recipes
- Sessions that used a recipe show the recipe name in the viewer metadata
