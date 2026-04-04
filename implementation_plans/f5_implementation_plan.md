# Implementation Plan: F5 — Click-to-Seek Audio Linkage

## Overview

Add click-to-seek functionality that links transcript timestamps to audio playback. When a user clicks any `[HH:MM:SS]` timestamp in the transcript, audio seeks to that position and plays. During playback, the current segment auto-highlights and auto-scrolls. An inline audio player is pinned at the top of the transcript tab.

**Scope:** Frontend-only (single file modification). No backend changes. No new dependencies.

---

## Pre-Implementation Checklist

- [ ] **Read fresh:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html` — this file has been modified by F1, F2, F3, F8, F9, F10. You MUST read it fresh before making any changes.
- [ ] Verify no `seekTo` function already exists in the file
- [ ] Verify no `.seg-active` CSS class already exists in the file
- [ ] Note the exact line content of the `il()` function, `loadTab()` function, and the `.ts` CSS rule

## Dependencies

None. This feature uses only the built-in HTML5 Audio API (`<audio>` element, `currentTime`, `timeupdate` event) which is already available in all modern browsers.

---

## Implementation Tasks

### Task 1: Add CSS for active segment and clickable timestamps

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify
- **Details:** Add the following CSS rules inside the `<style>` block. Insert them right after the existing `.content .ts` rule block (which currently ends around the line containing `padding:1px 5px;border-radius:4px;`).

Find this existing CSS:
```css
.content .ts{
  font-family:'SF Mono',SFMono-Regular,Menlo,monospace;
  font-size:12px;color:#6366f1;background:#eef2ff;
  padding:1px 5px;border-radius:4px;
}
```

Add AFTER it (do not replace it):
```css
.seg-active {
  background: #eef2ff;
  border-left: 3px solid #6366f1;
  padding-left: 8px;
  margin-left: -11px;
  border-radius: 0 6px 6px 0;
  transition: background 0.3s ease;
}
.ts.clickable { cursor: pointer; transition: background 0.15s; }
.ts.clickable:hover { background: #c7d2fe; }
.transcript-audio-bar {
  position: sticky; top: 0; z-index: 5;
  background: #fff; padding: 10px 0 8px;
  border-bottom: 1px solid #e2e8f0; margin-bottom: 10px;
}
.transcript-audio-bar audio { width: 100%; }
.autoscroll-row {
  display: flex; align-items: center; gap: 6px;
  margin-top: 6px; font-size: 12px; color: #64748b;
}
.autoscroll-row label { cursor: pointer; user-select: none; }
```

- **Verification:** Open the HTML file in browser, confirm no CSS syntax errors in DevTools console.

---

### Task 2: Make timestamps clickable in the inline renderer (`il()` function)

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify
- **Details:** The `il()` function is the inline markdown renderer. It currently has two places where `[HH:MM:SS]` timestamps are rendered:

**Change 1 — Standalone timestamps (last line of `il()`):**

Find this exact line inside the `il()` function:
```javascript
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,'<span class="ts">[$1]</span>');
```

Replace it with:
```javascript
    .replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,'<span class="ts clickable" data-ts="$1" onclick="seekTo(\'$1\')">[$1]</span>');
```

**Change 2 — Timestamps inside speaker labels:**

Find this exact line inside the `il()` function (inside the `spkMatch` branch):
```javascript
        return '<span class="ts">[' + ts + ']</span> <span class="spk ' + cls + '">' + speaker + ':</span>';
```

Replace it with:
```javascript
        return '<span class="ts clickable" data-ts="' + ts + '" onclick="seekTo(\'' + ts + '\')">[' + ts + ']</span> <span class="spk ' + cls + '">' + speaker + ':</span>';
```

- **Verification:** After these changes, every timestamp in transcript view should render with `class="ts clickable"` and an `onclick` attribute. Inspect the rendered HTML to confirm.

---

### Task 3: Add `seekTo()` and timestamp parsing functions

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify
- **Details:** Add the following JavaScript functions. Insert them in the `<script>` section, right BEFORE the `// Helpers` section (which starts with `function Q(sel)`). Create a new section header.

