---
name: edvid
description: Conversation-driven video editing for short-form vertical (Reels, TikTok, Shorts) and longform horizontal video. Use when asked to cut, grade, caption, add graphics, create a soundtrack, transcribe, or prepare video edits. Run Phase 1 (audio-led clean cut and grade), obtain approval, then build Phase 2/3 Remotion visuals and audio.
---

# Edvid

## Principle

1. **Two phases, one gate between them.** PHASE 1 is the clean cut + color grade; PHASE 2 is captions, graphics and images. (Hard Rule 1 enforces the gate.)
2. **LLM reasons from raw transcript + on-demand visuals.** The only derived artifact that earns its keep is the packed phrase-level transcript (`takes_packed.md`). Everything else you derive at decision time.
3. **Audio is primary, visuals follow.** Cut candidates come from speech boundaries and silence gaps.
4. **Ask → confirm → execute → iterate → persist.** Never touch the cut until the user confirms the strategy in plain English.
5. **Generalize.** Look at the material, ask the user, then edit — never assume what kind of video it is.
6. **Artistic freedom is the default.** Specific values here are worked examples, not mandates. Only the Hard Rules are mandatory.
7. **Spend tokens where taste lives.** Machine data is for programs, not for reading; verification is numeric before it is visual. Your attention is the scarce resource — put it on the edit, not on parsing JSON. (Hard Rules 12 and 13 say what this forbids.)

## Hard Rules (production correctness — non-negotiable)

1. **The phase gate is real.** No Phase-2 work before the cut is approved.
2. **Per-segment extract → lossless `-c copy` concat**, never a single-pass filtergraph. (Under the default J-cut the picture and the sound of a take are extracted as separate ranges and the audio tracks are summed — that is the one sanctioned mix, and the video path is still per-segment + lossless concat.)
3. **30ms audio fades at every segment boundary** (encoded in render.py).
4. **Never cut inside a word** — snap to word boundaries from the transcript.
5. **Pad every cut edge** (30–200ms window; trail slightly longer than lead). Cut on silence whenever possible.
6. **Cache transcripts per source.** Never re-transcribe unless the source changed.
7. **Color grade per-segment during extraction**, never post-concat.
8. **Strategy confirmation before execution.**
9. **All session outputs in `<videos_dir>/edit/`** — never inside the edvid repo.
10. **PHASE 2 is Remotion-only** — no ffmpeg/PIL burned text or overlays.
11. **PHASE 2 is data-driven.** Scaffold by copying the track template; describe the video in `public/edit-data.json`. **Never read or edit the template TSX** (`src/Main.tsx` etc.) — the only editable code file is `src/CustomGraphics.tsx`, only for bespoke graphics.
12. **Verify numerically first.** Run `verify_cut.py` on every rendered cut; open images only for flagged junctions. Batch any multi-frame look into one `contact_sheet.py` / `grade.py --candidates` montage.
13. **Never Read machine data into context**: `transcripts/*.json` (raw), `captions.json`, `track.json`, `segments.json`, matte/track binaries. Read `takes_packed.md` and helper stdout instead.

## Execution medium — ffmpeg pipeline (default) vs Adobe Premiere (MCP)

