"""Transcribe a video with OpenAI Whisper or ElevenLabs Scribe.

Extracts mono 16kHz audio via ffmpeg, uploads it to a speech-to-text
endpoint with word-level timestamps, and writes the result — normalized to
the ElevenLabs Scribe schema the rest of this skill consumes — to
<edit_dir>/transcripts/<video_stem>.json.

Two backends, chosen automatically by source length (backend="auto"):
  - OpenAI Whisper (whisper-1) for SHORT sources (<= 5 min). It exposes the
    word timestamps Edvid needs to avoid cutting inside words.
  - ElevenLabs Scribe (scribe_v1) for LONG sources (> 5 min) — e.g. YouTube
    videos and course lessons — when an ELEVENLABS_API_KEY is present. It
    handles long audio in a single request and returns the Scribe schema
    natively. If no ElevenLabs key is configured, long sources fall back to
    OpenAI (with chunking) so nothing breaks.
Pass backend="openai" or backend="elevenlabs" to force one regardless of length.

Audio is uploaded as constant-bitrate mono 16kHz 64kbps MP3 (~0.5 MB/min),
so file size is predictable from duration. When the file exceeds the
provider's upload cap it is split by BYTES into evenly-sized chunks that are
guaranteed to fit (24 MB target — the failure mode
of the old time-based FLAC chunking, where a dense 600s slice could blow the
cap and 413 the whole job, is gone by construction). Word timestamps are
offset and stitched back into a single continuous transcript.

Notes vs. the ElevenLabs Scribe backend:
  - OpenAI Whisper does NOT diarize, so every word gets speaker_id
    "speaker_0". The --num-speakers flag is accepted but ignored.
  - OpenAI Whisper does NOT tag audio events.
  - 'spacing' entries are reconstructed from inter-word gaps so silence
    detection (pack_transcripts / timeline_view) keeps working.

Cached: if the output file already exists, the upload is skipped.

Usage:
    python helpers/transcribe.py <video_path>
    python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    python helpers/transcribe.py <video_path> --language en
    python helpers/transcribe.py <video_path> --model whisper-1
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
# Edvid requires word-level timestamps. OpenAI's gpt-4o-transcribe models only
# support JSON output, while whisper-1 supports verbose_json + word timestamps.
DEFAULT_MODEL = "whisper-1"

ELEVENLABS_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_MODEL = "scribe_v1"

# Sources longer than this (seconds) transcribe via ElevenLabs Scribe when a
# key is available — Scribe handles long uploads in one request.
# 5 min = the practical line between short clips and lectures/YouTube.
LONG_SOURCE_SECONDS = 300

# Use a conservative 24 MB target for OpenAI uploads. Chunk count is derived
# from the actual file size, so every chunk fits by construction.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

# ElevenLabs Scribe accepts long single uploads, so don't chunk unless the
# source is very long; keep everything in one request to preserve continuity.
ELEVENLABS_CHUNK_SECONDS = 3600


def load_api_key() -> str:
    """Return the OpenAI API key from .env (repo root or cwd) or environment."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "OPENAI_API_KEY":
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
    v = os.environ.get("OPENAI_API_KEY", "")
    if not v:
        sys.exit("OPENAI_API_KEY not found in .env or environment")
    return v


def load_elevenlabs_key() -> str:
    """Return the ElevenLabs API key from .env (repo root or cwd) or env, or ""
    if none is configured. Optional — only long sources use it, and they fall
    back to OpenAI when it's absent.
    """
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, val = line.split("=", 1)
                if k.strip() == "ELEVENLABS_API_KEY":
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    return os.environ.get("ELEVENLABS_API_KEY", "")


