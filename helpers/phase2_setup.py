#!/usr/bin/env python3
"""Cross-platform Phase-2 setup helpers for Edvid's Remotion templates."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TRACKS = {"shortform", "longform"}
MAX_NEUTRAL_POINTS = 1_000_000


def require_file(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if not resolved.is_file():
        raise ValueError(f"{label} not found: {resolved}")
    return resolved


def executable(name: str) -> str:
    candidates = [name]
    if os.name == "nt":
        candidates = [f"{name}.cmd", f"{name}.exe", name]
    found = next((path for candidate in candidates if (path := shutil.which(candidate))), None)
    if found is None:
        raise ValueError(f"required executable not found on PATH: {name}")
    return found


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 300) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return result.stdout


def scaffold(edit_dir: Path, track: str, install: bool) -> Path:
    if track not in TRACKS:
        raise ValueError(f"unknown track: {track}")
    edit_dir = edit_dir.resolve()
    if not edit_dir.is_dir():
        raise ValueError(f"edit dir not found: {edit_dir}")

    template = ROOT / "assets" / track
    if not template.is_dir():
        raise ValueError(f"template not found: {template}")
    destination = edit_dir / "remotion"
    if destination.exists():
        raise ValueError(
            f"destination already exists: {destination} (refusing to overwrite)"
        )

    shutil.copytree(template, destination)
    cut = require_file(edit_dir / "cut.mp4", "Phase-1 cut")
    public = destination / "public"
    public.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cut, public / "cut.mp4")

    if install:
        run([executable("npm"), "install"], cwd=destination, timeout=900)
    return destination


def finite_number(value: object, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    return number


def neutral_track(edit_data_path: Path, output_path: Path) -> Path:
    edit_data_path = require_file(edit_data_path, "edit-data.json")
    data = json.loads(edit_data_path.read_text(encoding="utf-8"))

    duration = finite_number(data.get("durationSec"), "durationSec")
    fps = finite_number(data.get("fps"), "fps")
    width = finite_number(data.get("width"), "width")
    height = finite_number(data.get("height"), "height")
    camera = data.get("camera")
    if not isinstance(camera, dict):
        raise ValueError("camera must be an object")
    target_x = finite_number(camera.get("targetX"), "camera.targetX")
    target_y = finite_number(camera.get("targetY"), "camera.targetY")

    if duration <= 0 or fps <= 0 or width <= 0 or height <= 0:
        raise ValueError("durationSec, fps, width and height must be positive")
    count = round(duration * fps)
    if count <= 0 or count > MAX_NEUTRAL_POINTS:
        raise ValueError(f"neutral track would contain an unsafe point count: {count}")

    payload = {
        "fps": fps,
        "width": width,
        "height": height,
        "count": count,
        "points": [[target_x, target_y]] * count,
        "neutral": True,
    }
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output_path


def frame_count(video: Path) -> int:
    output = run(
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_frames",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "default=nw=1:nk=1",
            str(require_file(video, "video")),
        ],
        timeout=120,
    ).strip()
    try:
        count = int(output)
    except ValueError as exc:
        raise ValueError(f"ffprobe returned an invalid frame count for {video}: {output}") from exc
    if count <= 0:
        raise ValueError(f"ffprobe returned a non-positive frame count for {video}")
    return count


def video_fps(video: Path) -> float:
    output = run(
        [
            executable("ffprobe"),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate",
            "-of",
            "default=nw=1:nk=1",
            str(require_file(video, "video")),
        ],
        timeout=30,
    ).strip()
    try:
        fps = float(Fraction(output))
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"ffprobe returned an invalid frame rate: {output}") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError(f"ffprobe returned a non-positive frame rate: {output}")
    return fps


def segment_number(path: Path) -> int:
    match = re.search(r"(\d+)(?:_v)?$", path.stem)
    return int(match.group(1)) if match else sys.maxsize


def build_segments(edit_dir: Path, output_path: Path | None = None) -> Path:
    edit_dir = edit_dir.resolve()
    edl_path = require_file(edit_dir / "edl.json", "edl.json")
    cut_path = require_file(edit_dir / "cut.mp4", "cut.mp4")
    clips_dir = edit_dir / "clips_graded"
    if not clips_dir.is_dir():
        raise ValueError(f"clips directory not found: {clips_dir}")

    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    ranges = edl.get("ranges")
    if not isinstance(ranges, list):
        raise ValueError("edl.json ranges must be an array")

    segments = list(clips_dir.glob("seg_*_v.mp4"))
    if not segments:
        segments = list(clips_dir.glob("seg_*.mp4"))
    segments.sort(key=segment_number)
    if len(segments) != len(ranges):
        raise ValueError(
            f"{len(segments)} segments for {len(ranges)} ranges - clips_graded is dirty"
        )

    cumulative = [0]
    for segment in segments:
        cumulative.append(cumulative[-1] + frame_count(segment))
    real_frames = frame_count(cut_path)
    if cumulative[-1] != real_frames:
        raise ValueError(
            f"segments sum {cumulative[-1]}f != cut.mp4 {real_frames}f"
        )

    fps = video_fps(cut_path)
    payload = {
        "segments": [
            {
                "start": round(cumulative[index] / fps, 4),
                "dur": round((cumulative[index + 1] - cumulative[index]) / fps, 4),
            }
            for index in range(len(segments))
        ]
    }
    output_path = (output_path or edit_dir / "remotion" / "public" / "segments.json").resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def verify_scdet(video: Path, start_frame: int, end_frame: int) -> int:
    if start_frame < 0 or end_frame < start_frame:
        raise ValueError("invalid frame range")
    vf = f"select='between(n,{start_frame},{end_frame})',setpts=N/30/TB,scdet=threshold=0"
    result = subprocess.run(
        [
            executable("ffmpeg"),
            "-v",
            "info",
            "-i",
            str(require_file(video, "video")),
            "-vf",
            vf,
            "-an",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    lines = [line for line in result.stderr.splitlines() if "scd.score" in line]
    print("\n".join(lines))
    return 0 if lines else 1


def local_remotion(project_dir: Path) -> Path:
    project_dir = project_dir.resolve()
    name = "remotion.cmd" if os.name == "nt" else "remotion"
    binary = project_dir / "node_modules" / ".bin" / name
    if not binary.is_file():
        raise ValueError(f"local Remotion binary not found: {binary}; run npm install")
    return binary


def run_remotion(project_dir: Path, arguments: list[str]) -> int:
    if not arguments:
        raise ValueError("pass the Remotion command and arguments after --")
    project_dir = project_dir.resolve()
    completed = subprocess.run(
        [str(local_remotion(project_dir)), *arguments],
        cwd=project_dir,
        check=False,
        timeout=3600,
    )
    return completed.returncode


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)

    scaffold_parser = commands.add_parser("scaffold")
    scaffold_parser.add_argument("--edit-dir", type=Path, required=True)
    scaffold_parser.add_argument("--track", choices=sorted(TRACKS), required=True)
    scaffold_parser.add_argument("--skip-install", action="store_true")

    neutral_parser = commands.add_parser("neutral-track")
    neutral_parser.add_argument("--edit-data", type=Path, required=True)
    neutral_parser.add_argument("-o", "--output", type=Path, required=True)

    segments_parser = commands.add_parser("segments")
    segments_parser.add_argument("--edit-dir", type=Path, required=True)
    segments_parser.add_argument("-o", "--output", type=Path)

    scdet_parser = commands.add_parser("verify-scdet")
    scdet_parser.add_argument("--video", type=Path, required=True)
    scdet_parser.add_argument("--start-frame", type=int, required=True)
    scdet_parser.add_argument("--end-frame", type=int, required=True)

    remotion_parser = commands.add_parser("remotion")
    remotion_parser.add_argument("--project-dir", type=Path, required=True)
    remotion_parser.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "scaffold":
            output = scaffold(args.edit_dir, args.track, not args.skip_install)
            print(f"Remotion project ready: {output}")
        elif args.command == "neutral-track":
            print(f"Neutral track written: {neutral_track(args.edit_data, args.output)}")
        elif args.command == "segments":
            print(f"Segments written: {build_segments(args.edit_dir, args.output)}")
        elif args.command == "verify-scdet":
            return verify_scdet(args.video, args.start_frame, args.end_frame)
        elif args.command == "remotion":
            arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            return run_remotion(args.project_dir, arguments)
    except (ValueError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
