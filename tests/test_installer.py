import tempfile
import unittest
from pathlib import Path

import edvid_install


class InstallerPayloadTests(unittest.TestCase):
    def make_source(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        for entry in edvid_install.SKILL_PAYLOAD:
            path = source / entry
            if entry in {"agents", "assets", "helpers", "references"}:
                path.mkdir()
                (path / "fixture.txt").write_text(entry, encoding="utf-8")
            else:
                path.write_text(f"{entry}\n", encoding="utf-8")
        (source / "desktop").mkdir()
        (source / "desktop" / "must-not-install.txt").write_text(
            "desktop source", encoding="utf-8")
        (source / "contributor-only.txt").write_text(
            "not part of the skill", encoding="utf-8")
        return source

    def test_edvid_install_copies_only_the_allowlisted_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            skills = root / "skills"

            destination = edvid_install.install_into(source, skills, force=False)

            self.assertEqual(destination, skills / "edvid")
            self.assertFalse((destination / "desktop").exists())
            self.assertFalse((destination / "contributor-only.txt").exists())
            for entry in edvid_install.SKILL_PAYLOAD:
                self.assertTrue((destination / entry).exists(), entry)

    def test_update_preserves_user_state_and_removes_old_repo_extras(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            skills = root / "skills"
            destination = skills / "edvid"
            (destination / ".venv").mkdir(parents=True)
            (destination / ".venv" / "keep.txt").write_text(
                "venv", encoding="utf-8")
            (destination / ".env").write_text("SECRET=preserved\n", encoding="utf-8")
            (destination / "desktop").mkdir()
            (destination / "desktop" / "stale.txt").write_text(
                "remove", encoding="utf-8")

            edvid_install.install_into(source, skills, force=False)

            self.assertEqual(
                (destination / ".venv" / "keep.txt").read_text(encoding="utf-8"),
                "venv",
            )
            self.assertEqual(
                (destination / ".env").read_text(encoding="utf-8"),
                "SECRET=preserved\n",
            )
            self.assertFalse((destination / "desktop").exists())

    def test_incomplete_payload_does_not_modify_existing_install(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.make_source(root)
            (source / "SKILL.md").unlink()
            destination = root / "skills" / "edvid"
            destination.mkdir(parents=True)
            marker = destination / "working-install.txt"
            marker.write_text("keep", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                edvid_install.install_into(source, root / "skills", force=False)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")

    def test_third_party_skill_keeps_its_complete_tree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "remotion"
            (source / "nested").mkdir(parents=True)
            (source / "nested" / "router.md").write_text(
                "router", encoding="utf-8")

            destination = edvid_install.install_into(
                source,
                root / "skills",
                force=False,
                name="remotion-best-practices",
            )

            self.assertEqual(
                (destination / "nested" / "router.md").read_text(encoding="utf-8"),
                "router",
            )


if __name__ == "__main__":
    unittest.main()