def extract_audio(video_path: Path, dest: Path) -> None:
    """Extract mono 16kHz 64kbps MP3 (~0.5 MB/min) for upload.

    Constant bitrate means size scales linearly with duration, which is what
    lets us plan upload chunks by bytes with a hard guarantee they fit under
    the provider's cap. Whisper is trained on 16kHz mono, so the lossy encode
    costs nothing in transcript quality.
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "64k",
        str(dest),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        check=True, capture_output=True, text=True,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _segment_audio(audio_path: Path, out_dir: Path, chunk_seconds: float) -> list[Path]:
    """Split audio into <= chunk_seconds MP3 pieces (stream copy, no re-encode).
    Returns them in order."""
    pattern = str(out_dir / "chunk_%04d.mp3")
    cmd = [
        "ffmpeg", "-y", "-i", str(audio_path),
        "-f", "segment", "-segment_time", f"{chunk_seconds:.3f}",
        "-c", "copy", pattern,
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chunks = sorted(out_dir.glob("chunk_*.mp3"))
    # Frame-boundary drift can leave a sub-frame sliver as the final chunk; a
    # near-empty upload risks a 400 that aborts the whole job. <0.1s of tail
    # audio is inaudible — drop it.
    if len(chunks) > 1 and _probe_duration(chunks[-1]) < 0.1:
        chunks = chunks[:-1]
    return chunks


def call_openai(
    audio_path: Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    language: str | None = None,
) -> dict:
    """Call OpenAI Whisper on one audio file. Returns verbose JSON with words."""
    if model != "whisper-1":
        raise ValueError(
            "Edvid requires OpenAI model 'whisper-1' because its cut pipeline "
            "depends on verbose_json word timestamps. gpt-4o-transcribe does "
            "not currently expose word timestamps."
        )
    data: list[tuple[str, str]] = [
        ("model", model),
        ("response_format", "verbose_json"),
        ("timestamp_granularities[]", "word"),
        ("timestamp_granularities[]", "segment"),
        ("temperature", "0"),
    ]
    if language:
        data.append(("language", language))

    # OpenAI can return transient 5xx/429s mid-job; on a long multi-chunk
    # transcription a single blip would otherwise abort everything. Retry those
    # with exponential backoff; fail fast on 4xx (bad key / bad request).
    last_err = ""
    for attempt in range(6):
        with open(audio_path, "rb") as f:
            resp = requests.post(
                OPENAI_URL,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": (audio_path.name, f, "audio/mpeg")},
                data=data,
                timeout=1800,
            )
        if resp.status_code == 200:
            return resp.json()
        last_err = f"OpenAI returned {resp.status_code}: {resp.text[:500]}"
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt == 5:
            break
        wait = min(2 ** attempt * 5, 60)  # 5,10,20,40,60,60s
        print(f"    {last_err.splitlines()[0]} — retry {attempt + 1}/5 in {wait}s", flush=True)
        time.sleep(wait)

    raise RuntimeError(last_err)


def call_elevenlabs(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
) -> dict:
    """Call ElevenLabs Scribe on one audio file. Returns the raw JSON dict,
    which already follows the Scribe schema (words with type/start/end/speaker).
    """
    data: list[tuple[str, str]] = [
        ("model_id", ELEVENLABS_MODEL),
        ("timestamps_granularity", "word"),
        ("diarize", "false"),
        ("tag_audio_events", "false"),
    ]
    if language:
        data.append(("language_code", language))

    # Same transient-failure posture as OpenAI: retry 429/5xx, fail fast on 4xx.
    last_err = ""
    for attempt in range(6):
        with open(audio_path, "rb") as f:
            resp = requests.post(
                ELEVENLABS_URL,
                headers={"xi-api-key": api_key},
                files={"file": (audio_path.name, f, "audio/mpeg")},
                data=data,
                timeout=1800,
            )
        if resp.status_code == 200:
            return resp.json()
        last_err = f"ElevenLabs returned {resp.status_code}: {resp.text[:500]}"
        retryable = resp.status_code == 429 or resp.status_code >= 500
        if not retryable or attempt == 5:
            break
        wait = min(2 ** attempt * 5, 60)  # 5,10,20,40,60,60s
        print(f"    {last_err.splitlines()[0]} — retry {attempt + 1}/5 in {wait}s", flush=True)
        time.sleep(wait)

    raise RuntimeError(last_err)


def _to_scribe_words(openai_words: list[dict], offset: float) -> list[dict]:
    """Convert OpenAI word timestamps to Scribe-schema entries, inserting 'spacing'
    entries for inter-word gaps so downstream silence detection works.
    """
    out: list[dict] = []
    prev_end: float | None = None
    for w in openai_words:
        start = w.get("start")
        end = w.get("end")
        if start is None or end is None:
            continue
        s = float(start) + offset
        e = float(end) + offset
        text = (w.get("word") or w.get("text") or "").strip()
        if not text:
            continue
        if prev_end is not None and s > prev_end + 1e-3:
            out.append({
                "text": " ",
                "start": prev_end,
                "end": s,
                "type": "spacing",
                "speaker_id": "speaker_0",
            })
        out.append({
            "text": text,
            "start": s,
            "end": e,
            "type": "word",
            "speaker_id": "speaker_0",
        })
        prev_end = e
    return out


def _el_to_scribe_words(el_words: list[dict], offset: float) -> list[dict]:
    """Offset ElevenLabs Scribe words onto the global timeline. Scribe already
    emits the schema this skill consumes (word + spacing entries with
    start/end/speaker_id), so we only shift times and drop audio_event/junk.
    """
    out: list[dict] = []
    for w in el_words:
        wtype = w.get("type", "word")
        if wtype not in ("word", "spacing"):
            continue  # skip audio_event and anything unexpected
        start = w.get("start")
        end = w.get("end")
        if start is None or end is None:
            continue
        out.append({
            "text": w.get("text", ""),
            "start": float(start) + offset,
            "end": float(end) + offset,
            "type": wtype,
            "speaker_id": w.get("speaker_id") or "speaker_0",
        })
    return out


def _transcribe_audio(
    audio_path: Path,
    api_key: str,
    model: str,
    language: str | None,
    verbose: bool,
    cache_dir: Path | None = None,
    chunk_seconds: float | None = None,
    backend: str = "openai",
) -> dict:
    """Transcribe one prepared audio file (chunking if large). Returns a
    payload dict in ElevenLabs Scribe shape.

    Chunking is planned by BYTES for OpenAI: n = ceil(size / MAX_UPLOAD_BYTES)
    even time slices, so every chunk lands under the 25 MB cap regardless of
    duration (the mp3 is constant-bitrate). chunk_seconds, when given, acts as
    an additional upper bound — drop to ~300 when the provider is shedding
    load on big payloads.

    Chunks are fetched in parallel (offsets are precomputed, so order doesn't
    matter) and each chunk's raw payload is cached in cache_dir — a failed run
    resumes from the chunks that already succeeded instead of redoing them.
    """
    duration = _probe_duration(audio_path)
    size = audio_path.stat().st_size

    # Effective chunk length: byte-derived guarantee for OpenAI, plus any
    # explicit time cap. ElevenLabs takes big uploads, so only the time cap
    # applies there.
    eff_chunk = duration
    if backend == "openai" and size > MAX_UPLOAD_BYTES and duration > 0:
        eff_chunk = duration / math.ceil(size / MAX_UPLOAD_BYTES)
    if chunk_seconds:
        eff_chunk = min(eff_chunk, chunk_seconds)

    with tempfile.TemporaryDirectory() as seg_tmp:
        if duration > eff_chunk:
            chunks = _segment_audio(audio_path, Path(seg_tmp), eff_chunk)
        else:
            chunks = [audio_path]

        # offsets up-front so chunk results are order-independent
        offsets = [0.0]
        for c in chunks[:-1]:
            offsets.append(offsets[-1] + _probe_duration(c))

        def fetch(i: int, chunk: Path) -> dict:
            cache = cache_dir / f"chunk_{i:04d}.json" if cache_dir else None
            if cache and cache.exists():
                if verbose:
                    print(f"    chunk {i + 1}/{len(chunks)} (cached)", flush=True)
                return json.loads(cache.read_text())
            if verbose and len(chunks) > 1:
                print(f"    chunk {i + 1}/{len(chunks)}", flush=True)
            if backend == "elevenlabs":
                payload = call_elevenlabs(chunk, api_key, language=language)
            else:
                payload = call_openai(chunk, api_key, model=model, language=language)
            if cache:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(payload))
            return payload

        if len(chunks) == 1:
            payloads = [fetch(0, chunks[0])]
        else:
            # 2 workers, not 4: byte-based chunks are big (up to ~50 min of
            # audio each), and providers may rate-limit aggressive
            # concurrency — two in flight keeps throughput without tripping 429s.
            with ThreadPoolExecutor(max_workers=min(2, len(chunks))) as ex:
                payloads = list(ex.map(fetch, range(len(chunks)), chunks))

    words: list[dict] = []
    text_parts: list[str] = []
    detected_lang = language or ""
    for i, payload in enumerate(payloads):
        if backend == "elevenlabs":
            words.extend(_el_to_scribe_words(payload.get("words", []), offsets[i]))
        else:
            words.extend(_to_scribe_words(payload.get("words", []), offsets[i]))
        if payload.get("text"):
            text_parts.append(payload["text"].strip())
        if not detected_lang:
            detected_lang = payload.get("language") or payload.get("language_code") or ""

    backend_tag = (
        f"elevenlabs/{ELEVENLABS_MODEL}" if backend == "elevenlabs" else f"openai/{model}"
    )
    return {
        "language_code": detected_lang,
        "language": detected_lang,
        "text": " ".join(text_parts).strip(),
        "words": words,
        "_transcription_backend": backend_tag,
    }


def transcribe_one(
    video: Path,
    edit_dir: Path,
    api_key: str,
    language: str | None = None,
    num_speakers: int | None = None,
    model: str = DEFAULT_MODEL,
    verbose: bool = True,
    chunk_seconds: float | None = None,
    elevenlabs_key: str | None = None,
    backend: str = "auto",
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    num_speakers is accepted for CLI compatibility but ignored (OpenAI Whisper
    does not diarize; ElevenLabs Scribe is called with diarize=false here).

    backend: "auto" (default) uses ElevenLabs Scribe for sources longer than
    LONG_SOURCE_SECONDS when an elevenlabs_key is available, else OpenAI. Pass
    "openai" or "elevenlabs" to force one. ElevenLabs with no key falls back to
    OpenAI so long sources never hard-fail.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    # Pick the backend up front from source length (backend="auto").
    duration = _probe_duration(video)
    resolved = backend
    if resolved == "auto":
        resolved = "elevenlabs" if (duration > LONG_SOURCE_SECONDS and elevenlabs_key) else "openai"
    elif resolved == "elevenlabs" and not elevenlabs_key:
        resolved = "openai"

    if resolved == "elevenlabs":
        active_key = elevenlabs_key or ""
        active_model = ELEVENLABS_MODEL
        # don't chunk normal-length lectures; Scribe takes one long upload
        active_chunk = chunk_seconds or ELEVENLABS_CHUNK_SECONDS
        backend_label = "ElevenLabs Scribe"
    else:
        active_key = api_key
        active_model = model
        active_chunk = chunk_seconds
        backend_label = "OpenAI Whisper"

    if verbose:
        mins = duration / 60.0
        print(f"  extracting audio from {video.name} ({mins:.1f} min → {backend_label})", flush=True)

    # chunk-level resume cache, keyed by source identity + backend + params —
    # survives a failed run (e.g. a provider outage mid-job) so a retry only
    # redoes what failed. Backend is in the key so switching providers re-fetches.
    st = video.stat()
    chunk_cache = (transcripts_dir / ".chunks"
                   / f"{video.stem}-{st.st_size}-{int(st.st_mtime)}-{resolved}-{active_model}-{language or 'auto'}-{active_chunk or 'auto'}")

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.mp3"
        extract_audio(video, audio)
        size_mb = audio.stat().st_size / (1024 * 1024)
        if verbose:
            print(f"  transcribing {video.stem}.mp3 ({size_mb:.1f} MB) via {backend_label}", flush=True)
        payload = _transcribe_audio(audio, active_key, active_model, language, verbose,
                                    cache_dir=chunk_cache, chunk_seconds=active_chunk,
                                    backend=resolved)

    out_path.write_text(json.dumps(payload, indent=2))
    # only THIS video's chunk dir — siblings may belong to parallel batch workers
    shutil.rmtree(chunk_cache, ignore_errors=True)
    dt = time.time() - t0

    if verbose:
        kb = out_path.stat().st_size / 1024
        print(f"  saved: {out_path.name} ({kb:.1f} KB) in {dt:.1f}s")
        print(f"    words: {sum(1 for w in payload['words'] if w.get('type') == 'word')}")

    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe a video with OpenAI Whisper")
    ap.add_argument("video", type=Path, help="Path to video file")
    ap.add_argument(
        "--edit-dir",
        type=Path,
        default=None,
        help="Edit output directory (default: <video_parent>/edit)",
    )
    ap.add_argument(
        "--language",
        type=str,
        default=None,
        help="Optional ISO language code (e.g., 'en'). Omit to auto-detect.",
    )
    ap.add_argument(
        "--num-speakers",
        type=int,
        default=None,
        help="Accepted for compatibility but ignored (OpenAI Whisper does not diarize).",
    )
    ap.add_argument(
        "--model",
        type=str,
        default=DEFAULT_MODEL,
        help=f"OpenAI transcription model (default: {DEFAULT_MODEL}).",
    )
    ap.add_argument(
        "--chunk-seconds",
        type=float,
        default=None,
        help="Optional upper bound on chunk length. By default chunks are sized "
             "by BYTES so each upload stays under the conservative 24MB target; set "
             "this (e.g. 300) only when the provider is shedding load on big "
             "payloads (5xx on large chunks).",
    )
    ap.add_argument(
        "--backend",
        type=str,
        default="auto",
        choices=["auto", "openai", "elevenlabs"],
        help=f"Transcription backend. 'auto' (default) uses ElevenLabs Scribe for "
             f"sources longer than {LONG_SOURCE_SECONDS}s when ELEVENLABS_API_KEY is set, "
             "else OpenAI. Force with 'openai' or 'elevenlabs'.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()
    api_key = load_api_key()
    elevenlabs_key = load_elevenlabs_key()

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        api_key=api_key,
        language=args.language,
        num_speakers=args.num_speakers,
        model=args.model,
        chunk_seconds=args.chunk_seconds,
        elevenlabs_key=elevenlabs_key,
        backend=args.backend,
    )


if __name__ == "__main__":
    main()
