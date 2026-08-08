"""Transcribe a video locally with WhisperX (forced alignment).

Extracts mono 16kHz audio via ffmpeg, transcribes it with Whisper, then aligns
the result against the waveform with a per-language wav2vec2 model, and writes
the transcript — normalized to the schema the rest of this skill consumes — to
<edit_dir>/transcripts/<video_stem>.json.

WhisperX is the DEFAULT and needs no API key, no network and no upload cap. It
ships as a dependency of this skill, so `uv sync` is the whole install. Models
(a Whisper model plus the alignment model for the detected language) download
once on first run and are cached by huggingface afterwards.

Why forced alignment and not a plain Whisper decoder: a decoder infers word
times from its own attention and drifts. Alignment measures them against the
audio. Measured on a 16s Portuguese clip against speech_regions.py (this skill's
acoustic ground truth), 93% of WhisperX's words land inside a real speech region
and the end of speech is placed within 10ms — the decoder-only backends this
replaced scored 66% and were 610ms late.

Speed is roughly realtime and gets BETTER with length, because loading the
model is a fixed ~18s cost: a 16s clip took 23s, a 166s source took 109s
(0.66x its own duration).

Portuguese uses jonatasgrosman/wav2vec2-large-xlsr-53-portuguese; 30-odd other
languages are covered and need no HF token. If no alignment model exists for the
detected language the run still succeeds, but the transcript is tagged
"/UNALIGNED" and its word times are the decoder's own — coarse, and not safe for
Phase-2 karaoke captions.

An optional cloud backend, ElevenLabs Scribe, remains available via
--backend elevenlabs for anyone who has a key and wants a second opinion on a
disputed passage. It is never selected automatically.

Notes:
  - No diarization: every word gets speaker_id "speaker_0". The --num-speakers
    flag is accepted but ignored.
  - 'spacing' entries are reconstructed from inter-word gaps so silence
    detection (pack_transcripts / timeline_view) keeps working.
  - Words outside the alignment dictionary ("2014.", "R$13,60") come back
    without times; they inherit a neighbouring boundary rather than being
    dropped, so no word ever disappears from the transcript.

Cached: if the output file already exists, the work is skipped.

Usage:
    uv run python helpers/transcribe.py <video_path>
    uv run python helpers/transcribe.py <video_path> --edit-dir /custom/edit
    uv run python helpers/transcribe.py <video_path> --language pt
    uv run python helpers/transcribe.py <video_path> --model large-v3-turbo
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests


# WhisperX — the default backend. Local, no key, forced alignment.
WHISPERX_MODEL = "large-v3"

# Optional cloud second opinion. Never chosen automatically.
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_MODEL = "scribe_v1"
# Scribe accepts long single uploads, so only split absurdly long sources;
# keeping one request preserves continuity across the whole transcript.
ELEVENLABS_CHUNK_SECONDS = 3600


def _env_value(name: str) -> str:
    """Read one setting from .env (repo root or cwd) or the environment."""
    for candidate in [Path(__file__).resolve().parent.parent / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, val = line.split("=", 1)
                if k.strip() == name:
                    val = val.strip().strip('"').strip("'")
                    if val:
                        return val
    return os.environ.get(name, "")


def load_elevenlabs_key() -> str:
    """ElevenLabs key from .env or environment, or "" — fully optional."""
    return _env_value("ELEVENLABS_API_KEY")


def _whisperx_device() -> tuple[str, str]:
    """Pick device and compute type for WhisperX.

    CTranslate2 (the faster-whisper backend) has no Metal path, so Apple
    Silicon runs on CPU regardless of what torch reports for MPS — asking for
    'mps' here fails at load time rather than falling back.
    """
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def call_whisperx(
    audio_path: Path,
    language: str | None = None,
    model_name: str = WHISPERX_MODEL,
    verbose: bool = False,
) -> dict:
    """Transcribe locally with WhisperX. Returns {words, text, language}.

    Two passes: Whisper for the text, then wav2vec2 forced alignment against
    the waveform for word boundaries. The alignment pass is the whole point.

    The import is deferred so that `--help`, and any caller that only needs the
    module's other helpers, does not pay torch's import cost.
    """
    try:
        import whisperx
    except ImportError as e:
        raise RuntimeError(
            "whisperx is missing from this environment. It is a normal dependency "
            "of the skill, so this usually means the venv is stale:\n"
            "    uv sync --directory <edvid>"
        ) from e

    device, compute_type = _whisperx_device()
    if verbose:
        print(f"    whisperx on {device}/{compute_type}, model {model_name}", flush=True)

    audio = whisperx.load_audio(str(audio_path))
    model = whisperx.load_model(model_name, device, compute_type=compute_type,
                                language=language)
    result = model.transcribe(audio, batch_size=8, language=language)
    detected = result.get("language") or language or ""

    aligned_ok = True
    try:
        align_model, metadata = whisperx.load_align_model(language_code=detected, device=device)
        result = whisperx.align(result["segments"], align_model, metadata, audio, device,
                                return_char_alignments=False)
    except Exception as e:
        # No wav2vec2 model for this language (or it failed to load). Whisper's
        # own segment times still exist, but they are exactly the coarse kind
        # this backend was chosen to avoid — say so instead of silently
        # returning worse timestamps than the caller thinks they asked for.
        aligned_ok = False
        if verbose:
            print(f"    WARNING: forced alignment unavailable for '{detected}' ({e}); "
                  "word times fall back to Whisper's own and will be coarse", flush=True)

    words: list[dict] = []
    text_parts: list[str] = []
    for seg in result.get("segments", []):
        seg_words = seg.get("words") or []
        if not seg_words and seg.get("text"):
            # unaligned fallback: keep the segment as one span
            if seg.get("start") is not None and seg.get("end") is not None:
                words.append({"word": seg["text"].strip(),
                              "start": float(seg["start"]), "end": float(seg["end"])})
            text_parts.append(seg["text"].strip())
            continue
        for w in seg_words:
            text = (w.get("word") or "").strip()
            if not text:
                continue
            text_parts.append(text)
            # Tokens outside the alignment dictionary ("2014.", "R$13,60") come
            # back without times. Dropping them would silently delete words from
            # the transcript, so borrow the neighbouring boundary instead.
            words.append({"word": text, "start": w.get("start"), "end": w.get("end")})

    # fill gaps left by unalignable tokens, in both directions
    for i, w in enumerate(words):
        if w["start"] is None:
            w["start"] = next((words[j]["end"] for j in range(i - 1, -1, -1)
                               if words[j]["end"] is not None), 0.0)
        if w["end"] is None:
            w["end"] = next((words[j]["start"] for j in range(i + 1, len(words))
                             if words[j]["start"] is not None), w["start"])
        w["start"], w["end"] = float(w["start"]), float(max(w["end"], w["start"]))

    return {"words": words, "text": " ".join(text_parts).strip(),
            "language": detected, "_aligned": aligned_ok}


def extract_audio(video_path: Path, dest: Path) -> None:
    """Extract mono 16kHz 64kbps MP3. Whisper is trained on 16kHz mono, so the
    lossy encode costs nothing in transcript quality and keeps the file small.
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
    Returns them in order. Only the cloud backend needs this — WhisperX reads
    the whole file off disk.
    """
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


def call_elevenlabs(
    audio_path: Path,
    api_key: str,
    language: str | None = None,
) -> dict:
    """Call ElevenLabs Scribe on one audio file. Returns the raw JSON dict,
    which already follows the schema this skill consumes (words with
    type/start/end/speaker).
    """
    data: list[tuple[str, str]] = [
        ("model_id", ELEVENLABS_MODEL),
        ("timestamps_granularity", "word"),
        ("diarize", "false"),
        ("tag_audio_events", "false"),
    ]
    if language:
        data.append(("language_code", language))

    # Retry 429/5xx with backoff, fail fast on 4xx (bad key / bad request).
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


def _to_scribe_words(raw_words: list[dict], offset: float) -> list[dict]:
    """Convert a {word,start,end} list to the schema this skill consumes,
    inserting 'spacing' entries for inter-word gaps so downstream silence
    detection works.
    """
    out: list[dict] = []
    prev_end: float | None = None
    for w in raw_words:
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
    emits the schema this skill consumes, so we only shift times and drop
    audio_event/junk.
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
    backend: str = "whisperx",
) -> dict:
    """Transcribe one prepared audio file. Returns a payload dict in the schema
    the rest of the skill consumes.

    WhisperX reads the file off disk in one pass — no upload, no cap, nothing to
    split. Only the cloud backend chunks, and only for absurdly long sources;
    its chunk payloads are cached in cache_dir so a failed run resumes.
    """
    duration = _probe_duration(audio_path)

    eff_chunk = duration
    if backend == "elevenlabs" and chunk_seconds:
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
                payload = call_whisperx(chunk, language=language, model_name=model,
                                        verbose=verbose)
            if cache:
                cache_dir.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(payload))
            return payload

        if len(chunks) == 1:
            payloads = [fetch(0, chunks[0])]
        else:
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

    if backend == "elevenlabs":
        backend_tag = f"elevenlabs/{ELEVENLABS_MODEL}"
    else:
        aligned = all(p.get("_aligned", True) for p in payloads)
        backend_tag = f"whisperx/{model}" + ("" if aligned else "/UNALIGNED")

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
    api_key: str = "",
    language: str | None = None,
    num_speakers: int | None = None,
    model: str = WHISPERX_MODEL,
    verbose: bool = True,
    chunk_seconds: float | None = None,
    elevenlabs_key: str | None = None,
    backend: str = "auto",
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns existing path immediately if the transcript already exists.
    num_speakers is accepted for CLI compatibility but ignored (no diarization).

    backend: "auto" (default) is WhisperX — local, keyless, forced alignment.
    Pass "elevenlabs" to force the optional cloud backend; it falls back to
    WhisperX when no key is configured, so nothing ever hard-fails for a
    missing key.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    resolved = "whisperx" if backend == "auto" else backend
    if resolved == "elevenlabs" and not elevenlabs_key:
        resolved = "whisperx"

    duration = _probe_duration(video)

    if resolved == "elevenlabs":
        active_key = elevenlabs_key or ""
        active_model = ELEVENLABS_MODEL
        active_chunk = chunk_seconds or ELEVENLABS_CHUNK_SECONDS
        backend_label = "ElevenLabs Scribe"
    else:
        active_key = ""
        active_model = model
        active_chunk = None
        backend_label = f"WhisperX ({model}, forced alignment)"

    if verbose:
        mins = duration / 60.0
        print(f"  extracting audio from {video.name} ({mins:.1f} min → {backend_label})", flush=True)

    # chunk-level resume cache, keyed by source identity + backend + params —
    # survives a failed run so a retry only redoes what failed.
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
    ap = argparse.ArgumentParser(description="Transcribe a video locally with WhisperX")
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
        help="Optional ISO language code (e.g., 'pt'). Omit to auto-detect.",
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
        help=f"Whisper model for WhisperX (default: {WHISPERX_MODEL}). "
             "large-v3-turbo is faster and slightly less accurate.",
    )
    ap.add_argument(
        "--chunk-seconds",
        type=float,
        default=None,
        help="Only affects the optional ElevenLabs backend; WhisperX never chunks.",
    )
    ap.add_argument(
        "--backend",
        type=str,
        default="auto",
        choices=["auto", "whisperx", "elevenlabs"],
        help="Transcription backend. 'auto' (default) is WhisperX: local, no key, "
             "forced alignment. 'elevenlabs' is an optional cloud second opinion "
             "and needs ELEVENLABS_API_KEY; without one it falls back to WhisperX.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    edit_dir = (args.edit_dir or (video.parent / "edit")).resolve()

    transcribe_one(
        video=video,
        edit_dir=edit_dir,
        language=args.language,
        num_speakers=args.num_speakers,
        model=args.model,
        chunk_seconds=args.chunk_seconds,
        elevenlabs_key=load_elevenlabs_key(),
        backend=args.backend,
    )


if __name__ == "__main__":
    main()
