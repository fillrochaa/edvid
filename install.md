---
name: edvid-install
description: Verify or repair an edvid install (Claude Code, Codex, or any agent with a skills directory). The user installs with one command from README.md; this file is for checking that it worked and fixing what didn't.
---

# edvid install

Use this file only for first-time setup, verification or repair. For daily
editing, read `SKILL.md`. Always read `helpers/` — that's where the scripts live.

> **The user installs, you verify.** `README.md` gives them one command:
>
> ```
> uv run https://raw.githubusercontent.com/fillrochaa/edvid/main/edvid_install.py
> ```
>
> That is the supported install and it is the user's action, not yours. If
> someone hands you only a repo URL and asks you to install from it, point them
> at that command rather than cloning and running unknown code yourself.
>
> Your job starts after: verifying, diagnosing a missing binary, fixing a stale
> venv. Those are local operations on a machine whose owner is in the
> conversation.

## What the install consists of

1. The skill directory itself — `~/.claude/skills/edvid` (or the Codex
   equivalent). `edvid_install.py` puts it there; it needs no git on the user's
   machine because it unpacks a tarball.
2. Python deps via `uv sync`, which includes **WhisperX** — transcription is a
   normal dependency, not an extra.
3. `ffmpeg` + `ffprobe` on PATH — Phase 1 cannot run without them.
4. **Node.js 18+** and the `remotion-best-practices` skill — Phase 2 only. The
   installer fetches that skill too.
5. *(Optional, all lazy — ask only when the feature is first used, then write to
   `.env`)*: `PEXELS_API_KEY` (Phase-2 images), `TREBLO_API_KEY` (Phase-3 AI
   music), `GOOGLE_API_KEY`+`GOOGLE_CSE_ID` (brand/person images — Wikimedia
   Commons is the no-key fallback and covers most cases).

**There is no transcription key.** WhisperX runs locally. If you find yourself
about to ask the user for a Groq or OpenAI key, something is wrong with your
reading of this skill.

## Two layouts

- **User layout** (what the installer produces, and what you should assume): the
  skill directory IS `~/.claude/skills/edvid`. Nothing to register, no symlink,
  identical on every OS.
- **Contributor layout**: repo cloned wherever the user keeps projects, plus a
  link into the skills directory. Only for someone developing the skill. The
  installer detects a `.git` there and refuses to overwrite it.

    ```bash
    # macOS / Linux
    ln -sfn ~/Developer/edvid ~/.claude/skills/edvid
    ```

    ```powershell
    # Windows — a junction needs no admin rights, unlike a symlink
    New-Item -ItemType Junction -Path "$env:USERPROFILE\.claude\skills\edvid" `
      -Target "$env:USERPROFILE\Developer\edvid"
    ```

## Repair steps

Detect the platform before emitting commands. Do not hand a Windows user `ln`,
`brew`, `chmod`, `grep`, `sed`, or `curl -s -w` — in PowerShell `curl` is an
alias for `Invoke-WebRequest` and takes different flags. Use `Select-String`,
`Select-Object -First`, `Set-Content`, `Invoke-RestMethod`.

Everything below calls the skill directory `<EDVID>`.

### Python deps

```bash
uv sync --directory <EDVID>
```

`uv` reads `pyproject.toml`, provisions a Python 3.10–3.13 interpreter on its own
when the system one doesn't fit, and builds a `.venv` inside the skill. That venv
is why helpers run as `uv run python helpers/…` — a bare `python` won't see them.

**The Python cap is load-bearing:** `requires-python = ">=3.10,<3.14"` exists
because torch publishes no cp314 wheels. Without the cap, `uv` picks 3.14 and
every torch-backed feature (transcription, matting) fails to install.

If `uv` is missing: `brew install uv` (macOS), `winget install astral-sh.uv`
(Windows), or the installer at https://docs.astral.sh/uv/. On Windows a
`winget install` only reaches `$PATH` in a **new** PowerShell window.

### ffmpeg (+ optional yt-dlp)

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v yt-dlp >/dev/null || brew install yt-dlp     # optional, URL sources only

# Debian / Ubuntu
# sudo apt-get update && sudo apt-get install -y ffmpeg && pip install yt-dlp
# Arch
# sudo pacman -S ffmpeg yt-dlp
```