The default engine is the ffmpeg/Remotion pipeline below. **If the user asks for the
edit inside Adobe Premiere Pro via the `premiere-pro` MCP** ("edite a sequência no
Premiere", "corte via MCP"), the METHOD is unchanged — audio-primary, cut on
silence, phase gate, grade with taste — but the hands change: **read
`references/premiere-mcp.md`**. Transcription and `edl.json` are identical and
cached, so reuse an approved EDL and skip `cut.mp4`/preview.

## Directory layout

```
<videos_dir>/
├── <source files, untouched>
└── edit/
    ├── project.md               ← memory; appended every session
    ├── takes_packed.md          ← phrase-level transcripts, the primary reading view
    ├── edl.json                 ← cut decisions (Phase 1)
    ├── transcripts/<name>.json  ← cached word-level transcripts (WhisperX, local)
    ├── clips_graded/            ← per-segment extracts with grade + fades
    ├── cut.mp4                  ← PHASE 1 output: clean graded cut (approval artifact)
    ├── verify/                  ← montages / flagged-boundary views
    ├── captions.srt + chapters.txt   ← longform deliverables
    ├── final.mp4                ← delivered render (Phase 2 + 3, loudnorm'd)
    └── remotion/                ← Remotion project (Phase 2 + 3)
        ├── public/              ← cut.mp4, edit-data.json (THE edit), captions.json,
        │                          track.json, segments.json, pexels/ web/ brand/, sfx/, trilha.mp3
        └── src/                 ← immutable template code + CustomGraphics.tsx
```

## Setup

First-time install lives in `install.md`. On cold start just verify:

- **Transcription needs NO API key.** WhisperX ships as a dependency: `uv sync` is the whole install. It transcribes with Whisper and then runs FORCED ALIGNMENT (wav2vec2) against the waveform, so word times are measured, not inferred — 93% of words land inside a real speech region and the end of speech is placed within 10ms (measured against `speech_regions.py`). Models download once on first run and cache. Speed is ~realtime and improves with length (loading the model is a fixed ~18s: a 16s clip took 23s, a 166s source took 109s).
- A `/UNALIGNED` suffix in `_transcription_backend` means no wav2vec2 model existed for the detected language, so the word times are the decoder's own — coarse, and not safe for Phase-2 karaoke captions. Say so if it happens.
- `ffmpeg` + `ffprobe` on PATH; Python deps (`uv sync`); Node 18+ for Phase 2. `yt-dlp` ships with the Python deps, so URL sources need no extra install.
- The `remotion-best-practices` skill for Phase-2 domain knowledge (install from https://github.com/remotion-dev/skills if missing).
- Phase 2/3 can use optional API keys (illustrative images, AI soundtrack). They are listed in the track reference, asked for lazily when the feature is first used, and never at install time. **Nothing in Phase 1 needs a key.**

Helpers live in `helpers/`, resolved relative to this SKILL.md (usually `~/.claude/skills/edvid/` or `~/.codex/skills/edvid/`, or a symlink/junction pointing there). Run them as `uv run python helpers/<name>.py` — a bare `python` misses the `.venv` that `uv sync` builds.

## Helpers

Phase 1:
- **`ingest_url.py <url> --dest <videos_dir> [--section 12:00-25:30] [--max-height 1080]`** — edit from a link: yt-dlp → MP4 (≤1080p) straight into the videos dir; from there it's a source like any other. `--section` downloads ONLY a time range of a longform source (keyframe-accurate) — the cheap way to clip minutes 12–25 of a 1h video. `--simulate` prints title/duration/resolution without downloading (confirm before big fetches; run those in the background).
- **`transcribe.py <video> --edit-dir <edit> [--language pt] [--model large-v3-turbo]`** — word-level, cached, local (WhisperX + forced alignment). No cap, no chunking. Pass `--language` when you know it — auto-detect costs a pass and can pick wrong on a short clip.
- **`transcribe_batch.py <videos_dir>`** — every source in a directory, one at a time (local inference already uses every core). Per-file cached, and a failure on one source doesn't lose the ones already done.
- **`pack_transcripts.py --edit-dir <dir>`** — transcripts → `takes_packed.md` (phrase-level, breaks on ≥0.5s silence). **The** reading view: 1/10 the tokens of raw JSON.
- **`speech_regions.py <video>`** — acoustic speech intervals via silencedetect. The source of truth for cut EDGES (Whisper times drift/stretch). Answers *where* speech is — never *how loud* it is.
- **`voice_levels.py <video> [--edit-dir <dir>] [--edl edl.json] [--drop-db 5]`** — the source of truth for speech LEVEL. Flags every phrase, run and EDL range ≥5 dB under the speaker's own median and sizes a `gain_db`. It catches what nothing else does: a whispered aside where the transcript is perfect, `speech_regions` says "speech" and `verify_cut` finds no fault — and the viewer still cannot hear it. **Run it before writing the EDL.**
- **`detect_color.py <video> [--json]`** — resolves NORMAL vs LOG from the file instead of asking — metadata first, image statistics when the metadata is silent (common: a transcode drops the tags). Returns profile, **confidence**, evidence and the `grade` to apply. Only `confidence: low` should send you back to the user; the identification detail is in `references/log-grade.md`.
- **`render.py <edl.json> -o cut.mp4 --no-subtitles [--voice-master] [--keep-resolution] [--jobs N] [--no-jcut] [--jcut-lead N] [--jcut-tail-trim N]`** — per-segment extract (grade + fades, **parallel**) → **J-cut overlap assembly (default)** or lossless concat → optional voice master → loudnorm. Writes `jcut_timeline` into the EDL: the real output positions, which is what everything downstream must index off. Short-form fps is automatic: **30fps for 30fps+ sources, else 24** (longform keeps source fps via `--keep-resolution`). Set `edit-data.json` `fps` to match the resulting `cut.mp4`.
- **`verify_cut.py <edl.json> <cut.mp4> [--min-silence 1.2]`** — numeric self-eval: duration, per-junction pop/clipped-word probes, dead air, black frames, clipping, **and range level balance** (each range's RMS vs the median range; `LOW-LEVEL` under −4 dB). ~350 tokens of text instead of N images. The range-balance line is the convergence test for a `gain_db` fix.
- **`grade.py <in> -o <out>`** — grade presets/raw filters. **`--candidates "a=<filter>;b=<preset>;original=" --frame <t> -o cmp.png`** renders N looks on the SAME frame into one labeled montage.
- **`timeline_view.py <video> <start> <end>`** — filmstrip+waveform PNG for ONE flagged spot, not a scan tool.
- **`contact_sheet.py <video> --times t1 t2 … -o sheet.png`** — N frames in one labeled grid; the way to eyeball several moments **you already know**.
- **`watch_video.py <video> [--mode scene|keyframe|uniform] [--times t1 t2 …] [--start/--end] [--max-frames 24]`** — "what is IN this footage?" when you *don't* know where to look: scene detection + perceptual dedup → labeled contact sheets in `edit/verify/watch_<stem>/`, one Read per sheet. For visual inventory of unknown material, and for surveying `cut.mp4` beyond verify_cut's numbers. `--times` pins transcript-cue frames: deictic moments from `takes_packed.md` ("olha isso", "como você pode ver") are LOW visual change and invisible to scene detection — pin them to decide B-roll/callout/zoom placement in Phase 2.

Phase 2/3 helpers (captions, face tracking, image search, music) are listed in
the track reference you load after the gate.

Interface:
- **`preview_server.py --root <edit> [--port 4820]`** — serves the standard preview interface (see the Preview interface section). App code lives at `assets/preview/` and is IMMUTABLE.
- **`start_preview.py --root <edit> [--port 4820]`** — starts the same server as a detached UTF-8 process and writes `preview-server.log` / `preview-server.err.log` in the edit directory. Prefer it on Windows and whenever the host cannot keep a foreground command alive.

## Preview interface (standard — launch it at the start of every edit)

Every edit session gets the same interactive interface in the user's preview panel: a video-editor timeline (video track with filmstrip + audio track with waveform), a live playhead that scrubs the render in real time, per-take trim handles and take removal, and — from Phase 2 — caption and insert tracks. The layout follows the source aspect on its own: **vertical** sources put a tall player on the right with the transport + timeline on the left; **horizontal** sources keep the player stacked above the timeline. Dark glass, Edvid brand. **Never build a UI per session** — feed the standard interface with `state.json`. Editing `assets/preview/` is allowed only when the user asks for a UI change; it is shared, so the improvement lands for every project.

**Launch (do this when a session starts, even before the first render — the UI shows a waiting state):**
1. Write `<edit>/state.json`:
   ```json
   {"project": "Nome — C0000", "phase": 1, "video": "cut.mp4", "edl": "edl.json",
    "captions": "remotion/public/captions.json", "editData": "remotion/public/edit-data.json",
    "finalVideo": "final.mp4", "fps": 24, "message": "Fase 1 — cortando",
    "sourceDurations": {"C0000": 1038.5},
    "awaitingStyle": false,
    "style": {"edit": "split", "captions": "karaoke",
              "elements": {"tracking": false, "zoomAuto": true, "zoomCuts": true, "musicAI": false}}}
   ```
   (`captions`/`editData`/`finalVideo` only when they exist; the Fase-2 tab plays `finalVideo` — the render WITH captions/inserts — while Fase 1 plays the clean cut; `sourceDurations` lets the UI clamp take extensions; `awaitingStyle`/`style` drive the Estilo tab below.)
2. Select the runtime adapter; never invoke another host's tools.

   - **Claude Code:** ensure `.claude/launch.json` has the config (adjust `--root` per session). The server takes the port by flag only, so pass the harness-assigned `$PORT` and set `autoPort`: `{"name": "edvid-preview", "runtimeExecutable": "sh", "runtimeArgs": ["-c", "exec python3 <skill>/helpers/preview_server.py --root '<edit>' --port \"$PORT\""], "autoPort": true, "port": 4820}`. Run `preview_start` with name `edvid-preview`, then arm `Monitor(command="python3 <skill>/helpers/watch_edits.py '<edit>'", description="escolhas e marcações salvas no preview", persistent=true)` **in the same turn**.
   - **Codex desktop / CLI:** start `uv run python -X utf8 helpers/start_preview.py --root '<edit>' --port 4820`. It detaches safely on Windows and Unix, preserves paths with spaces/non-ASCII characters, and leaves logs in the edit directory. Once it responds, use the `control-in-app-browser` skill to open `http://127.0.0.1:4820` in the in-app Browser automatically and leave that tab on the preview. Do not assume `preview_start`, `Monitor`, or `.claude/launch.json` exists. If the Browser capability or a persistent local process is unavailable, give the user the command and local URL instead. After showing the preview, tell the user to save and reply in this task. On every subsequent user message, before any other work, check for `preview_edits.json` and `preview_style.json`; read, validate, apply, then remove only `preview_edits.json`.

   A preview save must always lead to an agent-visible action: Claude Code uses the persistent watcher; Codex uses the user's next task message as the notification boundary.

**Preview aberto e vazio para sempre = permissão, não render.** On macOS the
privacy layer guards `~/Documents`, `~/Desktop`, `~/Downloads` and iCloud Drive:
an app that was never granted Files-and-Folders access gets `PermissionError
[Errno 1] Operation not permitted` writing `state.json`, and the UI then waits
on a file that will never appear. `preview_server.py` refuses to start on an
unwritable root and prints the fix — read that output instead of re-rendering.
**If the user says the permissions are already enabled, the answer is to restart
the Mac**: the permission cache goes stale and shows the toggle on while still
denying. Confirmed in production; nothing in Settings fixed it.

**Keep state.json fresh** — bump `phase` and `message` at each milestone (cut rendered, cut approved, Phase 2 rendered…). The UI polls and hot-reloads by itself; waveform + filmstrip regenerate automatically when cut.mp4 changes.

The timeline shows one track per KIND: markers, captions, video, audio (the mix),
**A1 / A2** (the J-cut takes), **text** overlays (hook), **images** (inserts + any
data-driven CustomGraphics windows), soundtrack. Anything you leave in code instead
of data simply will not appear.

**A1 / A2** are the J-cut takes, folded inside the audio track behind a caret that
only appears when the EDL carries a `jcut_timeline`. The hatched orange head on a
block is the lead — how much voice arrives before that take's picture.

**What the user can do in the UI:** scrub, trim take edges, delete takes, drag
insert/hook chips — and **mark correction ranges**: park the needle, press `M`
(or the IN button), move to the end of the problem, press `M` again — the note box
opens centred over the timeline — then type what should change. Many ranges per pass. Zoom: the slider is anchored on the needle, trackpad pinch
on the pointer. Shortcuts live behind the **?** button at the bottom right.

### The Estilo tab (between Fase 1 and Fase 2)

The cut is approved and nothing about the LOOK of Fase 2 is decided. **Do not ask
the style questions in chat** — the gate screen exists so the user SEES what each
style does, and a chat list of names asks them to choose blind. Set
`"awaitingStyle": true` in `state.json`; the UI opens its own tab and
`watch_edits.py` notifies you when they save `<edit>/preview_style.json`.

The catalog of options and what each pick means is in the track reference, which
you read next anyway — **`references/shortform.md`**. Short-form only: the gate
has no longform vocabulary yet, so on a longform job skip `awaitingStyle` and ask
the layer questions in chat.

**When the user saves timeline edits**, the UI writes `<edit>/preview_edits.json`
(never touches edl.json) and `watch_edits.py` notifies you automatically. To apply:
- `notes[]` — free-text correction requests, each with `start`/`end` on the draft
  timeline plus `renderedStart`/`renderedEnd` on the current `cut.mp4`, and the
  `phase` tab the user was on. Use the RENDERED pair to find the moment in the
  existing render. These are instructions in the user's words — read them, then do
  the edit they describe (re-cut, re-grade, swap an insert, fix a caption…).
- `edl.changes` / `edl.removed` — validate each new edge against
  `speech_regions.py` (warn if an edge clips a word — the user's intent wins, but
  say so), update `edl.json`, re-render, `verify_cut.py`.
- `editData` — insert/hook/behind timings → edit-data.json → re-render Phase 2.

Then delete `preview_edits.json` and update `state.json`.

---

# PHASE 1 — Clean cut + color grade

Goal: best take of every beat, cut on silence, graded image, clean `cut.mp4` for approval. No text, no graphics.

1. **Inventory.** URL source? `ingest_url.py` first (`--section` when only a range of a longform video matters). `ffprobe` every source. `transcribe_batch.py` (or `transcribe.py`) → `pack_transcripts.py` → read `takes_packed.md`. Note dimensions/orientation and whether it looks flat/LOG. Material you can't picture from the transcript → `watch_video.py` for a one-Read visual survey.
2. **Pre-scan** `takes_packed.md` for verbal slips, mis-speaks, and dead-air-stretched words (Whisper stretches a word's end across silence — verify long "phrases" against `speech_regions.py`/waveform before trusting them). **Then run `voice_levels.py` on every source** — the transcript is level-blind, so an inaudible passage reads exactly like a normal one. Anything it flags is a decision to make BEFORE the EDL: boost it with `gain_db`, or cut the take entirely.
3. **Converse.** Describe what you see; ask questions shaped by the material (content type, target length/aspect, pacing, must-keep/must-cut). No fixed checklist.
4. **Detect the colour profile — do NOT ask.** Run `detect_color.py <source>`.
   The answer is in the file; asking put a measurable question on the user.
   - **`rec709` (normal)** → no grade. `"grade": ""`. A standard profile already
     carries its look; "improving" it loses the match with the user's other material.
   - **LOG / HLG / PQ** → apply the helper's `grade` field and say so in one line.
     Apple Log uses its approved preset; any other LOG gets an expansion **measured
     from that footage**, not a guessed vendor curve.
   - **`confidence: low`** → the ONLY case that still asks. It means the statistics
     are ambiguous — a bright, shadowless scene has the same lifted black floor as
     a LOG curve. Show what was measured, then ask.
   Still show the `--candidates` montage before committing a LOG grade: detection
   picks the curve, the user picks the look.
5. **Propose the cut strategy** (4–8 sentences: shape, takes, cut direction, grade direction, length estimate). **Wait for confirmation.**
6. **Execute.** Produce `edl.json` (schema below; editor sub-agent brief for multi-take). Set cut edges from `speech_regions.py`, not raw Whisper times. Render: `render.py edl.json -o cut.mp4 --no-subtitles` (+`--voice-master` if wanted; longform: `--keep-resolution`). **The J-cut runs by default** — see below; you do not ask for it and you do not configure it per project.
7. **Self-eval (numeric first).** `verify_cut.py edl.json cut.mp4` (longform: `--min-silence 1.2`). Clean → done. Flags → `timeline_view` ONLY the flagged junctions, fix, re-render. Cap 3 loops, then surface remaining flags to the user.
8. **Show `cut.mp4` and wait for approval.** The phase gate.
9. **Open the Estilo tab** — `"awaitingStyle": true` in `state.json`, and let the
   user pick the editing style, the caption style and the edit elements in the UI
   (see "The Estilo tab"). Do NOT ask this in chat. Only then read the track
   reference: **`references/shortform.md`** or **`references/longform.md`**.

## J-cut — the default Phase-1 cleanup

Takes are OVERLAPPED, not butted. The outgoing take's audio runs to its natural
end; the incoming take's audio starts `lead` frames earlier **on its own track**
and the two are summed; the incoming PICTURE starts where the outgoing audio ends,
skipping `lead` frames of its own head. The voice arrives before the face.

Why it is the default: a straight concat leaves a beat of silence at every
junction — the outgoing take keeps its trailing pad and the incoming one starts
with its own. Measured on a real 3-take edit: **130ms and 140ms**. Small on paper,
a clear pause in the room. The J-cut removes it and the takes interlock.

Defaults, in `render.py`: **lead 5 frames**, **tail trim up to 2 frames**.
Override per project with `"jcut": {"lead_frames": N, "tail_trim_frames": N}`;
turn it off with `"jcut": false` or `--no-jcut` (single-range EDLs skip it anyway).

Three things that are not obvious:

- **Tighten with the TAIL, not the lead.** A bigger lead also pushes the picture
  deeper into the incoming take's speech, which reads as entering mid-word. The
  tail trim tightens the seam and leaves the picture entry alone. Measured: 5f
  lead alone gave 62/46ms of interlock; adding a 2f tail trim doubled it to
  129/112ms with the picture still entering 140ms into the speech.
- **The tail trim is measured, never blind.** `render.py` reads the silence
  actually present at the end of each range and trims at most that (keeping 10ms).
  A fixed 2 frames would eventually decapitate a word on a take that ends tight.
- **Sync is by construction:** `video_in = audio_in + lead` and
  `video_offset = audio_offset + lead`. Break that pairing and the take drifts.

`render.py` writes a `jcut_timeline` block into the EDL — the real output
positions. Everything downstream (preview timeline, `segments.json`, Phase-2
overlays) must index off THAT, not off the sum of the ranges: the J-cut output is
shorter than `Σ(end−start)`, so summing places every take after the first too late.

## Color grade

Reason about the image, don't preset-blind. Mental model ASC CDL: per channel `out = (in*slope + offset)**power`, then saturation. Applied per-segment at extraction (Hard Rule 7).

- **Iterate on ONE frame via a candidates montage, and let the user choose:**
  `grade.py <src> --candidates "punch=eq=contrast=1.15:saturation=1.25;suave=…;original=" --frame <t> -o edit/verify/grades.png` — one image, all looks labeled, side by side. Only render the full cut once the grade is locked.
- **Build from spaceless filters** so the string survives the EDL: `eq=…`, `colorbalance=…`, `colorlevels=…`. No `curves` with spaces (breaks filtergraph parsing).
- **The grade always runs at 8-bit.** `render.py` prepends `format=yuv420p` to the
  grade segment of the vf chain, because ffmpeg's `colorlevels` is broken on 9–14
  bit RGB — on a 10-bit source it collapses the frame to a constant TV black
  (measured `YAVG=64/1023`, `YBITDEPTH=1` on an iPhone Apple Log ProRes) while
  behaving correctly at 8- and 16-bit. `curves`, `colorbalance`, `hue` and `eq` are
  bit-depth-safe. Keep that guard in front of any new grade caller.
- **Standard/Rec.709** → light corrective or none. A user `.cube` goes first as `lut3d=`.

### LOG / HDR sources

`detect_color.py` resolves the profile and returns the `grade` to apply, so the
normal path needs nothing here. When it returns LOG/HLG/PQ, reports
`confidence: low`, or you are adding a vendor preset, **read
`references/log-grade.md`** — it carries the identification table (Apple Log has
no tag that names it), the Apple Log preset's two load-bearing details, and why
the grade must run at 8-bit.

Still show the candidates montage and get a pick — a preset is a starting point,
not permission to skip the approval.
- **Skin is the guardrail.** The moment skin goes orange/magenta/clipped, back off. Check a mid-shot face at each step.
- **Relative tweaks** ("+1 exposure", "mais saturação") → nudge that one term, re-montage the same frame, show again.
- **Rec.709 is the only color space allowed to leave Phase 1.** `render.py` handles
  this (tonemaps HDR, converts wide-gamut SDR, tags every output bt709/tv) — but
  VERIFY on the rendered cut: `ffprobe -v error -select_streams v:0
  -show_entries stream=color_space,color_primaries,color_range cut.mp4` must read
  bt709 / bt709 / tv. Anything else means a second interpretation is still alive
  downstream: Chrome (Remotion's decoder in Phase 2) re-reads those tags and
  silently re-grades the image — typically ~1.2 gamma darker with a hue shift — so
  the Phase-2 render stops matching the cut the user approved. Phone/mirrorless
  sources routinely write bt2020 primaries with `color_transfer=unknown`; that is
  wide-gamut SDR, **not** HDR, and an HDR-only check will miss it.

## Voice EQ + mastering (optional Phase-1 audio polish)

Opt-in: `render.py … --voice-master` or `"voice_master": true` in the EDL. Runs after compositing, before loudnorm. Chain (`VOICE_MASTER_CHAIN` in render.py): highpass 80 → mud cut −2.5dB@200 → compressor (3:1, −20dB, makeup 3) → presence +2.5dB@3.2k → air +3dB@9k shelf → deesser → limiter 0.95.

Tune per voice: brighter → raise treble/3.2k; warmer → back those off, lift ~200Hz; more "radio" → lower threshold / raise ratio; more natural → ratio 2, threshold −24dB. **Verify:** `ffmpeg -i cut.mp4 -af astats -vn -f null -` → Flat factor 0, peak < 0dB; loudnorm summary ≈ −14 LUFS / TP ≤ −1. Then let the user hear it.

## Cut craft

- Silences ≥ 400ms are the cleanest cuts; 150–400ms usable with a check; < 150ms unsafe.
- Preserve peaks (laughs, punchlines, emphasis) — extend past a punchline to include the reaction.
- Every cut must work on audio AND video.

**Fine-comb the silences — Whisper times are NOT cut edges:**
- Onsets drift early (bakes dead air at a segment head); ends stretch across silence (a 4s "phrase" may be 1s of talk); restarts get collapsed into one stretched word (the doubled take is invisible in text but audible).
- Fix: edges from `speech_regions.py` — start → region onset −30ms, end → offset +50–80ms (the trail keeps the word's decay; cutting at the offset clips the last sibilant). Inside merged speech blocks, place the edge by eye on a fine `timeline_view`.
- If the user flags a gap/clip after render, re-run `speech_regions.py` around that timestamp — don't nudge blindly.
- **A stretched word can hide a false start, and the stretch also mis-attributes every word around it.** When "de" spans 6.16→8.64, the words the source transcript places on either side may belong to *different takes* — the speaker trailed off, paused, and restarted the whole sentence. The text shows one clean sentence; the audio holds two attempts.
- **Never conclude a range is missing content from the SOURCE transcript's word times.** Extract the exact range and transcribe it in isolation — no surrounding context for the LM to complete from. A model reading the full file completes from context; the same model on a 3-second extract cannot. If the answer changes a deliverable (a caption rewrite, dropping a take), also check the range against `speech_regions.py` and the waveform — agreement between the isolated transcript and the acoustics is what makes it trustworthy.
- **Rotation:** phone clips are often stored landscape with a ±90° display-matrix; render.py handles it — don't force dimensions.

**Level the takes — presence is not audibility:**
- People drop their voice on asides, parentheticals and sentence tails ("além de, *claro*, …"). It sounds natural in the room and disappears on a phone speaker. The transcript is perfect, so nothing in the text pipeline flags it.
- Find it with `voice_levels.py --edl edl.json`: it reports each range's average AND the worst low run inside it, and suggests a `gain_db`. Size the gain off the **worst run**.
- Fix it per-range with `gain_db`, never with a global compressor.
- Confirm with `verify_cut.py`'s range-balance line. Target a ~2 dB spread between ranges — that is levelled. Driving it to 0 dB flattens the delivery and lifts room tone for nothing.
- Room tone is the real ceiling on a boost, not clipping. Before committing a large gain, compare the boosted take's internal pause against a pause elsewhere in the cut; if the boosted one is now the louder pause, back off.

## Editor sub-agent brief (multi-take selection)

```
You are editing a <type> video. Pick the best take of each beat and assemble
chronologically by beat, not clip order.
INPUTS: takes_packed.md; narrative context (2 sentences); speaker note;
expected structure (archetype or invent); verbal slips to avoid; target runtime.
Archetypes: launch (HOOK→PROBLEM→SOLUTION→BENEFIT→EXAMPLE→CTA); tutorial
(INTRO→SETUP→STEPS→GOTCHAS→RECAP); interview (Q→A→FOLLOWUP…); essay
(COLD-OPEN→THESIS→POINTS→COUNTER→CONCLUSION→CTA); vlog; or invent.
RULES: edges on word boundaries; pad 30–200ms; prefer ≥400ms silences; keep
unavoidable slips only if no better take (note in "reason"); if over budget,
drop a beat or trim tails and report.
OUTPUT (JSON array, no prose):
[{"source":"C0103","start":2.42,"end":6.85,"beat":"HOOK","quote":"…","reason":"…"}]
```

For a single long source (longform), the main context can pick cuts directly from `takes_packed.md`; for sources > ~30 min, delegate to the sub-agent so the full transcript never enters the main context.

## EDL format (Phase 1)

```json
{
  "version": 1,
  "sources": {"C0103": "/abs/path/C0103.MP4"},
  "grade": "eq=contrast=1.06:saturation=1.05",
  "voice_master": true,
  "jcut": {"lead_frames": 5, "tail_trim_frames": 2},
  "ranges": [
    {"source": "C0103", "start": 2.42, "end": 6.85, "beat": "HOOK",
     "quote": "…", "reason": "…", "gain_db": 0,
     "chapter": "Only on longform section openers"}
  ],
  "total_duration_s": 87.4
}
```

`grade`: preset name, raw filter, or `"auto"` — normally whatever `detect_color.py`
returned. `chapter` fields feed `chapters.py` (longform).

`jcut`: optional. **Omit it and the J-cut runs with the defaults** (lead 5f, tail
trim up to 2f); `false` butt-joins instead. After a render, `render.py` adds a
`jcut_timeline` array — the real per-take video/audio offsets in the output. That
block, not `Σ(end−start)`, is the timeline Phase 2 and the preview must use.

`gain_db`: per-range level correction in dB, sized by `voice_levels.py`. Applied at
extraction, before the edge fades, with a limiter on any boost so a loud syllable
inside a quiet take cannot clip. This is the fix for an under-level take — not a
global compressor, which would pump the good takes to rescue the bad one.
Cap around +12 dB: past that the room tone rises with the voice and the take
starts sounding like a different microphone.

---

# PHASE 2 + 3 — read the track reference (after the gate)

The cut is approved and the user picked the style in the UI (`preview_style.json`)
→ load **one** file and build exactly what was picked:

- **Vertical / Reels / TikTok / Shorts → read `references/shortform.md`.** Karaoke captions, static hook headline, dynamic camera, inserts, behind-the-subject, SFX, soundtrack.
- **Horizontal / YouTube / tutorial / vlog → read `references/longform.md`.** Retention cut is there too (read it BEFORE Phase 1 on longform jobs), B-roll, lower-thirds, chapter cards, callouts, .srt + chapters, soundtrack.

Both tracks: scaffold with one `cp -R` of the template, describe the video in `public/edit-data.json`, verify with montage stills, render, loudnorm, deliver `edit/final.mp4`. Load the `remotion-best-practices` skill when writing any Remotion code (CustomGraphics).

## Memory — `project.md`

Append one section per session at `<edit>/project.md`:

```markdown
## Session N — YYYY-MM-DD
**Phase reached:** …  **Strategy:** …
**Decisions:** takes, cuts, grade (LOG?), layer choices + why
**Outstanding:** deferred items
```

On startup, read it if it exists and summarize the last session in one sentence before asking whether to continue.

## Anti-patterns
- Every mistake under **Cut craft** — Whisper times as edges, cutting at a word's
  offset, judging level by the transcript, sizing `gain_db` off a range average,
  chasing the low-run numbers to zero, fixing one quiet take with a global
  compressor. They are stated there as procedure; this is the reminder that they
  are also the way Phase 1 goes wrong.
- Committing a grade without the one-frame candidates montage + user pick.
- Shipping a `cut.mp4` that is not tagged bt709/tv — Phase 2 will re-interpret it and the approved grade drifts.
- Butt-joining the takes. The J-cut is the default; `--no-jcut` is a deliberate exception, not a shortcut.
- Tightening a J-cut seam by raising the lead. That buys tightness by shoving the picture deeper into the incoming take's speech. Trim the outgoing TAIL instead.
- A fixed tail trim. It must be bounded by the silence actually measured at that range's end, or it eventually cuts a word off.
- `adelay` in milliseconds when placing overlapped audio, or `-shortest` on the mux. `adelay`'s integer-ms rounding leaves the mix a fraction short of the video and `-shortest` then amputates whole FRAMES of picture — and whether it bites depends on which way the numbers round, so it passes by luck until it doesn't. Delay in samples (`=NS`), and pin the length with `-t`.
- Re-transcribing cached sources; re-rendering Phase 1 when only Phase 2 changed.
- Launching the preview without arming `watch_edits.py` in the same turn. This
  is the one failure mode where the user reasonably believes they handed you a
  decision and you never got it — the toast says saved, the file is written, and
  no one is reading it.
- Applying `preview_edits.json` blindly — validate new edges against `speech_regions.py` first (flag clipped words to the user).
- Asking "NORMAL ou LOG?", or assuming the profile without running `detect_color.py`. It reads the answer off the file; ask only on `confidence: low`.
