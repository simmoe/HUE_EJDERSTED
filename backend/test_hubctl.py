import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
hubctl = types.ModuleType("hubctl")
hubctl.__file__ = str(ROOT / "scripts" / "hubctl")
exec(compile(Path(hubctl.__file__).read_text(encoding="utf-8"), hubctl.__file__, "exec"), hubctl.__dict__)

SHA = "a" * 40
OTHER = "b" * 40


class StageBackendTests(unittest.TestCase):
    def test_page_directory_is_left_behind(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "backend"
            page = src / "static"
            page.mkdir(parents=True)
            (page / "index.html").write_text("<html></html>", encoding="utf-8")
            (src / "main.py").write_text("print('ok')\n", encoding="utf-8")
            dest = Path(tmp) / "stage"
            hubctl.stage_backend(src, dest)
            self.assertTrue((dest / "main.py").is_file())
            self.assertFalse((dest / "static").exists())


class StampBuildTests(unittest.TestCase):
    def test_commit_is_written_into_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp)
            (page / "index.html").write_text("<html><head><title>x</title></head></html>", encoding="utf-8")
            hubctl.stamp_build(page, SHA)
            text = (page / "index.html").read_text(encoding="utf-8")
            self.assertIn(f'content="{SHA}"', text)
            self.assertEqual((page / "build-id").read_text(encoding="utf-8").strip(), SHA)

    def test_bad_sha_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp)
            (page / "index.html").write_text("<html><head></head></html>", encoding="utf-8")
            with self.assertRaises(SystemExit):
                hubctl.stamp_build(page, "not-a-sha")


class PageCommitTests(unittest.TestCase):
    def test_baked_commit_wins_over_the_stamp(self):
        self.assertEqual(hubctl.page_commit_to_guard(SHA, OTHER), SHA)

    def test_stamp_is_used_when_the_page_has_no_build_id(self):
        self.assertEqual(hubctl.page_commit_to_guard("", OTHER), OTHER)
        self.assertEqual(hubctl.page_commit_to_guard("nope", OTHER), OTHER)
