from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HELPERS = Path(__file__).resolve().parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))

import transcribe  # noqa: E402


class FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"text": "ola mundo", "language": "pt", "words": []}


class OpenAITranscriptionTests(unittest.TestCase):
    def test_openai_request_uses_verbose_word_timestamps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "clip.mp3"
            audio.write_bytes(b"audio")
            with mock.patch.object(transcribe.requests, "post", return_value=FakeResponse()) as post:
                payload = transcribe.call_openai(audio, "secret", language="pt")

        self.assertEqual(payload["text"], "ola mundo")
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], transcribe.OPENAI_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")
        self.assertIn(("model", "whisper-1"), kwargs["data"])
        self.assertIn(("response_format", "verbose_json"), kwargs["data"])
        self.assertIn(("timestamp_granularities[]", "word"), kwargs["data"])
        self.assertIn(("timestamp_granularities[]", "segment"), kwargs["data"])
        self.assertIn(("language", "pt"), kwargs["data"])

    def test_rejects_models_without_word_timestamps(self) -> None:
        with self.assertRaisesRegex(ValueError, "word timestamps"):
            transcribe.call_openai(Path("unused.mp3"), "secret", model="gpt-4o-transcribe")

    def test_openai_words_are_normalized_to_edvid_schema(self) -> None:
        words = [
            {"word": "ola", "start": 0.1, "end": 0.4},
            {"word": "mundo", "start": 0.7, "end": 1.0},
        ]

        normalized = transcribe._to_scribe_words(words, offset=2.0)

        self.assertEqual([item["type"] for item in normalized], ["word", "spacing", "word"])
        self.assertEqual(normalized[0]["start"], 2.1)
        self.assertEqual(normalized[1]["start"], 2.4)
        self.assertEqual(normalized[1]["end"], 2.7)
        self.assertEqual(normalized[2]["speaker_id"], "speaker_0")

    def test_auto_backend_falls_back_to_openai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "clip.mp4"
            video.write_bytes(b"video")
            edit = root / "edit"
            captured: dict[str, str] = {}

            def fake_extract(_video: Path, dest: Path) -> None:
                dest.write_bytes(b"audio")

            def fake_transcribe(
                _audio: Path,
                _key: str,
                _model: str,
                _language: str | None,
                _verbose: bool,
                **kwargs: object,
            ) -> dict:
                captured["backend"] = str(kwargs["backend"])
                return {"text": "ok", "words": [], "language": "pt", "language_code": "pt"}

            with (
                mock.patch.object(transcribe, "_probe_duration", return_value=700.0),
                mock.patch.object(transcribe, "extract_audio", side_effect=fake_extract),
                mock.patch.object(transcribe, "_transcribe_audio", side_effect=fake_transcribe),
            ):
                out = transcribe.transcribe_one(
                    video=video,
                    edit_dir=edit,
                    api_key="secret",
                    elevenlabs_key="",
                    backend="auto",
                    verbose=False,
                )

        self.assertEqual(captured["backend"], "openai")
        self.assertEqual(out.name, "clip.json")


if __name__ == "__main__":
    unittest.main()