```javascript
// ===========================================================================
// Click-to-Seek Audio Linkage (F5)
// ===========================================================================
let _autoScroll = true;
let _transcriptAudio = null;  // reference to the audio element in transcript tab

function parseTimestamp(tsStr) {
  // Parse "MM:SS" or "HH:MM:SS" into seconds
  const parts = tsStr.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return 0;
}

function seekTo(tsStr) {
  const seconds = parseTimestamp(tsStr);
  // Try transcript-embedded audio first, then any audio on page
  let audio = _transcriptAudio || document.querySelector('.transcript-audio-bar audio') || document.querySelector('audio');
  if (!audio) {
    // If no audio player visible, switch to audio source and create one
    console.warn('No audio player found');
    return;
  }
  audio.currentTime = seconds;
  audio.play();
}

function onAudioTimeUpdate() {
  const audio = _transcriptAudio;
  if (!audio) return;
  const currentTime = audio.currentTime;
  const content = Q('#content');
  if (!content) return;

  // Find all clickable timestamps in the content area
  const tsSpans = content.querySelectorAll('.ts.clickable[data-ts]');
  if (tsSpans.length === 0) return;

  // Build list of {element: paragraph, time: seconds} for each segment
  let bestEl = null;
  let bestTime = -1;

  tsSpans.forEach(span => {
    const t = parseTimestamp(span.dataset.ts);
    // Find the containing paragraph or list item (the transcript segment)
    const segEl = span.closest('p') || span.closest('li');
    if (!segEl) return;
    if (t <= currentTime && t > bestTime) {
      bestTime = t;
      bestEl = segEl;
    }
  });

  // Remove old highlights
  content.querySelectorAll('.seg-active').forEach(el => el.classList.remove('seg-active'));

  // Apply highlight to the matching segment
  if (bestEl) {
    bestEl.classList.add('seg-active');
    if (_autoScroll) {
      bestEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }
}

function toggleAutoScroll(cb) {
  _autoScroll = cb.checked;
}
```

- **Verification:** Run `python -c "open('/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html').read()"` to confirm the file is valid (no syntax breaking). Then open in browser and check that `seekTo`, `parseTimestamp`, `onAudioTimeUpdate` are defined in the global scope (type `seekTo` in console).

---

### Task 4: Embed inline audio player in transcript tab (`loadTab()` modification)

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify
- **Details:** Modify the `loadTab()` function to add an audio player above the transcript content when viewing the transcript tab.

Find this block inside `loadTab()`:
```javascript
  c.innerHTML='<p style="color:#94a3b8">Loading...</p>';
  try{
    const r=await f(`/api/view/${fname}`);
    const md=await r.text();
    let legendHtml = '';
    if (tab === 'transcript' && viewerSession?.id) {
      legendHtml = await loadSpeakerLegend(viewerSession.id);
    }
    c.innerHTML = legendHtml + renderMd(md);
  }catch(e){
    c.innerHTML='<p class="empty">Failed to load</p>';
  }
```

Replace it with:
```javascript
  c.innerHTML='<p style="color:#94a3b8">Loading...</p>';
  try{
    const r=await f(`/api/view/${fname}`);
    const md=await r.text();
    let legendHtml = '';
    if (tab === 'transcript' && viewerSession?.id) {
      legendHtml = await loadSpeakerLegend(viewerSession.id);
    }
    // F5: Embed audio player at top of transcript tab
    let audioBarHtml = '';
    if (tab === 'transcript' && viewerSession?.files?.audio) {
      audioBarHtml = '<div class="transcript-audio-bar">'
        + '<audio controls src="/api/download/' + viewerSession.files.audio + '"></audio>'
        + '<div class="autoscroll-row">'
        + '<input type="checkbox" id="autoscroll-cb" checked onchange="toggleAutoScroll(this)">'
        + '<label for="autoscroll-cb">Auto-scroll during playback</label>'
        + '</div></div>';
    }
    c.innerHTML = audioBarHtml + legendHtml + renderMd(md);
    // F5: Attach timeupdate listener to transcript audio
    if (tab === 'transcript') {
      _transcriptAudio = c.querySelector('.transcript-audio-bar audio');
      if (_transcriptAudio) {
        _transcriptAudio.addEventListener('timeupdate', onAudioTimeUpdate);
      }
    }
  }catch(e){
    c.innerHTML='<p class="empty">Failed to load</p>';
  }
```

- **Verification:** Open the web UI, navigate to a session with a transcript and audio file, click the Transcript tab. You should see an audio player pinned at the top of the transcript content area, above the speaker legend and transcript text, with an "Auto-scroll during playback" checkbox below it.

---

### Task 5: Clean up audio reference on tab switch

- **File:** `/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html`
- **Action:** Modify
- **Details:** When switching away from the transcript tab, we should clean up the audio reference to avoid stale event listeners. Modify the `switchTab()` function.

Find this block:
```javascript
function switchTab(btn,tab){
  Q('#tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  btn.classList.add('on');
  // Reset content styles that chat tab may have overridden
  const c = Q('#content');
  if (tab !== 'chat') {
    c.className = 'content';
    c.style.cssText = '';
  }
  loadTab(tab);
}
```

Replace it with:
```javascript
function switchTab(btn,tab){
  Q('#tabs').querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  btn.classList.add('on');
  // F5: Clean up transcript audio reference when leaving transcript tab
  if (_transcriptAudio) {
    _transcriptAudio.removeEventListener('timeupdate', onAudioTimeUpdate);
    _transcriptAudio.pause();
    _transcriptAudio = null;
  }
  // Reset content styles that chat tab may have overridden
  const c = Q('#content');
  if (tab !== 'chat') {
    c.className = 'content';
    c.style.cssText = '';
  }
  loadTab(tab);
}
```

