# Implementation Plan: F8 — Meeting Analytics Dashboard

## Pre-Implementation Checklist

**Files the implementor MUST read fresh before starting** (they may have been modified by other features):
- `/Users/alperencngzz/Desktop/listener/listener/web/app.py` — Flask backend (all features modify this)
- `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — Frontend (most features modify this)
- `/Users/alperencngzz/Desktop/listener/listener/diarizer.py` — Has `compute_talk_times()` and `align_speakers()`
- `/Users/alperencngzz/Desktop/listener/listener/transcriber.py` — Has `Segment` dataclass with `speaker` field
- `/Users/alperencngzz/Desktop/listener/pyproject.toml` — Current dependencies

**Things to verify:**
- F1 (Speaker Diarization) is implemented. Confirmed: `listener/diarizer.py` exists with `compute_talk_times()`, `align_speakers()`, etc. The `Segment` dataclass has a `speaker` field. The web app already diarizes and saves speaker info to `meta.json`.
- No new pip dependencies needed — F8 is pure Python computation + Chart.js from CDN.

## Dependencies

**No new packages required.** Per IMPROVEMENTS.md Section 5: "F8: Analytics — Computation is pure Python; Chart.js loaded from CDN."

Chart.js will be loaded in the frontend via CDN:
```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
```

## Overview

F8 adds:
1. A new `listener/analytics.py` module that computes meeting metrics from diarized segments
2. A new `/api/analytics/<session_id>` Flask endpoint
3. Analytics computation integrated into `_process_recording()` in app.py
4. An "Analytics" tab in the session viewer with Chart.js pie/bar/timeline charts

### Metrics to Compute

**From diarization data (segments with speaker labels):**
- Talk time per speaker (seconds and percentage)
- Turn count per speaker (how many times each person spoke)
- Average turn length per speaker
- Interruption count (overlapping speech segments — approximated by very short gaps between different speakers)
- Silence/gap ratio (total silence vs total duration)

**From Claude analysis (optional, only if analysis was run):**
- Topic distribution (parsed from the existing "Topics Discussed" section in analysis)

---

## Implementation Tasks

### Task 1: Create `listener/analytics.py`

- **File:** `/Users/alperencngzz/Desktop/listener/listener/analytics.py`
- **Action:** Create new file
- **Details:**

```python
"""Meeting analytics — compute metrics from diarized transcript segments.

Provides talk-time distribution, turn counts, silence analysis,
and other meeting health metrics for the Analytics Dashboard (F8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from listener.transcriber import Segment


@dataclass
class SpeakerStats:
    """Analytics for a single speaker."""
    talk_time: float = 0.0        # total seconds spoken
    turn_count: int = 0           # number of speaking turns
    avg_turn_length: float = 0.0  # average seconds per turn
    percentage: float = 0.0       # percentage of total talk time

    def to_dict(self) -> dict:
        return {
            "talk_time": round(self.talk_time, 1),
            "turns": self.turn_count,
            "avg_turn_length": round(self.avg_turn_length, 1),
            "percentage": round(self.percentage, 1),
        }


@dataclass
class MeetingAnalytics:
    """Complete analytics for a meeting session."""
    speakers: dict[str, SpeakerStats] = field(default_factory=dict)
    total_duration: float = 0.0
    total_talk_time: float = 0.0
    silence_ratio: float = 0.0
    segment_count: int = 0
    topics: list[dict] = field(default_factory=list)  # from Claude analysis

    def to_dict(self) -> dict:
        return {
            "speakers": {k: v.to_dict() for k, v in self.speakers.items()},
            "total_duration": round(self.total_duration, 1),
            "total_talk_time": round(self.total_talk_time, 1),
            "silence_ratio": round(self.silence_ratio, 2),
            "segment_count": self.segment_count,
            "topics": self.topics,
        }


def compute_analytics(
    segments: list[Segment],
    total_duration: float,
) -> MeetingAnalytics:
    """Compute meeting analytics from diarized segments.

    Args:
        segments: List of Segment objects (with speaker labels from F1).
        total_duration: Total audio duration in seconds.

    Returns:
        MeetingAnalytics with all computed metrics.
    """
    analytics = MeetingAnalytics(
        total_duration=total_duration,
        segment_count=len(segments),
    )

    if not segments:
        return analytics

    # --- Per-speaker accumulation ---
    # Track turns: a "turn" is a contiguous run of segments by the same speaker
    speaker_times: dict[str, float] = {}
    speaker_turns: dict[str, int] = {}

    prev_speaker = None
    for seg in segments:
        label = seg.speaker or "Unknown"
        duration = max(0.0, seg.end - seg.start)

        speaker_times[label] = speaker_times.get(label, 0.0) + duration

        # Count a new turn when the speaker changes
        if label != prev_speaker:
            speaker_turns[label] = speaker_turns.get(label, 0) + 1
            prev_speaker = label

    total_talk = sum(speaker_times.values())
    analytics.total_talk_time = total_talk

    # Silence ratio
    if total_duration > 0:
        analytics.silence_ratio = max(0.0, 1.0 - (total_talk / total_duration))
    else:
        analytics.silence_ratio = 0.0

    # Build SpeakerStats
    for label in sorted(speaker_times.keys()):
        talk = speaker_times[label]
        turns = speaker_turns.get(label, 1)
        pct = (talk / total_talk * 100) if total_talk > 0 else 0.0
        avg_turn = talk / turns if turns > 0 else 0.0

        analytics.speakers[label] = SpeakerStats(
            talk_time=talk,
            turn_count=turns,
            avg_turn_length=avg_turn,
            percentage=pct,
        )

    return analytics


def extract_topics_from_analysis(analysis_text: str) -> list[dict]:
    """Extract topic names from the Claude analysis markdown.

    Looks for the "## Topics Discussed" section and parses bullet items.
    Returns a list of {"name": "Topic Name"} dicts.

    This is a best-effort parser — if the section doesn't exist or
    the format is unexpected, returns an empty list.
    """
    topics = []
    in_topics_section = False

    for line in analysis_text.split("\n"):
        stripped = line.strip()

        # Detect section headers
        if stripped.startswith("## "):
            if "topic" in stripped.lower():
                in_topics_section = True
                continue
            else:
                if in_topics_section:
                    break  # Left the topics section
                continue

        if stripped == "---" and in_topics_section:
            break

        if in_topics_section and stripped.startswith("- "):
            topic_name = stripped[2:].strip().rstrip(".")
            # Remove leading bold markers if present
            if topic_name.startswith("**") and "**" in topic_name[2:]:
                # Extract text inside **...**
                end = topic_name.index("**", 2)
                topic_name = topic_name[2:end]
            if topic_name:
                topics.append({"name": topic_name})

    return topics
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.analytics import compute_analytics, MeetingAnalytics, SpeakerStats; print('OK')"
```

---

### Task 2: Modify `listener/web/app.py` — Add analytics endpoint and integrate into pipeline

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/app.py`
- **Action:** Modify (read the file fresh first!)

#### 2a: Add the `/api/analytics/<session_id>` endpoint

Add this new endpoint **after the existing `/api/speakers/<session_id>` route** (around line 217):

```python
@app.route("/api/analytics/<session_id>")
def api_analytics(session_id):
    """Return analytics data for a session from its meta.json."""
    meta_path = OUTPUT_DIR / f"{session_id}_meta.json"
    if not meta_path.exists():
        return jsonify({"error": "Session not found"}), 404
    try:
        meta = json.loads(meta_path.read_text())
        analytics = meta.get("analytics", {})
        if not analytics:
            return jsonify({"error": "No analytics available (diarization may not have run)"}), 404
        return jsonify(analytics)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
```

#### 2b: Integrate analytics computation into `_process_recording()`

In the `_process_recording()` function, **after** the speaker diarization block and **after** the speaker info computation but **before** writing `meta.json`, add analytics computation.

Find the block that starts with:
```python
        # Save metadata (include speaker info)
        speaker_info = {}
        if result.has_speakers:
```

Replace it with the following expanded version:

```python
        # Compute analytics (requires diarized segments from F1)
        analytics_data = {}
        speaker_info = {}
        if result.has_speakers:
            from listener.diarizer import compute_talk_times
            talk_times = compute_talk_times(result.segments)
            speaker_info = {
                label: {"talk_time_seconds": round(secs, 1)}
                for label, secs in sorted(talk_times.items())
            }

            # F8: Meeting analytics
            try:
                from listener.analytics import compute_analytics
                analytics = compute_analytics(result.segments, result.duration)
                analytics_data = analytics.to_dict()
            except Exception as e:
                logger.warning("Analytics computation failed: %s", e)

        meta = {
            "title": title,
            "language": result.language,
            "language_probability": result.language_probability,
            "duration": result.duration,
            "speakers": speaker_info,
            "analytics": analytics_data,
        }
        (OUTPUT_DIR / f"{session_id}_meta.json").write_text(json.dumps(meta, ensure_ascii=False))
```

**IMPORTANT**: Also add topic extraction after the analysis is saved. Find the block:
```python
            analysis_filename = f"{session_id}_analysis.md"
            (OUTPUT_DIR / analysis_filename).write_text(analysis_md)
            files["analysis"] = analysis_filename
```

Add right after it (before the `with _lock:` that sets status to "done"):

```python
            # F8: Extract topics from analysis and add to analytics
            if analytics_data:
                try:
                    from listener.analytics import extract_topics_from_analysis
                    topics = extract_topics_from_analysis(analysis)
                    if topics:
                        analytics_data["topics"] = topics
                        # Re-save meta with topics
                        meta["analytics"] = analytics_data
                        (OUTPUT_DIR / f"{session_id}_meta.json").write_text(
                            json.dumps(meta, ensure_ascii=False)
                        )
                except Exception as e:
                    logger.warning("Topic extraction failed: %s", e)
```

- **Verification:**
```bash
cd /Users/alperencngzz/Desktop/listener && python -c "from listener.web.app import app; print('App loads OK')"
```

---

### Task 3: Modify `listener/web/templates/index.html` — Add Analytics tab with Chart.js

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify (read the file fresh first!)

#### 3a: Add Chart.js CDN script tag

Add this line **just before the closing `</head>` tag** (before `</style></head>`... actually add it right before the `<style>` closing and after it, before `</head>`):

Find `</style>` and add right after it, before `</head>`:

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
```

#### 3b: Add CSS for the analytics tab

Add these CSS rules **inside the `<style>` block**, before the closing `</style>` tag:

```css
/* ---- Analytics Dashboard (F8) ---- */
.analytics-grid{
  display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:8px;
}
.analytics-card{
  background:#fff;border-radius:10px;padding:14px;
  border:1px solid #e2e8f0;
}
.analytics-card.full{grid-column:1/-1}
.analytics-card h4{
  font-size:12px;font-weight:700;color:#94a3b8;
  text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;
}
.analytics-card canvas{width:100%!important;max-height:220px}
.stat-row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-bottom:1px solid #f1f5f9}
.stat-row:last-child{border-bottom:none}
.stat-label{font-size:13px;color:#475569;display:flex;align-items:center;gap:6px}
.stat-val{font-size:13px;font-weight:600;color:#1e293b}
.stat-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.analytics-summary{
  display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px;
}
.analytics-summary .stat-box{
  flex:1;min-width:100px;background:#f8fafc;border-radius:8px;
  padding:10px 12px;text-align:center;border:1px solid #e2e8f0;
}
.stat-box .stat-num{font-size:20px;font-weight:700;color:#1e293b}
.stat-box .stat-desc{font-size:11px;color:#94a3b8;margin-top:2px}
.topics-list{list-style:none;padding:0}
.topics-list li{
  font-size:13px;color:#475569;padding:4px 0;
  border-bottom:1px solid #f1f5f9;
}
.topics-list li:last-child{border-bottom:none}
.no-analytics{text-align:center;color:#94a3b8;font-size:13px;padding:40px 20px}

@media(max-width:500px){
  .analytics-grid{grid-template-columns:1fr}
}
```

#### 3c: Add the Analytics tab button in the viewer

In the `openViewer()` JavaScript function, find the line that builds audio tab:
```javascript
if(files.audio) tabs+=`<button class="tab" onclick="switchTab(this,'audio')">Audio</button>`;
```

Add this line **right after** that audio tab line:
```javascript
tabs+=`<button class="tab" onclick="switchTab(this,'analytics')">Analytics</button>`;
```

#### 3d: Add the `loadAnalytics()` JavaScript function and integrate with `loadTab()`

In the `loadTab()` function, add an analytics case. Find:
```javascript
  if(tab==='audio'){
```

Add **before** that `if(tab==='audio')` block:
```javascript
  if(tab==='analytics'){
    await loadAnalytics(viewerSession);
    return;
  }
```

Now add the full `loadAnalytics` function and chart helpers. Add this **after the `loadSpeakerLegend` function** (after its closing `}`):

```javascript
// ===========================================================================
// Analytics Dashboard (F8)
// ===========================================================================
const CHART_COLORS = ['#6366f1','#059669','#d97706','#dc2626','#7c3aed','#0891b2','#be185d','#4338ca'];
let _pieChart = null, _barChart = null;

async function loadAnalytics(sess) {
  const c = Q('#content');
  if (!sess?.id) { c.innerHTML = '<p class="no-analytics">No session selected</p>'; return; }

  c.innerHTML = '<p style="color:#94a3b8">Loading analytics...</p>';

  try {
    const r = await f(`/api/analytics/${sess.id}`);
    if (!r.ok) {
      c.innerHTML = '<p class="no-analytics">No analytics available.<br><span style="font-size:12px">Analytics require speaker diarization (F1). Re-process with a HuggingFace token to enable.</span></p>';
      return;
    }
    const data = await r.json();
    renderAnalytics(c, data);
  } catch (e) {
    c.innerHTML = '<p class="no-analytics">Failed to load analytics</p>';
  }
}

function renderAnalytics(container, data) {
  // Destroy old charts
  if (_pieChart) { _pieChart.destroy(); _pieChart = null; }
  if (_barChart) { _barChart.destroy(); _barChart = null; }

  const speakers = data.speakers || {};
  const speakerNames = Object.keys(speakers);
  const hasSpeakers = speakerNames.length > 0;

  // Summary stats
  const durMin = Math.round((data.total_duration || 0) / 60);
  const silPct = Math.round((data.silence_ratio || 0) * 100);
  const segCount = data.segment_count || 0;

  let html = `<div class="analytics-summary">
    <div class="stat-box"><div class="stat-num">${durMin}</div><div class="stat-desc">Minutes</div></div>
    <div class="stat-box"><div class="stat-num">${speakerNames.length}</div><div class="stat-desc">Speakers</div></div>
    <div class="stat-box"><div class="stat-num">${segCount}</div><div class="stat-desc">Segments</div></div>
    <div class="stat-box"><div class="stat-num">${silPct}%</div><div class="stat-desc">Silence</div></div>
  </div>`;

  if (!hasSpeakers) {
    html += '<p class="no-analytics">No speaker data available for charts.</p>';
    container.innerHTML = html;
    return;
  }

  html += '<div class="analytics-grid">';

  // Pie chart: Talk Time Distribution
  html += `<div class="analytics-card">
    <h4>Talk Time</h4>
    <canvas id="pie-chart"></canvas>
  </div>`;

  // Bar chart: Turns Per Speaker
  html += `<div class="analytics-card">
    <h4>Speaking Turns</h4>
    <canvas id="bar-chart"></canvas>
  </div>`;

  // Speaker details table
  html += `<div class="analytics-card full">
    <h4>Speaker Details</h4>`;
  speakerNames.forEach((name, i) => {
    const s = speakers[name];
    const color = CHART_COLORS[i % CHART_COLORS.length];
    const timeStr = fmtDur(s.talk_time);
    html += `<div class="stat-row">
      <span class="stat-label"><span class="stat-dot" style="background:${color}"></span>${esc(name)}</span>
      <span class="stat-val">${timeStr} &middot; ${s.turns} turns &middot; ${s.percentage}%</span>
    </div>`;
  });
  html += '</div>';

  // Topics (if available)
  const topics = data.topics || [];
  if (topics.length > 0) {
    html += `<div class="analytics-card full"><h4>Topics Discussed</h4><ul class="topics-list">`;
    topics.forEach(t => {
      html += `<li>${esc(t.name)}</li>`;
    });
    html += '</ul></div>';
  }

  html += '</div>'; // close analytics-grid
  container.innerHTML = html;

  // Render charts (must happen after DOM insertion)
  setTimeout(() => { renderCharts(speakers, speakerNames); }, 50);
}

function renderCharts(speakers, speakerNames) {
  if (typeof Chart === 'undefined') {
    console.warn('Chart.js not loaded');
    return;
  }

  const colors = speakerNames.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]);
  const talkTimes = speakerNames.map(n => speakers[n].talk_time);
  const turns = speakerNames.map(n => speakers[n].turns);

  // Pie chart
  const pieEl = document.getElementById('pie-chart');
  if (pieEl) {
    _pieChart = new Chart(pieEl, {
      type: 'doughnut',
      data: {
        labels: speakerNames,
        datasets: [{
          data: talkTimes,
          backgroundColor: colors,
          borderWidth: 2,
          borderColor: '#fff',
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 11 } } },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                const secs = ctx.raw;
                const m = Math.floor(secs / 60);
                const s = Math.floor(secs % 60);
                const pct = speakers[ctx.label].percentage;
                return `${ctx.label}: ${m}m ${s}s (${pct}%)`;
              }
            }
          }
        }
      }
    });
  }

  // Bar chart
  const barEl = document.getElementById('bar-chart');
  if (barEl) {
    _barChart = new Chart(barEl, {
      type: 'bar',
      data: {
        labels: speakerNames,
        datasets: [{
          label: 'Turns',
          data: turns,
          backgroundColor: colors.map(c => c + '88'),
          borderColor: colors,
          borderWidth: 1.5,
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              label: function(ctx) {
                const avgLen = speakers[ctx.label].avg_turn_length;
                return `${ctx.raw} turns (avg ${Math.round(avgLen)}s each)`;
              }
            }
          }
        },
        scales: {
          y: { beginAtZero: true, ticks: { stepSize: 1, font: { size: 11 } }, grid: { color: '#f1f5f9' } },
          x: { ticks: { font: { size: 11 } }, grid: { display: false } }
        }
      }
    });
  }
}
```

- **Verification:** Open http://127.0.0.1:8642 in a browser and click on a session that has diarized speakers. The "Analytics" tab should appear and show charts.

---

## Summary of All File Changes

| File | Action | Description |
|---|---|---|
| `/Users/alperencngzz/Desktop/listener/listener/analytics.py` | **Create** | Analytics computation module: `compute_analytics()`, `extract_topics_from_analysis()`, `SpeakerStats`, `MeetingAnalytics` |
| `/Users/alperencngzz/Desktop/listener/listener/web/app.py` | **Modify** | Add `/api/analytics/<session_id>` endpoint; integrate analytics computation into `_process_recording()` |
| `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` | **Modify** | Add Chart.js CDN; analytics CSS; Analytics tab button; `loadAnalytics()` and `renderCharts()` JS functions |

## Handling Missing F1 (Speaker Diarization)

F8 **depends on F1** for speaker data. The implementation gracefully degrades:

1. **`compute_analytics()`** works with or without speaker labels — if all segments have empty `speaker` fields, everything gets attributed to "Unknown" and the analytics still compute (duration, silence ratio, segment count).
2. **`_process_recording()`** only calls `compute_analytics()` inside the `if result.has_speakers:` block, so if diarization wasn't run, `analytics_data` stays as an empty dict `{}`.
3. **The API endpoint** returns a 404 with a helpful message if analytics aren't available.
4. **The UI** shows a friendly message: "No analytics available. Analytics require speaker diarization (F1)."

F1 **is already implemented** (confirmed in `progress.md`), so this fallback is just for cases where a session was recorded without a HuggingFace token.

## Final Verification

After implementing all tasks, run these commands:

```bash
# 1. Verify module imports
cd /Users/alperencngzz/Desktop/listener
python -c "from listener.analytics import compute_analytics, extract_topics_from_analysis, MeetingAnalytics, SpeakerStats; print('analytics.py OK')"

# 2. Verify app loads with new endpoint
python -c "from listener.web.app import app; rules=[r.rule for r in app.url_map.iter_rules()]; assert '/api/analytics/<session_id>' in rules, 'Missing analytics endpoint'; print('Endpoint OK')"

# 3. Verify analytics computation with mock data
python -c "
from listener.transcriber import Segment
from listener.analytics import compute_analytics

segs = [
    Segment(start=0.0, end=10.0, text='Hello', speaker='Speaker 1'),
    Segment(start=10.5, end=25.0, text='Hi there', speaker='Speaker 2'),
    Segment(start=25.5, end=40.0, text='Lets discuss', speaker='Speaker 1'),
]
a = compute_analytics(segs, total_duration=45.0)
d = a.to_dict()
print('Speakers:', list(d['speakers'].keys()))
print('Total duration:', d['total_duration'])
print('Silence ratio:', d['silence_ratio'])
assert len(d['speakers']) == 2
assert d['speakers']['Speaker 1']['turns'] == 2
assert d['speakers']['Speaker 2']['turns'] == 1
print('Analytics computation OK')
"

# 4. Verify topic extraction
python -c "
from listener.analytics import extract_topics_from_analysis
md = '''## Summary
Some stuff

## Topics Discussed
- Budget Review
- Q3 Planning
- **Team Hiring** updates

## Key Decisions
- Something
'''
topics = extract_topics_from_analysis(md)
print('Topics found:', [t['name'] for t in topics])
assert len(topics) == 3
print('Topic extraction OK')
"

# 5. Start web app and verify manually
python -m listener web
# Then open http://127.0.0.1:8642 in browser
# Click on a session with speaker diarization
# Click the "Analytics" tab — should show charts
```

## Expected Behavior

1. **New recording with diarization**: After recording + processing completes, `meta.json` will have an `"analytics"` key with speaker stats. The Analytics tab will show a doughnut chart (talk time), bar chart (turns), speaker details table, summary stats (duration, speakers, segments, silence %), and topics if analysis was run.

2. **Existing sessions**: Old sessions without analytics will show "No analytics available" in the Analytics tab. To add analytics to old sessions, users would need to re-process them (not in scope for F8).

3. **Session without diarization**: The Analytics tab appears but shows a helpful message explaining that diarization is required.
