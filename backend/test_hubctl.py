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


class StampSiteTests(unittest.TestCase):
    def test_html_root_names_the_hub(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / "index.html"
            index.write_text('<html lang="da"><head></head></html>', encoding="utf-8")
            hubctl.stamp_site(index, "garden")
            text = index.read_text(encoding="utf-8")
            self.assertIn('data-hue-site="garden"', text)
            self.assertNotIn('data-hue-site="home"', text)


class PageCommitTests(unittest.TestCase):
    def test_baked_commit_wins_over_the_stamp(self):
        self.assertEqual(hubctl.page_commit_to_guard(SHA, OTHER), SHA)

    def test_stamp_is_used_when_the_page_has_no_build_id(self):
        self.assertEqual(hubctl.page_commit_to_guard("", OTHER), OTHER)
        self.assertEqual(hubctl.page_commit_to_guard("nope", OTHER), OTHER)


class PromoteLivePageTests(unittest.TestCase):
    def test_copies_the_running_page_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "backend" / "static"
            src.mkdir(parents=True)
            (src / "index.html").write_text("live", encoding="utf-8")
            (src / "chunk.js").write_text("js", encoding="utf-8")
            self.assertEqual(hubctl.promote_live_page(root), "copied")
            self.assertEqual((root / "served" / "index.html").read_text(encoding="utf-8"), "live")
            self.assertEqual((root / "served" / "chunk.js").read_text(encoding="utf-8"), "js")
            self.assertEqual(hubctl.promote_live_page(root), "exists")

    def test_does_nothing_when_there_is_no_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(hubctl.promote_live_page(Path(tmp)), "none")


class UnstampedStaticTests(unittest.TestCase):
    def test_blocks_an_unstamped_page(self):
        self.assertTrue(hubctl.unstamped_page_blocks_static("", False))
        self.assertTrue(hubctl.unstamped_page_blocks_static("nope", False))

    def test_allows_a_baked_commit_or_an_explicit_replace(self):
        self.assertFalse(hubctl.unstamped_page_blocks_static(SHA, False))
        self.assertFalse(hubctl.unstamped_page_blocks_static("", True))


class StaticArgsTests(unittest.TestCase):
    def test_flag_is_optional(self):
        self.assertEqual(hubctl._static_args(["garden"]), (["garden"], False))
        self.assertEqual(
            hubctl._static_args(["both", "--replace-unstamped"]),
            (["home", "garden"], True),
        )

    def test_unknown_flag_is_refused(self):
        with self.assertRaises(SystemExit):
            hubctl._static_args(["garden", "--force"])
