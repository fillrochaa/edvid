"""Batch-transcribe every video in a directory.

Walks <videos_dir> for common video extensions, transcribes each with WhisperX,
writes transcripts to <videos_dir>/edit/transcripts/<name>.json.

WhisperX is local, so it already saturates the machine on ONE file — this runs
serially by default. --workers only helps the optional ElevenLabs cloud backend,
which is I/O-bound.

Cached per-file: any source that already has a transcript is skipped.

Usage:
    python helpers/transcribe_batch.py <videos_dir>
    python helpers/transcribe_batch.py <videos_dir> --workers 4
    python helpers/transcribe_batch.py <videos_dir> --num-speakers 2
    python helpers/transcribe_batch.py <videos_dir> --edit-dir /custom/edit
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from transcribe import (
    WHISPERX_MODEL,
    load_elevenlabs_key,
    transcribe_one,
)


VIDEO_EXTS = {".mp4", ".MP4", ".mov", ".MOV", ".mkv", ".MKV", ".avi", ".AVI", ".m4v"}


def find_videos(videos_dir: Path) -> list[Path]:
    videos = sorted(
        p for p in videos_dir.iterdir()
        if p.is_file() and p.suffix in VIDEO_EXTS and not p.name.startswith(".")
    )
    return videos


def main() -> None:
    ap = argparse.ArgumentParser(description="Parallel batch transcription of a videos directory")
    ap.add_argument("videos_dir", type=Path, help="Directory containing source videos")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <videos_dir>/edit)",
    )
    ap.add_argument("--workers", type=int, default=1,
                    help="Parallel workers (default: 1). Only raise this for "
                         "--backend elevenlabs; local transcription contends with itself.")
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code. Omit to auto-detect per file.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Accepted for compatibility but ignored (no diarization).",
    )
    ap.add_argument(
        "--model",
        type=str,
        default=WHISPERX_MODEL,
        help=f"Whisper model for WhisperX (default: {WHISPERX_MODEL}).",
    )
    ap.add_argument(
        "--backend",
        type=str,
        default="auto",
        choices=["auto", "whisperx", "elevenlabs"],
        help="Transcription backend per file. 'auto' (default) is WhisperX: local, "
             "no key, forced alignment. 'elevenlabs' is an optional cloud second "
             "opinion and needs ELEVENLABS_API_KEY.",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"not a directory: {videos_dir}")

    edit_dir = (args.edit_dir or (videos_dir / "edit")).resolve()
    (edit_dir / "transcripts").mkdir(parents=True, exist_ok=True)

    videos = find_videos(videos_dir)
    if not videos:
        sys.exit(f"no videos found in {videos_dir}")

    already_cached = [v for v in videos if (edit_dir / "transcripts" / f"{v.stem}.json").exists()]
    pending = [v for v in videos if v not in already_cached]

    print(f"found {len(videos)} videos ({len(already_cached)} cached, {len(pending)} to transcribe)")
    if not pending:
        print("nothing to do")
        return

    elevenlabs_key = load_elevenlabs_key()

    # WhisperX already saturates every core on one file — running several at
    # once only makes them contend and finish later. The cloud backend is
    # I/O-bound, so there parallel workers are free throughput.
    if args.backend != "elevenlabs" and args.workers != 1:
        print("WhisperX runs one file at a time (local inference already uses every core)")
        args.workers = 1

    print(f"transcribing {len(pending)} files with {args.workers} parallel workers")
    t0 = time.time()

    errors: list[tuple[Path, str]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                transcribe_one,
                video=v,
                edit_dir=edit_dir,
                language=args.language,
                num_speakers=args.num_speakers,
                model=args.model,
                verbose=False,
                elevenlabs_key=elevenlabs_key,
                backend=args.backend,
            ): v
            for v in pending
        }
        for fut in as_completed(futures):
            v = futures[fut]
            try:
                out = fut.result()
                print(f"  + {v.stem}  →  {out.name}")
            except Exception as e:
                errors.append((v, str(e)))
                print(f"  x {v.stem}  FAILED: {e}")

    dt = time.time() - t0
    print(f"\ndone in {dt:.1f}s")
    if errors:
        print(f"{len(errors)} failures:")
        for v, msg in errors:
            print(f"  {v.name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()