Also modify `closeViewer()` to clean up audio:

Find:
```javascript
function closeViewer(){
  Q('#viewer').classList.remove('open');
  Q('#live-panel').classList.remove('open');
  closeLiveStream();
  viewerSession=null;
  document.querySelectorAll('.sitem').forEach(el=>el.classList.remove('active'));
}
```

Replace with:
```javascript
function closeViewer(){
  // F5: Clean up transcript audio
  if (_transcriptAudio) {
    _transcriptAudio.removeEventListener('timeupdate', onAudioTimeUpdate);
    _transcriptAudio.pause();
    _transcriptAudio = null;
  }
  Q('#viewer').classList.remove('open');
  Q('#live-panel').classList.remove('open');
  closeLiveStream();
  viewerSession=null;
  document.querySelectorAll('.sitem').forEach(el=>el.classList.remove('active'));
}
```

- **Verification:** Switch between tabs (Transcript -> Analysis -> Audio -> Transcript). Audio should stop when leaving the transcript tab, and a fresh player should appear when returning to it.

---

## Summary of All Changes

| File | Action | What changes |
|---|---|---|
| `listener/web/templates/index.html` | **Modify** | CSS: `.seg-active`, `.ts.clickable`, `.transcript-audio-bar`, `.autoscroll-row`. JS: `seekTo()`, `parseTimestamp()`, `onAudioTimeUpdate()`, `toggleAutoScroll()`, `_autoScroll`, `_transcriptAudio` globals. Modified: `il()` (2 lines — make timestamps clickable), `loadTab()` (embed audio player + attach timeupdate), `switchTab()` (cleanup audio), `closeViewer()` (cleanup audio). |

No other files are modified. No backend changes. No new dependencies.

---

## Final Verification

### 1. File syntax check
```bash
python3 -c "open('/Users/alperencngzz/Desktop/listener/listener/web/templates/index.html').read(); print('File reads OK')"
```

### 2. Start the web server
```bash
cd /Users/alperencngzz/Desktop/listener && python -m listener web
```
Expected: Server starts on http://127.0.0.1:8642

### 3. Functional tests (manual, in browser)

1. **Open a session with transcript + audio** — Click any past session that has both files.
2. **Transcript tab shows audio player** — An `<audio>` element should appear at the top of the transcript content, with playback controls and the "Auto-scroll during playback" checkbox (checked by default).
3. **Click a timestamp** — Click any `[MM:SS]` or `[HH:MM:SS]` in the transcript. The audio should jump to that time and start playing. The cursor should be `pointer` on hover, and the timestamp background should turn to `#c7d2fe` on hover.
4. **Auto-highlight during playback** — While audio plays, the transcript segment closest to (and not exceeding) the current playback time should get a left indigo border and light blue background (`.seg-active` class).
5. **Auto-scroll** — During playback, the content area should smoothly scroll to keep the active segment centered.
6. **Disable auto-scroll** — Uncheck the "Auto-scroll during playback" checkbox. Playback should continue, highlighting should continue, but scrolling should stop.
7. **Tab switch cleanup** — While audio is playing in the transcript tab, switch to the Analysis or Audio tab. Audio should stop. Switch back to Transcript — a new player should appear (not playing).
8. **Close viewer cleanup** — Open a session, play audio in transcript tab, then open a different session. Audio should stop.
9. **No audio file** — If a session only has a transcript (no .wav), the transcript tab should still render normally, just without the audio bar at top. Timestamps should not crash on click (they'll log a console warning).

### 4. Verify no regressions
- Speaker labels with timestamps (from F1) should still render correctly AND be clickable
- Chat tab (F2) should still work
- Analytics tab (F8) should still work
- Live transcription (F9) should still work
- Webhooks card (F10) should still render

---

## Edge Cases & Notes

- **Timestamp format:** The regex handles both `MM:SS` and `HH:MM:SS` formats, matching the output of `_fmt_ts()` in `transcriber.py` which produces `HH:MM:SS` when hours > 0, else `MM:SS`.
- **No audio file:** `seekTo()` gracefully handles the case where no audio element exists (logs warning, does nothing).
- **Multiple speakers:** Each speaker line's timestamp is independently clickable. The `seg-active` highlight applies to the whole `<p>` element containing that speaker's segment.
- **Performance:** `timeupdate` fires ~4 times per second. The handler iterates over `data-ts` spans in the DOM. For a typical 1-hour meeting with ~200-500 segments, this is negligible.
- **Sticky audio bar:** The `.transcript-audio-bar` uses `position: sticky; top: 0` so it stays visible while scrolling through the transcript.