```powershell
# Windows
winget install Gyan.FFmpeg
winget install yt-dlp.yt-dlp     # optional
```

If a package manager needs sudo/admin, tell the user the exact command and wait.
Do not invent a password.

### Node.js + the Remotion skill (Phase 2)

The installer handles this. To do it by hand: the skill lives in a
**subdirectory** of its repo (`skills/remotion-best-practices` — upstream
renamed it from `skills/remotion` and turned it into a router bundling a dozen
sub-skills), so it cannot be cloned into place. The repo is small, which makes
copying the cheapest answer — and re-running the copy IS the update.

```bash
node --version    # must be 18+
```

```powershell
node --version    # if missing: winget install OpenJS.NodeJS.LTS, then reopen PowerShell
```

## Verify end-to-end

Run one real thing. Prefer the lightest check that still proves the pipeline is
wired up.

```bash
# macOS / Linux
cd <EDVID>
uv run python helpers/timeline_view.py --help >/dev/null && echo "helpers OK"
uv run python -c "import cv2; print('opencv', cv2.__version__)"          # Phase-2 face tracking
uv run python -c "import whisperx; print('whisperx OK')"                 # transcription
ffprobe -hide_banner -filters | grep -qE '\bdeesser\b' && echo "ffmpeg has voice-master filters"
ffprobe -version | head -1
node --version && echo "node OK (Phase 2)"
```

```powershell
# Windows — no grep/head; use Select-String and Select-Object
cd <EDVID>
uv run python helpers/timeline_view.py --help > $null; if ($?) { "helpers OK" }
uv run python -c "import cv2; print('opencv', cv2.__version__)"
uv run python -c "import whisperx; print('whisperx OK')"
if (ffprobe -hide_banner -filters | Select-String -Pattern '\bdeesser\b' -Quiet) { "ffmpeg has voice-master filters" }
ffprobe -version | Select-Object -First 1
node --version; if ($?) { "node OK (Phase 2)" }
```

A full transcription is a fine smoke test now that it costs nothing but CPU —
but the first run downloads the models (several GB), so only do it if the user
is ready to wait. Better to let their first real clip be the test.

## Hand off

Tell the user, in one short message:

- Where the skill is installed.
- That they should `cd` into their footage folder and start their agent there.
- That a good first message is *"edit these into a launch video"* or
  *"inventory these takes and propose a strategy."*
- That all outputs land in `<videos_dir>/edit/` — the skill directory stays clean.
- That **no API key is needed**, and the first transcription downloads models once.

## Keeping the skill current

- Re-run the install command. It replaces the skill and preserves `.env`.
- On the contributor layout: `git -C <EDVID> pull --ff-only`, then
  `uv sync --directory <EDVID>` if `pyproject.toml` changed.

## Cold-start reminders

- Helpers run as `uv run python helpers/<name>.py`. A bare `python` misses the
  `.venv` — the most common post-install failure.
- Never ask for a transcription API key. There isn't one.
- On Windows, prefer a **junction** over a symlink (no admin needed) — and
  prefer the user layout over both.
- After any `winget install`, `$PATH` only refreshes in a **new** PowerShell
  window. A "command not found" right after a successful install is almost
  always this.
- `ffmpeg` from static builds is fine. Any modern (≥4.x) build is enough.
- `yt-dlp` is optional — install lazily the first time a URL source shows up.
- Node 18+ and `remotion-best-practices` are Phase-2 only. Phase 1 (cut + grade)
  works without them, so a user who only wants a clean cut can start immediately.
- Remotion projects are scaffolded per-video by copying the skill's own template
  from `assets/` and running `npm install` there. `create-video` is not used: the
  template carries the compositions the skill knows how to fill, with the
  Remotion version pinned so an upstream release can't break a render.
- If the user is on a Linux without a package manager you recognize, print the
  manual `ffmpeg` install URL and wait rather than guessing.
