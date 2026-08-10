"""Bring a URL source (YouTube & friends) into the videos dir via yt-dlp.

Edit-from-a-link: fetches an MP4 up to --max-height (default 1080 — editing
quality), ASCII-safe filename, and optional --section to download ONLY a time
range of a longform source (keyframe-accurate, no full download) — the cheap
way to clip a 1h video when the edit only needs minutes 12-25. The file lands
in --dest untouched like any other source; transcribe.py etc. proceed as
normal from there.

Usage:
    python helpers/ingest_url.py <url> --dest <videos_dir>
    python helpers/ingest_url.py <url> --dest . --section 12:00-25:30
    python helpers/ingest_url.py <url> --dest . --section 44:10-        # to the end
    python helpers/ingest_url.py <url> --simulate                       # metadata only

yt-dlp ships as a dependency of the skill. Long downloads are silent
(quiet mode so stdout stays parseable) — run in the background for big files.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def parse_time(value: str) -> float:
    parts = value.strip().split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    sys.exit(f"cannot parse time: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def parse_section(value: str) -> str:
    """'12:00-25:30' / '44:10-' → yt-dlp download-sections syntax '*720-1530'."""
    if "-" not in value:
        sys.exit(f"--section must be START-END (END optional): {value!r}")
    a, _, b = value.partition("-")
    if not a.strip():
        sys.exit("--section needs a start time")
    start = parse_time(a)
    end = "inf" if not b.strip() else f"{parse_time(b):.2f}"
    return f"*{start:.2f}-{end}"


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    return {
        "duration_s": round(float(data.get("format", {}).get("duration") or 0.0), 2),
        "width": v.get("width"),
        "height": v.get("height"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Download a URL source for editing (yt-dlp wrapper)")
    ap.add_argument("url")
    ap.add_argument("--dest", type=Path, default=Path("."),
                    help="videos dir the source lands in (default: cwd)")
    ap.add_argument("--max-height", type=int, default=1080,
                    help="resolution ceiling (default 1080; use 2160 for 4K sources)")
    ap.add_argument("--section", type=str, default=None,
                    help="START-END time range to download instead of the whole video "
                         "(e.g. 12:00-25:30, or 44:10- to the end); keyframe-accurate")
    ap.add_argument("--simulate", action="store_true",
                    help="print title/duration/resolution without downloading")
    args = ap.parse_args()

    if shutil.which("yt-dlp") is None:
        sys.exit("yt-dlp not on PATH — the venv is stale: uv sync --directory <edvid>")

    h = args.max_height
    fmt = (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/bv*[height<={h}]+ba"
           f"/b[height<={h}]/bv+ba/b")

    if args.simulate:
        cmd = ["yt-dlp", "-f", fmt, "--no-playlist", "--skip-download",
               "--print", "%(title)s | %(uploader)s | %(duration_string)s | "
                          "%(width)sx%(height)s | %(webpage_url)s",
               "--", args.url]
        raise SystemExit(subprocess.run(cmd).returncode)

    dest = args.dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", fmt,
        "--merge-output-format", "mp4",
        "--no-playlist",
        "--restrict-filenames",       # ascii-safe names survive every ffmpeg filtergraph
        "--write-info-json",
        "-q", "--no-simulate",
        "--print", "after_move:filepath",
        "-o", str(dest / "%(title).60B.%(ext)s"),
    ]
    if args.section:
        # --force-keyframes-at-cuts re-encodes the boundary GOPs so the range
        # starts exactly where asked instead of snapping to the previous keyframe.
        cmd += ["--download-sections", parse_section(args.section),
                "--force-keyframes-at-cuts"]
    cmd += ["--", args.url]

    r = subprocess.run(cmd, stdout=subprocess.PIPE, text=True)
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    if r.returncode != 0 or not lines:
        sys.exit(f"yt-dlp failed (exit {r.returncode}) — see stderr above")
    video = Path(lines[-1])
    if not video.exists():
        sys.exit(f"yt-dlp reported {video} but the file is missing")

    info: dict = {}
    info_path = video.with_name(video.stem + ".info.json")
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text())
            info = {"title": raw.get("title"), "uploader": raw.get("uploader") or raw.get("channel"),
                    "source_url": raw.get("webpage_url"), "upload_date": raw.get("upload_date")}
        except Exception:
            pass

    summary = {"path": str(video), **probe(video), **info,
               "section": args.section}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\nnext: python helpers/transcribe.py '{video}' --edit-dir '{dest / 'edit'}'",
          file=sys.stderr)


if __name__ == "__main__":
    main()
