"""Transcribe a video locally with WhisperX (forced alignment).

Extracts mono 16kHz audio via ffmpeg, transcribes it with Whisper, then aligns
the result against the waveform with a per-language wav2vec2 model, and writes
the transcript to <edit_dir>/transcripts/<video_stem>.json.

There is ONE backend and it needs no API key, no network and no upload cap.
WhisperX ships as a dependency of this skill, so `uv sync` is the whole install.
Models (a Whisper model plus the alignment model for the detected language)
download once on first run and are cached by huggingface afterwards.

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

Output schema — a flat list of entries, each {text, start, end, type,
speaker_id}, where type is "word" or "spacing":
  - No diarization: every entry gets speaker_id "speaker_0". The --num-speakers
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
import subprocess
import sys
import tempfile
import time
from pathlib import Path


WHISPERX_MODEL = "large-v3"


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
    """Transcribe locally with WhisperX. Returns {words, text, language, _aligned}
    where words are raw {word, start, end} — _to_scribe_words shapes them.

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


def _to_scribe_words(raw_words: list[dict]) -> list[dict]:
    """Shape {word,start,end} into the schema the rest of the skill consumes,
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
        s, e = float(start), float(end)
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


def transcribe_one(
    video: Path,
    edit_dir: Path,
    language: str | None = None,
    num_speakers: int | None = None,
    model: str = WHISPERX_MODEL,
    verbose: bool = True,
) -> Path:
    """Transcribe a single video. Returns path to transcript JSON.

    Cached: returns the existing path immediately if the transcript is already
    there. num_speakers is accepted for CLI compatibility but ignored.

    There is no chunking. WhisperX reads the file off disk in one pass, so the
    upload caps, byte-planned splits and resume caches that a cloud backend
    needed simply do not apply — and no seam means no stitching error.
    """
    transcripts_dir = edit_dir / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    out_path = transcripts_dir / f"{video.stem}.json"

    if out_path.exists():
        if verbose:
            print(f"cached: {out_path.name}")
        return out_path

    duration = _probe_duration(video)
    if verbose:
        print(f"  extracting audio from {video.name} "
              f"({duration / 60.0:.1f} min → WhisperX {model}, forced alignment)", flush=True)

    t0 = time.time()
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / f"{video.stem}.mp3"
        extract_audio(video, audio)
        if verbose:
            size_mb = audio.stat().st_size / (1024 * 1024)
            print(f"  transcribing {video.stem}.mp3 ({size_mb:.1f} MB)", flush=True)
        raw = call_whisperx(audio, language=language, model_name=model, verbose=verbose)

    tag = f"whisperx/{model}" + ("" if raw.get("_aligned", True) else "/UNALIGNED")
    payload = {
        "language_code": raw.get("language", ""),
        "language": raw.get("language", ""),
        "text": raw.get("text", ""),
        "words": _to_scribe_words(raw.get("words", [])),
        "_transcription_backend": tag,
    }
    out_path.write_text(json.dumps(payload, indent=2))
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
        help=f"Whisper model (default: {WHISPERX_MODEL}). "
             "large-v3-turbo is faster and slightly less accurate.",
    )
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.exists():
        sys.exit(f"video not found: {video}")

    transcribe_one(
        video=video,
        edit_dir=(args.edit_dir or (video.parent / "edit")).resolve(),
        language=args.language,
        num_speakers=args.num_speakers,
        model=args.model,
    )


if __name__ == "__main__":
    main()
