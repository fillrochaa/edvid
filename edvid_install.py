"""One-command installer for the edvid skill.

    uvx --from git+https://github.com/fillrochaa/edvid edvid-install

Or, once the package is on PyPI, the short form — no git involved at all:

    uvx edvid-install

This is the `npx create-video@latest` shape, with uvx in place of npx and PyPI
in place of npm. What it buys over a README full of shell snippets:

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
# own repo (skills/remotion) — so it cannot be cloned into place and has always
# been a second manual step. Installing it here is the difference between "one
# command" and "one command plus a thing you'll forget". ~400 KB.
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
    """Where to install. An explicit --target wins; otherwise every agent whose
    config directory already exists. Falling back to Claude Code is safe: it is
    the common case, and an unused skills directory costs nothing.
    """
    if explicit:
        return [("(--target)", Path(explicit).expanduser())]
    found = [(name, d) for name, d in AGENT_DIRS if d.parent.exists()]
    return found or [AGENT_DIRS[0]]


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
    # Keep .env: it holds the user's optional API keys and is not ours to drop.
    env = dest / ".env"
    saved = env.read_bytes() if env.exists() else None
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    if saved is not None:
        (dest / ".env").write_bytes(saved)
        log("    .env preservado")
    return dest


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
                "node": "brew install node"}[tool]
    if sys.platform == "win32":
        return {"uv": "winget install astral-sh.uv", "ffmpeg": "winget install Gyan.FFmpeg",
                "node": "winget install OpenJS.NodeJS.LTS"}[tool]
    return {"uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
            "ffmpeg": "sudo apt install ffmpeg  (ou o gerenciador da sua distro)",
            "node": "sudo apt install nodejs npm  (precisa ser 18+)"}[tool]


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
    with tempfile.TemporaryDirectory() as tmp:
        src = fetch_repo(REPO, args.ref, Path(tmp), "edvid")
        if src is None:
            sys.exit(1)
        for name, skills_dir in targets:
            log(f"  instalando para {name}: {skills_dir / SKILL_NAME}")
            dests.append(install_into(src, skills_dir, args.force))

        if not args.no_remotion:
            rsrc = fetch_repo(REMOTION_REPO, "main", Path(tmp), "remotion")
            sub = next((rsrc / s for s in REMOTION_SUBDIRS if (rsrc / s).is_dir()),
                       None) if rsrc else None
            if sub and sub.is_dir():
                for _, skills_dir in targets:
                    log(f"  instalando skill do Remotion (Fase 2): {skills_dir / REMOTION_NAME}")
                    install_into(sub, skills_dir, args.force, name=REMOTION_NAME)
            else:
                log("  ! skill do Remotion não instalada (a Fase 1 não depende dela)")

    have_uv = run_uv_sync(dests[0]) if dests else False

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

    log()
    if missing:
        log(f"Falta instalar: {', '.join(missing)}. Rode os comandos acima e"
            " depois `edvid-install` de novo (ou só `uv sync` na pasta da skill).")
    else:
        log("Tudo pronto. Nenhuma chave de API é necessária — a transcrição roda local.")
    log()
    log("Como usar: abra seu agente DENTRO da pasta com seus vídeos e diga")
    log('  "edita esses vídeos num Reels"')
    log()
    log("Atualizar depois: rode este mesmo comando novamente.")


if __name__ == "__main__":
    main()
