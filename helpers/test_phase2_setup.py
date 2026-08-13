#!/usr/bin/env python3
"""Unit tests for the cross-platform Phase-2 helper."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import phase2_setup  # noqa: E402


class Phase2SetupTests(unittest.TestCase):
    def test_scaffold_copies_template_and_cut_without_overwrite(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edvid phase2 ") as tmp:
            edit = Path(tmp) / "edição"
            edit.mkdir()
            (edit / "cut.mp4").write_bytes(b"cut")
            output = phase2_setup.scaffold(edit, "shortform", install=False)
            self.assertTrue((output / "package.json").is_file())
            self.assertEqual((output / "public" / "cut.mp4").read_bytes(), b"cut")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                phase2_setup.scaffold(edit, "shortform", install=False)

    def test_neutral_track_uses_validated_edit_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edvid phase2 ") as tmp:
            root = Path(tmp)
            source = root / "edit-data.json"
            output = root / "public" / "track.json"
            source.write_text(
                json.dumps(
                    {
                        "durationSec": 2,
                        "fps": 30,
                        "width": 1080,
                        "height": 1920,
                        "camera": {"targetX": 540, "targetY": 640},
                    }
                ),
                encoding="utf-8",
            )
            phase2_setup.neutral_track(source, output)
            track = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(track["count"], 60)
            self.assertEqual(track["points"][0], [540.0, 640.0])
            self.assertTrue(track["neutral"])

    def test_segments_use_encoded_frame_counts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="edvid phase2 ") as tmp:
            edit = Path(tmp) / "edição"
            clips = edit / "clips_graded"
            clips.mkdir(parents=True)
            (edit / "cut.mp4").write_bytes(b"cut")
            (edit / "edl.json").write_text(
                json.dumps({"ranges": [{}, {}]}), encoding="utf-8"
            )
            first = clips / "seg_000_v.mp4"
            second = clips / "seg_001_v.mp4"
            first.write_bytes(b"1")
            second.write_bytes(b"2")

            counts = {first.resolve(): 30, second.resolve(): 60, (edit / "cut.mp4").resolve(): 90}
            with patch.object(phase2_setup, "frame_count", side_effect=lambda p: counts[p.resolve()]), patch.object(
                phase2_setup, "video_fps", return_value=30.0
            ):
                output = phase2_setup.build_segments(edit)

            data = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                data["segments"],
                [{"start": 0.0, "dur": 1.0}, {"start": 1.0, "dur": 2.0}],
            )


if __name__ == "__main__":
    unittest.main()
