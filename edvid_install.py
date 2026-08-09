"""One-command installer for the edvid skill.

    uv run https://raw.githubusercontent.com/fillrochaa/edvid/main/edvid_install.py

`uv run <url>` and not `uvx --from <package>`: uvx would resolve and install the
whole edvid dependency tree — torch, pandas, numpy — merely to run a script that
imports nothing but the stdlib. Measured: 2+ minutes and a from-source build of
pandas, versus 0.4s for the script. Same reason this file stays stdlib-only.

This is the `npx create-video@latest` shape. What it buys over a README full of
shell snippets:

  - ONE command, byte-identical on macOS, Linux and Windows PowerShell. Every
    OS difference (home directory, path separators, no chmod, no symlinks) is
    handled here in Python instead of in two or three shell variants that drift
    apart.
  - No clone step and no symlink/junction, so nothing needs admin rights.
  - It finds the agent by itself — Claude Code, Codex, or anything else with a
    skills directory — instead of asking the user which one they run.
  - It verifies ffmpeg and Node afterwards and prints the exact install command
    for THEIR platform when something is missing.

Deliberately stdlib-only: it has to run before anything is installed.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


REPO = "fillrochaa/edvid"
SKILL_NAME = "edvid"

# Phase 2 needs Remotion domain knowledge, which lives in a SUBDIRECTORY of its
# own repo — so it cannot be cloned into place and has always been a second
# manual step. Installing it here is the difference between "one command" and
# "one command plus a thing you'll forget".
REMOTION_REPO = "remotion-dev/skills"
REMOTION_NAME = "remotion-best-practices"
# Upstream renamed and restructured this: `skills/remotion` became
# `skills/remotion-best-practices`, a router skill that bundles a dozen
# sub-skills as real directories. Try the current path first and keep the old
# one as a fallback so an older --ref still installs.
REMOTION_SUBDIRS = ("skills/remotion-best-practices", "skills/remotion")

# Every agent that reads Agent-Skills-style directories. Add a line to support
# another one; the rest of the installer needs no change.
AGENT_DIRS: list[tuple[str, Path]] = [
    ("Claude Code", Path.home() / ".claude" / "skills"),
    ("Codex", Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "skills"),
]


def log(msg: str = "") -> None:
    print(msg, flush=True)


def detect_targets(explicit: str | None) -> list[tuple[str, Path]]:
    """Where to install. An explicit --target wins.

    Claude Code is ALWAYS a target, not a fallback. It is the documented home for
    this skill, and its config directory does not necessarily exist yet — a fresh
    install can have `~/.claude.json` and no `~/.claude/`. Gating on that
    directory made the installer skip Claude Code entirely on a machine that also
    had Codex: the "does any agent exist" test passed on Codex alone, so the
    fallback never fired and the user was told everything was fine.

    Other agents are added only when their config directory exists, so we don't
    litter `~/.codex/skills` on a machine that has never run Codex.
    """
    if explicit:
        return [("(--target)", Path(explicit).expanduser())]
    targets = [AGENT_DIRS[0]]
    targets += [(name, d) for name, d in AGENT_DIRS[1:] if d.parent.exists()]
    return targets


def fetch_repo(repo: str, ref: str, into: Path, label: str) -> Path | None:
    """Download and unpack `repo` at `ref`. Returns the unpacked directory.

    A tarball rather than `git clone` on purpose — it needs no git on the user's
    machine, which is one less thing to install on Windows.
    """
    log(f"  baixando {repo}@{ref}…")
    tgz = into / f"{label}.tar.gz"
    last = None
    for kind in ("heads", "tags"):
        try:
            url = f"https://codeload.github.com/{repo}/tar.gz/refs/{kind}/{ref}"
            with urllib.request.urlopen(url, timeout=120) as r, open(tgz, "wb") as f:
                shutil.copyfileobj(r, f)
            break
        except Exception as e:
            last = e
    else:
        log(f"  ! não consegui baixar {repo}@{ref}: {last}")
        return None

    out = into / label
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tgz) as tf:
        # filter="data" refuses absolute paths and traversal outside the target.
        try:
            tf.extractall(out, filter="data")
        except TypeError:      # Python < 3.12 has no filter=
            tf.extractall(out)
    roots = [p for p in out.iterdir() if p.is_dir()]
    if not roots:
        log(f"  ! o tarball de {repo} veio vazio")
        return None
    return roots[0]


def install_into(src: Path, skills_dir: Path, force: bool,
                 name: str = SKILL_NAME) -> Path:
    """Copy a skill into an agent's skills directory, replacing any previous
    copy. Refuses to touch a git checkout — that is somebody's development
    clone, and silently overwriting uncommitted work is unforgivable.
    """
    dest = skills_dir / name
    if (dest / ".git").exists() and not force:
        log(f"  ! {dest} é um clone git (instalação de desenvolvedor) — pulando.")
        log("    Use --force para substituir, ou atualize com: git -C <dir> pull --ff-only")
        return dest
    skills_dir.mkdir(parents=True, exist_ok=True)

    # Replace the old copy WITHOUT deleting `.venv` or `.env`.
    #
    # `.env` is the user's keys — not ours to drop. `.venv` is skipped for two
    # reasons. It is the likeliest thing to be locked: on Windows, rmtree dies
    # with WinError 32 if any file in the tree is open, and a running agent (or
    # an antivirus mid-scan) holds the venv's DLLs — measured, that is exactly
    # how a reinstall crashed. And deleting it threw away ~2 GB of torch on
    # every update, so "re-run the same command to update" meant re-downloading
    # the world; `uv sync` reconciles an existing venv in seconds instead.
    KEEP = {".venv", ".env"}
    if dest.exists():
        for entry in dest.iterdir():
            if entry.name in KEEP:
                continue
            try:
                if entry.is_dir() and not entry.is_symlink():
                    _rmtree(entry)
                else:
                    entry.unlink()
            except OSError as e:
                log(f"    ! não consegui remover {entry.name}: {e}")
    # dirs_exist_ok so the surviving .venv/.env keep their place.
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return dest


def _rmtree(path: Path) -> None:
    """rmtree that clears the read-only bit and retries once.

    Windows refuses to delete read-only files, and git checkouts and some
    unpackers leave them behind. A lock (WinError 32) is a different failure and
    still propagates — chmod cannot help there.

    `onexc` is 3.12+; `onerror` is the 3.10/3.11 spelling and takes the same
    three arguments in the same order, so one handler serves both.
    """
    def handler(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            raise
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=handler)
    else:
        shutil.rmtree(path, onerror=handler)


def run_uv_sync(dest: Path) -> bool:
    """Install the Python dependencies. Returns False if uv is missing."""
    if not shutil.which("uv"):
        return False
    log("  instalando dependências (torch + whisperx, alguns GB na primeira vez)…")
    r = subprocess.run(["uv", "sync", "--directory", str(dest)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("  ! uv sync falhou:")
        log("    " + (r.stderr or "").strip()[-600:])
        return True    # uv exists; the failure is a separate problem
    return True


def hint(tool: str) -> str:
    """The install command for this platform, not a generic one."""
    if sys.platform == "darwin":
        return {"uv": "brew install uv", "ffmpeg": "brew install ffmpeg",
                "node": "brew install node", "git": "brew install git"}[tool]
    if sys.platform == "win32":
        return {"uv": "winget install astral-sh.uv", "ffmpeg": "winget install Gyan.FFmpeg",
                "node": "winget install OpenJS.NodeJS.LTS",
                "git": "winget install Git.Git"}[tool]
    return {"uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "ffmpeg": "sudo apt install ffmpeg  (ou o gerenciador da sua distro)",
            "node": "sudo apt install nodejs npm  (precisa ser 18+)",
            "git": "sudo apt install git"}[tool]


def main() -> None:
    ap = argparse.ArgumentParser(
        prog="edvid-install",
        description="Instala a skill edvid no seu agente (Claude Code, Codex, …).")
    ap.add_argument("--ref", default="main", help="branch ou tag (padrão: main)")
    ap.add_argument("--target", default=None,
                    help="pasta de skills específica, em vez de detectar")
    ap.add_argument("--force", action="store_true",
                    help="substituir mesmo que o destino seja um clone git")
    ap.add_argument("--no-remotion", action="store_true",
                    help="não instalar a skill do Remotion (só a Fase 1)")
    args = ap.parse_args()

    log("edvid — instalação")
    log()

    targets = detect_targets(args.target)
    dests: list[Path] = []
    failed: list[tuple[str, Exception]] = []
    with tempfile.TemporaryDirectory() as tmp:
        src = fetch_repo(REPO, args.ref, Path(tmp), "edvid")
        if src is None:
            sys.exit(1)
        # One agent failing must not cost the others. A locked directory under
        # ~/.codex used to abort the run AFTER Claude Code was already written,
        # so the user got a traceback, no dependency install, no Remotion skill,
        # and no idea that half of it had worked.
        for name, skills_dir in targets:
            log(f"  instalando para {name}: {skills_dir / SKILL_NAME}")
            try:
                dests.append(install_into(src, skills_dir, args.force))
            except Exception as e:
                failed.append((name, e))
                log(f"  ! falhou para {name}: {e}")

        if not args.no_remotion:
            rsrc = fetch_repo(REMOTION_REPO, "main", Path(tmp), "remotion")
            sub = next((rsrc / s for s in REMOTION_SUBDIRS if (rsrc / s).is_dir()),
                       None) if rsrc else None
            if sub and sub.is_dir():
                for _, skills_dir in targets:
                    log(f"  instalando skill do Remotion (Fase 2): {skills_dir / REMOTION_NAME}")
                    try:
                        install_into(sub, skills_dir, args.force, name=REMOTION_NAME)
                    except Exception as e:
                        log(f"  ! Remotion falhou em {skills_dir}: {e}")
            else:
                log("  ! skill do Remotion não instalada (a Fase 1 não depende dela)")

    # Every destination needs its own .venv — helpers run as `uv run python …`
    # from whichever copy the agent loaded. Syncing only the first one left the
    # second agent with a skill that imports nothing. uv hardlinks from its
    # global cache, so the extra copies are cheap.
    have_uv = all(run_uv_sync(d) for d in dests) if dests else False

    log()
    log("verificação:")
    missing = []
    if not have_uv:
        missing.append("uv")
        log(f"  x uv         — necessário: {hint('uv')}")
    else:
        log("  ok uv")
    for tool, why in (("ffmpeg", "corte e render (Fase 1)"),
                      ("node", "Remotion (Fase 2)")):
        if shutil.which(tool):
            log(f"  ok {tool}")
        else:
            missing.append(tool)
            log(f"  x {tool:10} — {why}. Instale com: {hint(tool)}")
    # git is not an edvid dependency — the install above used no git at all. But
    # Claude Code leans on it, so its absence is worth a word rather than
    # silence. Deliberately NOT added to `missing`: nothing here is blocked by it.
    if not shutil.which("git"):
        log(f"  · git        — a edvid não usa, mas o Claude Code sim. {hint('git')}")

    # Say where it landed, explicitly. The failure this replaces was silent: the
    # installer reported success while having skipped the agent the user actually
    # runs, and they only found out when the skill wasn't there.
    log()
    log("instalado em:")
    for d in dests:
        log(f"  {d}")
        log(f"  {d.parent / REMOTION_NAME}")

    if failed:
        log()
        log("NÃO instalado em:")
        for name, e in failed:
            log(f"  {name}: {e}")
        log("  Se for 'já está sendo usado por outro processo', feche esse agente")
        log("  e rode este comando de novo — o que já instalou permanece.")

    log()
    if missing:
        log(f"Falta instalar: {', '.join(missing)}. Rode os comandos acima e"
            " depois este comando de novo.")
    else:
        log("Tudo pronto. Nenhuma chave de API é necessária — a transcrição roda local.")
    log()
    log("Reinicie o agente para ele enxergar a skill.")
    log()
    log("Como usar: abra seu agente DENTRO da pasta com seus vídeos e diga")
    log('  "edita esses vídeos num Reels"')
    log()
    log("Atualizar depois: rode este mesmo comando novamente.")


if __name__ == "__main__":
    main()
