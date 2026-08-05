---
name: edvid-install
description: Install edvid into the current agent (Claude Code, Codex, Hermes, Openclaw, etc.) and wire up ffmpeg + the OpenAI API key so the user can start editing immediately.
---

# edvid install

Use this file only for first-time install or reconnect. For daily editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

## What you're doing

You're setting up a conversation-driven video editor for the user. After install, the user drops raw footage into any folder, runs their agent (`claude`, `codex`, etc.) there, and says "edit these into a launch video." You do the rest by reading `SKILL.md`.

edvid runs in three phases: **Phase 1** = clean cut + color grade + optional voice mastering (ffmpeg + OpenAI), shown to the user for approval; **Phase 2** = captions, motion graphics, illustrative images, dynamic camera (**Remotion** + OpenCV); **Phase 3** = soundtrack (ffmpeg, plus the Treblo API only if the user wants AI-generated music). So the machine needs the ffmpeg + Python toolchain (Phases 1 & 3) and the Node/Remotion toolchain (Phase 2).

Must exist on this machine:

1. The `edvid` repo cloned somewhere stable.
2. `ffmpeg` on `$PATH` (plus optional `yt-dlp` for online sources). — Phase 1
3. An OpenAI API key in `.env` at the repo root (for Whisper transcription). — Phase 1
4. **Node.js 18+ and npm** on `$PATH` (for Remotion). — Phase 2
5. The **`remotion-best-practices` skill** installed and discoverable (clone https://github.com/remotion-dev/skills and symlink `skills/remotion` into the agent's skills dir). — Phase 2
6. *(Optional, all lazy — ask only when the feature is first used, then write to `.env`)*:
   - `ELEVENLABS_API_KEY` — Phase 1 transcription of **long sources** (>5 min: YouTube videos, course lessons). With `backend=auto`, sources over 5 min transcribe via ElevenLabs Scribe (`scribe_v1`) when this key is set. Short clips stay on OpenAI; no key means long sources fall back to OpenAI (with chunking). Ask for it the first time a >5 min source appears. https://elevenlabs.io/app/settings/api-keys
   - `PEXELS_API_KEY` — Phase 2 illustrative images (stock photos/videos). https://www.pexels.com/api/
   - `TREBLO_API_KEY` — Phase 3 AI-generated soundtrack, only if the user picks "create with AI" (a local music file needs no key). https://sonauto.ai (Treblo)
   - `GOOGLE_API_KEY` + `GOOGLE_CSE_ID` — Phase 2 images of **named brands/people/logos** that Pexels lacks. Optional and finicky to provision (the key and the Custom Search API must live in the same Google Cloud project). **Wikimedia Commons is the no-key fallback** (`wikimedia_images.py`) and covers most people/places, so Google is rarely required.

And one thing must be true about the current agent:

7. It can discover `SKILL.md` — either via a global skills directory (`~/.claude/skills/`, `~/.codex/skills/`) or via a `CLAUDE.md` / system-prompt import.

## Install prompt contract

- Do everything yourself. Only ask the user for things you cannot generate — the OpenAI API key, and confirmation before `brew install`.
- Prefer a stable clone path like `~/Developer/edvid` (not `/tmp`, not `~/Downloads`).
- The skill references helpers by bare name (`transcribe.py`, `render.py`). That works because SKILL.md and `helpers/` ship together — keep them as siblings when you register the skill.
- After install, verify by running one real command against one real file. Don't declare success on file-existence checks alone.

## Steps

### 1. Clone to a stable path

```bash
test -d ~/Developer/edvid || git clone <YOUR-EDVID-REPO-URL> ~/Developer/edvid
cd ~/Developer/edvid
```

If the repo is already there, `git pull --ff-only` and continue.

### 2. Install Python deps

```bash
# Prefer uv if available; fall back to pip.
command -v uv >/dev/null && uv sync || pip install -e .
```

`pyproject.toml` lists `requests`, `pillow`, `numpy`, and `opencv-python-headless==4.10.0.84` (the last one powers the Phase-2 dynamic-camera face/eye tracking in `face_track.py` — keep it pinned to the 4.10 line; 5.x dropped `CascadeClassifier` and breaks Haar detection). No console scripts — helpers are invoked directly as `python helpers/<name>.py`.

### 3. Install ffmpeg (+ optional yt-dlp)

`ffmpeg` and `ffprobe` are hard requirements for Phase 1. `yt-dlp` is only needed if the user wants to pull sources from URLs. Phase 2 uses Remotion (Node.js) — set up in step 6.

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v yt-dlp >/dev/null || brew install yt-dlp     # optional

# Debian / Ubuntu
# sudo apt-get update && sudo apt-get install -y ffmpeg
# pip install yt-dlp

# Arch
# sudo pacman -S ffmpeg yt-dlp
```

If `brew` / `apt` / `pacman` requires a sudo prompt, tell the user the exact command and wait. Do not invent a password.

### 4. Register the skill with the current agent

Figure out which agent you are running under, and register once. A symlink of the whole repo directory is the right shape — helpers/ needs to sit next to SKILL.md.

- **Claude Code** (`~/.claude/` present):

    ```bash
    mkdir -p ~/.claude/skills
    ln -sfn ~/Developer/edvid ~/.claude/skills/edvid
    ```

- **Codex** (`$CODEX_HOME` set, or `~/.codex/` present):

    ```bash
    mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
    ln -sfn ~/Developer/edvid "${CODEX_HOME:-$HOME/.codex}/skills/edvid"
    ```

- **Hermes / Openclaw / another agent with a skills directory**: symlink `~/Developer/edvid` into that agent's skills directory under the name `edvid`. If the agent has no skills directory, add a line to its system prompt / config pointing at `~/Developer/edvid/SKILL.md` (e.g. an `@~/Developer/edvid/SKILL.md` import in a `CLAUDE.md`-equivalent).

If you can't tell which agent you're in, ask the user once: "which agent am I running under — Claude Code, Codex, or something else?" Then pick the right target.

### 5. OpenAI API key

OpenAI Whisper (`whisper-1`) is the base transcription backend and handles short sources (≤5 min). Without an OpenAI API key, nothing transcribes. `whisper-1` is intentional: Edvid requires word-level timestamps, which `gpt-4o-transcribe` does not currently expose. OpenAI Whisper does not diarize speakers or tag audio events, so every word gets `speaker_id: speaker_0`. Long sources (>5 min) prefer the optional `ELEVENLABS_API_KEY` (Scribe) when present — see requirement 6 — but fall back to OpenAI when it isn't, so the OpenAI key is still required.

1. Check existing state in this order and stop at the first hit:

    ```bash
    # a) env var already exported
    [ -n "$OPENAI_API_KEY" ] && echo "env"
    # b) .env at repo root already has it
    grep -q '^OPENAI_API_KEY=..' ~/Developer/edvid/.env 2>/dev/null && echo "dotenv"
    ```

2. If neither is set, ask the user exactly once:

    > I need an OpenAI API key for transcription (word-level timestamps). Create one at https://platform.openai.com/api-keys and provide it through the secure credential flow — I'll store it in `~/Developer/edvid/.env`. Or if you already have it exported as `OPENAI_API_KEY`, say "use env" and I'll skip.

    When the user pastes a key, write it to `~/Developer/edvid/.env`:

    ```bash
    printf 'OPENAI_API_KEY=%s\n' "$KEY" > ~/Developer/edvid/.env
    chmod 600 ~/Developer/edvid/.env
    ```

    Never echo the key back in tool output. Never commit `.env`.

3. Sanity check with a cheap, quota-free call:

    ```bash
    curl -s -o /dev/null -w '%{http_code}\n' \
      -H "Authorization: Bearer $(sed -n 's/^OPENAI_API_KEY=//p' ~/Developer/edvid/.env)" \
      https://api.openai.com/v1/models
    ```

    `200` means the key works. `401` means the user pasted a wrong/expired key — ask once more and stop. Anything else (network, 5xx), move on and verify during first real transcription.

### 6. Node.js + the Remotion skill (Phase 2)

Phase 2 (captions, motion graphics, images) is built in Remotion, which needs Node.js 18+ and the `remotion-best-practices` skill.

```bash
# Node.js 18+ (install via nvm/brew if missing)
node --version

# Install the Remotion skill and symlink it next to edvid
test -d ~/Developer/remotion-skills || \
  git clone --depth 1 https://github.com/remotion-dev/skills ~/Developer/remotion-skills
mkdir -p ~/.claude/skills
ln -sfn ~/Developer/remotion-skills/skills/remotion ~/.claude/skills/remotion
```

None of the optional keys (`ELEVENLABS_API_KEY`, `PEXELS_API_KEY`, `TREBLO_API_KEY`, `GOOGLE_API_KEY`/`GOOGLE_CSE_ID` — see requirement 6) are needed at install time. Ask for each **lazily**, the first time its feature is used, and append it to `.env` next to `OPENAI_API_KEY`. `ELEVENLABS_API_KEY` is the Phase-1 exception to "Phase 2/3": ask for it the first time a **>5 min source** shows up (long lessons / YouTube), since that's when the auto backend wants Scribe. Image search also works with **zero keys** via Wikimedia Commons, so Phase 2 images are never hard-blocked.

### 7. Verify end-to-end

Run one real thing. Prefer the lightest verification that still proves the pipeline is wired up. Use `uv run` (or activate the venv) so the helper sees its deps — after `uv sync` a bare `python` won't find `opencv`/`numpy`:

```bash
cd ~/Developer/edvid
uv run python helpers/timeline_view.py --help >/dev/null && echo "helpers OK"      # or: python … after pip install -e .
uv run python -c "import cv2; print('opencv', cv2.__version__)"                    # Phase-2 face tracking
ffprobe -hide_banner -filters | grep -qE '\bdeesser\b' && echo "ffmpeg has voice-master filters"   # Phase-1 --voice-master
ffprobe -version | head -1
node --version && echo "node OK (Phase 2)"
```

Full transcription test is optional at install time — it uses OpenAI API credits. Better to wait until the user hands you their first clip.

### 8. Hand off

Tell the user, in one short message:

- Where the skill is installed (`~/Developer/edvid`).
- That they should `cd` into their footage folder and start their agent there (e.g. `claude`).
- That a good first message is: *"edit these into a launch video"* or *"inventory these takes and propose a strategy."*
- That all outputs land in `<videos_dir>/edit/` — the repo stays clean.

## Keeping the skill current

- `cd ~/Developer/edvid && git pull --ff-only` pulls the latest code. The symlink auto-picks it up on the next run.
- If `pyproject.toml` changed deps, re-run `uv sync` / `pip install -e .` after pulling.

## Cold-start reminders

- Symlink the **whole directory**, not just `SKILL.md`. The helpers need to sit next to it.
- If `.env` exists but the key is empty, treat it the same as missing — don't assume existence means validity.
- `ffmpeg` from static builds works fine. Any modern (≥ 4.x) build is enough.
- `yt-dlp` is optional. Don't block install on it; install lazily the first time a user asks to pull from a URL.
- Node.js 18+ and the `remotion-best-practices` skill are required for Phase 2 (captions, motion graphics, images). Phase 1 (cut + grade) works without them, so a user who only wants a clean cut can start immediately — but set up step 6 so Phase 2 is ready when the cut is approved.
- Remotion projects are scaffolded per-video with `npx create-video@latest` inside `<videos_dir>/edit/remotion/` when Phase 2 starts — nothing to install globally.
- Never run transcription as part of install verification unless the user explicitly asks — OpenAI API usage draws on the user's quota.
- If the user is on Linux without a package manager Claude recognizes, print the manual `ffmpeg` install URL and wait rather than guessing.
