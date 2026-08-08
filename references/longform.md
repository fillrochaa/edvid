# LONGFORM track (YouTube 16:9) — cut philosophy + Phase 2 + captions reference

Read this file when the source is horizontal / the user says YouTube, longform,
tutorial or vlog. Reuses the whole Phase-1 engine and Phase-3 soundtrack —
what changes is the cut intent, output spec, captions, and the Phase-2 visuals.

## Deltas vs short-form

| | Short-form | **Longform** |
|---|---|---|
| Aspect / res | 1080×1920 @ 24 | **16:9 at source res/fps** (`render.py --keep-resolution`) |
| Cut goal | max compression | **retention arc**, keep pacing/breath |
| Silence trim | aggressive (30–80ms pads) | **gentle** — cut fillers/mistakes/dead-air > ~0.8s; keep 300–600ms beats |
| Captions | karaoke burned | **`.srt` for YouTube CC** (not burned) — `captions_srt.py` |
| Hook | static 4s headline | **cold open** (5–15s tease) + intro |
| Camera | hard-zoom dynamic | mostly static; **B-roll cutaways** carry variety |
| Template | `assets/shortform/` | **`assets/longform/`** |

## Phase 1 — cut for retention, not compression

Same helpers (`transcribe` → `pack` → editor sub-agent → `speech_regions` →
`render`), different intent:

1. **Retention arc**, assembled by argument, not clip order:
   - **COLD OPEN (0–15s):** the single most compelling line from *anywhere* —
     payoff tease, bold claim, the "after". Retention make-or-break.
   - **INTRO:** who/what/the promise. Short.
   - **BODY:** chapters, each opening a mini-hook and closing a loop.
   - **PAYOFF → OUTRO/CTA:** deliver, then CTA. **Last ~20s visually calm**
     (YouTube end cards).
2. **Gentle silence pass:** cut fillers, false starts, restarts, dead air
   > ~0.8–1.0s only. Longform cut as tight as a Reel feels breathless.
3. **Re-hooks / open loops** at section turns ("mas antes…", tease what's next)
   so retention survives the mid-video dip.
4. **Editor sub-agent brief:** longform archetypes — tutorial
   (INTRO→SETUP→STEPS→GOTCHAS→RECAP), talking-head essay
   (COLD-OPEN→THESIS→POINTS→COUNTER→CONCLUSION→CTA), vlog
   (COLD-OPEN→ARRIVAL→BEATS→REFLECTION). Tell it to place a cold open and to
   label chapter starts with `"chapter": "Title"` on the opening range.
5. **Render at source spec:** `render.py edl.json -o cut.mp4 --no-subtitles
   --keep-resolution` (+ `--voice-master` as usual; −14 LUFS is YouTube's
   target too). Verify with `verify_cut.py … --min-silence 1.2` (longform keeps
   breathing room — don't flag natural beats).
6. **Tutorial / screen-record:** the capture is the base video; add crop-zoom
   into the active UI region + callouts in Phase 2 rather than a face camera.

## Chapters + .srt (ship with the video)

- `chapters.py <edit>/edl.json -o <edit>/chapters.txt` — YouTube description
  block from the EDL `"chapter"` fields. YouTube rules (validated by the
  helper): first stamp `00:00`, ≥ 3 chapters, each ≥ 10s.
- `transcribe.py cut.mp4 --edit-dir <edit>` then
  `captions_srt.py --transcript <edit>/transcripts/cut.json -o <edit>/captions.srt`
  — broadcast-style cues (≤42 chars/line, ≤2 balanced lines, 1–6s, sentence
  case). Uploaded as CC, never burned.

## Phase 2 — 16:9 visuals (data-driven template)

Scaffold + describe, same pattern as short-form:

```bash
cp -R <skill>/assets/longform/. <edit>/remotion/ && cd <edit>/remotion && npm install
cp ../cut.mp4 public/
```

Write `public/edit-data.json` (schema in `assets/longform/README.md`):
width/height/fps/durationSec **matching cut.mp4 exactly**, accent color, and
the four layers — graphics **punctuate, they don't saturate**:

- **broll[]** — full-frame image (Ken-Burns) or muted video cutaways over
  narration; the core of longform visual variety. Sync to what's said.
- **lowerThirds[]** — name/title card slides in bottom-left, ~3–4s.
- **chapters[]** — title card at each chapter start (mirrors chapters.txt).
- **callouts[]** — accent chip at a normalized x/y for emphasis
  ("2x mais rápido"). Occasional — NOT karaoke.

Reuse short-form extras sparingly if a moment calls for it (dynamic camera,
behind-the-subject, SFX) — longform ≠ Reel density. Verify with ONE
`contact_sheet.py` over the graphic moments, render
`npx remotion render Longform out/render.mp4`, loudnorm → `edit/final.mp4`.

Never edit `src/Main.tsx` — it's data-driven; the JSON is the edit.

---

## Anti-patterns (Fase 2/3)

**Espelhados de `shortform.md` — mudou um, muda o outro.** Valem para os dois
tracks; os que dependem da aba Estilo ficaram só no short-form, que é onde ela
existe.

- Hardcoding a bespoke graphic's timings inside `CustomGraphics.tsx`. Put the
  windows in an `edit-data.json` array (a key the template ignores, e.g.
  `splitInserts`) and map over it — otherwise the graphic is invisible to the
  preview timeline and the user cannot see or retime it.
- Re-rendering Phase 1 without regenerating `segments.json`. Every Phase-2
  overlay that must land on a cut is indexed off that file; stale, it is off by
  frames and nothing errors. Worse, a `VIDEO_LAG`-style constant can absorb the
  first frame of the drift and make a broken file look correct at the one
  boundary you happen to check.
- Delivering Phase 2 with Remotion's own audio track — it drifts progressively against the source (+0.66s by 78s on a 95s edit). Re-mux `cut.mp4`'s audio and mix the soundtrack in ffmpeg (recipe in the track reference).
- Judging A/V sync with short correlation windows — speech is quasi-periodic and a 2–3s window happily locks onto the wrong syllable, inventing a drift. Use 15s+ windows, and remember a PARTIAL render cannot show drift that accumulates over the full timeline.
- Indexing Phase 2 off `Σ(end−start)` when a `jcut_timeline` exists — the J-cut output is shorter, so everything after the first take lands late.

---

## Helpers de Fase 2/3

