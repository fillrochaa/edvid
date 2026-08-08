"""Map the LOUDNESS of the speech, not just its presence.

`speech_regions.py` answers "is someone talking here?" — a binary gate at a
fixed dB threshold. It happily reports a whispered aside and a projected
sentence as the same thing. That is how an under-level take survives the cut:
the words are all there, the transcript is perfect, verify_cut sees no pop and
no dead air — and the viewer still hits a passage they cannot hear.

This helper answers the other question: "how loud is the speech here, RELATIVE
to how loud this person normally is?" It builds a frame-level RMS timeline of
the whole source, learns the noise floor and the speaker's own median speech
level from the material itself, and then reports every phrase (and every
sub-phrase run of words) that sits meaningfully below that median.

The reference is always the speaker's own voice in the same recording, never an
absolute dBFS number — mic distance, gain staging and room differ per shoot, so
an absolute threshold flags everything or nothing.

Why RMS on 21ms frames and not LUFS: LUFS integrates over 400ms, which smears a
0.6s whispered clause into its loud neighbours. Frame RMS keeps whisper-length
events resolvable, and for a single voice in one room the K-weighting that LUFS
adds buys nothing — we only ever compare this voice against itself.

Loudness of a phrase = the 75th percentile of its speech frames (frames above
the noise floor). Not the mean: a phrase is mostly consonant gaps and decay
tails, and the mean tracks how gappy the phrase is more than how loud it is.
Not the peak: one plosive sets it. P75 tracks the sustained vowel body, which is
what perceived level follows.

Usage:
    python helpers/voice_levels.py <video> [--edit-dir <dir>] [--drop-db 5.0]
    python helpers/voice_levels.py <video> --edit-dir edit --json
    python helpers/voice_levels.py <video> --edl edl.json   # audit a cut's ranges
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

FRAME = 1024          # samples per astats window @48k ≈ 21.3ms
SR = 48000

# A drop of this many dB below the speaker's own median reads as "noticeably
# quieter" on phone speakers, which is where this footage gets watched. 3dB is
# normal sentence-to-sentence variation and would flag everything; 10dB is
# already inaudible-in-a-noisy-room and flags too late. 5dB is the useful line.
DEFAULT_DROP_DB = 5.0

# Runs shorter than this are consonant gaps and word-internal dips, not a
# passage the viewer experiences as quiet.
MIN_RUN_S = 0.25


# -------- Frame-level RMS timeline ------------------------------------------


def rms_timeline(video: Path) -> tuple[np.ndarray, np.ndarray]:
    """Return (times, rms_dbfs) at ~21ms resolution for the whole file.

    One ffmpeg pass: astats with `reset` emits per-window stats, ametadata
    prints them. Silent windows come back as -inf / very large negatives; they
    are clamped to -120 so downstream percentile math stays finite.
    """
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-af", f"astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
        "-vn", "-f", "null", "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.exit(f"ffmpeg failed on {video.name}:\n{proc.stderr[-2000:]}")

    times: list[float] = []
    levels: list[float] = []
    pending_t: float | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("frame:"):
            m = re.search(r"pts_time:([0-9.]+)", line)
            pending_t = float(m.group(1)) if m else None
        elif "RMS_level=" in line and pending_t is not None:
            raw = line.split("=", 1)[1].strip()
            try:
                v = float(raw)
            except ValueError:
                v = -120.0
            if not math.isfinite(v):
                v = -120.0
            times.append(pending_t)
            levels.append(max(v, -120.0))
            pending_t = None

    if not times:
        sys.exit(f"no audio levels read from {video.name} — does it have an audio track?")
    return np.asarray(times), np.asarray(levels)


def estimate_floor(levels: np.ndarray) -> float:
    """Gate separating room tone from speech, learned from this recording.

    A percentile of all frames does not work: it encodes how TALKY the file is,
    not where the noise ends. A 90%-speech file puts P10 inside the speech, a
    file with long silences puts it deep in the noise — and a gate that lands in
    the noise is the failure that matters here, because then room tone gets
    measured as "very quiet speech" and every silence reports as a whisper.

    Ridler-Calvard intermeans instead: split at t, take the mean of each side,
    move t to the midpoint, repeat. On a bimodal histogram (room tone / voice)
    it converges into the valley between the two modes regardless of their
    relative sizes.

    Clamped afterwards to 12–32 dB under the speech median: a file with almost
    no silence has no second mode to find, and an unclamped gate would wander
    into the voice and start calling ordinary quiet syllables whispers.
    """
    t = float(levels.mean())
    for _ in range(60):
        lo, hi = levels[levels < t], levels[levels >= t]
        if lo.size == 0 or hi.size == 0:
            break
        t_new = (float(lo.mean()) + float(hi.mean())) / 2.0
        if abs(t_new - t) < 0.01:
            t = t_new
            break
        t = t_new

    speech = levels[levels > t]
    if speech.size:
        med = float(np.percentile(speech, 50))
        t = min(max(t, med - 32.0), med - 12.0)
    return max(t, -70.0)


def phrase_level(levels: np.ndarray, floor_db: float) -> float | None:
    """P75 of the frames above the floor. None if the span holds no speech."""
    speech = levels[levels > floor_db]
    if speech.size == 0:
        return None
    return float(np.percentile(speech, 75))


# -------- Transcript phrases -------------------------------------------------


def load_words(video: Path, edit_dir: Path | None) -> list[dict]:
    """Word list from the cached transcript, or [] when there is none."""
    if edit_dir is None:
        return []
    tpath = edit_dir / "transcripts" / f"{video.stem}.json"
    if not tpath.exists():
        return []
    data = json.loads(tpath.read_text())
    words = data.get("words") or []
    out = []
    for w in words:
        # Transcripts carry 'spacing' tokens that span the gaps BETWEEN words.
        # Keeping them makes every phrase look contiguous and the whole file
        # collapses into one phrase — drop anything that is not a real word.
        if w.get("type", "word") != "word":
            continue
        try:
            out.append({
                "word": (w.get("word") or w.get("text") or "").strip(),
                "start": float(w["start"]),
                "end": float(w["end"]),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def group_phrases(words: list[dict], gap_s: float = 0.5) -> list[dict]:
    """Same phrase grouping as pack_transcripts: break on gaps ≥ 0.5s."""
    phrases: list[dict] = []
    cur: list[dict] = []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] >= gap_s:
            phrases.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                            "text": " ".join(x["word"] for x in cur), "words": cur})
            cur = []
        cur.append(w)
    if cur:
        phrases.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": " ".join(x["word"] for x in cur), "words": cur})
    return phrases


# -------- Low-level run detection -------------------------------------------


def low_runs(times, levels, floor_db, median_db, drop_db, min_run_s=MIN_RUN_S):
    """Contiguous stretches of SPEECH sitting ≥ drop_db under the median.

    A FINDING tool, not a convergence test. Runs are selected for being below
    the threshold, so a run's own level is always below it by construction —
    re-running this on a corrected render will still surface the decay tails and
    soft consonants of the passage you just fixed, and chasing that number to
    zero would just keep raising room tone. Judge whether a fix landed with
    `verify_cut.py`'s range-balance check, which compares each rendered range
    against the others instead of against a threshold it was selected by.

    Operates on frames, so it finds quiet passages the phrase grouping cannot
    see — e.g. a speaker who fades out over the last third of a sentence, or a
    whispered clause inside an otherwise normal phrase. Frames below the noise
    floor are silence, not quiet speech, and never open or extend a run; a run
    survives short silent bridges (a breath) without being split in two.
    """
    thresh = median_db - drop_db
    is_speech = levels > floor_db
    is_low = is_speech & (levels < thresh)

    runs = []
    i = 0
    n = len(levels)
    while i < n:
        if not is_low[i]:
            i += 1
            continue
        j = i
        last_low = i
        while j + 1 < n:
            j += 1
            if is_low[j]:
                last_low = j
            elif is_speech[j]:
                break            # speech came back up to level — run is over
            elif times[j] - times[last_low] > 0.30:
                break            # silence longer than a breath — run is over
        start_t, end_t = float(times[i]), float(times[last_low])
        span = levels[i:last_low + 1]
        lvl = phrase_level(span, floor_db)
        if end_t - start_t >= min_run_s and lvl is not None:
            runs.append({"start": round(start_t, 2), "end": round(end_t, 2),
                         "level_db": round(lvl, 1),
                         "delta_db": round(lvl - median_db, 1)})
        i = last_low + 1
    return runs


def words_in(words: list[dict], start: float, end: float) -> str:
    hit = [w["word"] for w in words if w["end"] > start and w["start"] < end]
    return " ".join(hit)


def suggest_gain(delta_db: float, headroom_db: float = 1.5) -> float:
    """Gain that lifts a quiet passage back toward the speaker's median.

    Deliberately short of a full match: recovering the last dB or two costs more
    in raised room tone and breath than it buys in intelligibility, and the
    delivery was quiet on purpose. Capped at +12dB — past that the noise floor
    comes up with the voice and it sounds like a different microphone.
    """
    return round(min(max(-delta_db - headroom_db, 0.0), 12.0), 1)


# -------- Report -------------------------------------------------------------


def analyze(video: Path, edit_dir: Path | None, drop_db: float, edl_path: Path | None):
    times, levels = rms_timeline(video)
    floor = estimate_floor(levels)
    speech = levels[levels > floor]
    if speech.size == 0:
        sys.exit(f"{video.name}: no speech found above the noise floor ({floor:.1f} dBFS)")
    median = float(np.percentile(speech, 50))

    words = load_words(video, edit_dir)
    phrases = group_phrases(words) if words else []

    rows = []
    for p in phrases:
        mask = (times >= p["start"]) & (times <= p["end"])
        lvl = phrase_level(levels[mask], floor)
        if lvl is None:
            continue
        delta = lvl - median
        rows.append({
            "start": round(p["start"], 2), "end": round(p["end"], 2),
            "text": p["text"], "level_db": round(lvl, 1), "delta_db": round(delta, 1),
            "flag": "LOW" if delta <= -drop_db else "ok",
            "suggest_gain_db": suggest_gain(delta) if delta <= -drop_db else 0.0,
        })

    runs = low_runs(times, levels, floor, median, drop_db)
    for r in runs:
        r["text"] = words_in(words, r["start"], r["end"]) if words else ""
        r["suggest_gain_db"] = suggest_gain(r["delta_db"])

    edl_rows = []
    if edl_path and edl_path.exists():
        edl = json.loads(edl_path.read_text())
        for i, r in enumerate(edl.get("ranges", [])):
            r_start, r_end = float(r["start"]), float(r["end"])
            mask = (times >= r_start) & (times <= r_end)
            lvl = phrase_level(levels[mask], floor)
            if lvl is None:
                continue
            delta = lvl - median

            # The gain must serve the QUIETEST speech in the range, not the
            # range average. A range holding a whispered clause followed by a
            # normal one averages out to "fine" while the whisper stays
            # unhearable — so size the correction off the deepest low run that
            # falls inside the range, and fall back to the range level only
            # when no run was found.
            inner = [x for x in runs if x["end"] > r_start and x["start"] < r_end]
            worst = min((x["delta_db"] for x in inner), default=None)
            drive = worst if worst is not None else delta
            flagged = drive <= -drop_db

            edl_rows.append({
                "index": i, "start": r_start, "end": r_end,
                "beat": r.get("beat", ""), "quote": r.get("quote", ""),
                "level_db": round(lvl, 1), "delta_db": round(delta, 1),
                "worst_delta_db": round(drive, 1),
                "current_gain_db": float(r.get("gain_db", 0.0) or 0.0),
                "flag": "LOW" if flagged else "ok",
                "suggest_gain_db": suggest_gain(drive) if flagged else 0.0,
            })

    return {
        "video": video.name,
        "noise_floor_db": round(floor, 1),
        "median_speech_db": round(median, 1),
        "drop_db": drop_db,
        "phrases": rows,
        "low_runs": runs,
        "edl_ranges": edl_rows,
    }


def print_report(rep: dict) -> None:
    print(f"voice levels — {rep['video']}   (21ms RMS windows)")
    print(f"  noise floor: {rep['noise_floor_db']:.1f} dBFS   "
          f"median speech: {rep['median_speech_db']:.1f} dBFS   "
          f"flag threshold: {-rep['drop_db']:.1f} dB")

    if rep["phrases"]:
        print(f"\nphrases (level vs the speaker's own median):")
        for p in rep["phrases"]:
            mark = " ▼ LOW" if p["flag"] == "LOW" else ""
            gain = f"  → +{p['suggest_gain_db']:.1f}dB" if p["suggest_gain_db"] else ""
            txt = p["text"][:52]
            print(f"  {p['start']:6.2f}-{p['end']:6.2f}  {p['level_db']:6.1f}  "
                  f"{p['delta_db']:+5.1f}  {txt:<52}{mark}{gain}")

    if rep["edl_ranges"]:
        print(f"\nEDL ranges  (measured on the SOURCE — suggested gain_db is absolute, it replaces any current value):")
        for r in rep["edl_ranges"]:
            mark = " ▼ LOW" if r["flag"] == "LOW" else ""
            gain = f"  → gain_db: {r['suggest_gain_db']:+.1f}" if r["suggest_gain_db"] else ""
            cur = f" (now {r['current_gain_db']:+.1f})" if r["current_gain_db"] else ""
            print(f"  [{r['index']:02d}] {r['start']:6.2f}-{r['end']:6.2f}  "
                  f"avg {r['delta_db']:+5.1f}  worst {r['worst_delta_db']:+5.1f}{cur}  "
                  f"{r['beat']:<8}{mark}{gain}")

    runs = rep["low_runs"]
    print(f"\nlow-level speech runs ≥{MIN_RUN_S}s: {len(runs)}")
    for r in runs:
        txt = f'  "{r["text"]}"' if r.get("text") else ""
        print(f"  {r['start']:6.2f}-{r['end']:6.2f}  {r['level_db']:6.1f} dBFS  "
              f"{r['delta_db']:+5.1f} dB → +{r['suggest_gain_db']:.1f}dB{txt}")

    n_low = len(runs) + sum(1 for p in rep["phrases"] if p["flag"] == "LOW")
    print(f"\nVERDICT: {'clean — no under-level speech' if n_low == 0 else f'{n_low} under-level spot(s) — set gain_db on the affected EDL range(s)'}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Detect under-level / whispered speech relative to the speaker's own median.")
    ap.add_argument("video", type=Path)
    ap.add_argument("--edit-dir", type=Path, default=None,
                    help="edit dir holding transcripts/<stem>.json — adds phrase text to the report")
    ap.add_argument("--edl", type=Path, default=None,
                    help="audit an EDL's ranges and suggest a gain_db for each")
    ap.add_argument("--drop-db", type=float, default=DEFAULT_DROP_DB,
                    help=f"dB below the median that counts as under-level (default {DEFAULT_DROP_DB})")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not args.video.exists():
        sys.exit(f"not found: {args.video}")

    rep = analyze(args.video, args.edit_dir, args.drop_db, args.edl)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print_report(rep)


if __name__ == "__main__":
    main()
