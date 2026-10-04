import json
import tempfile
import unittest
from pathlib import Path

from organize_photos import Organizer, sha256


class OrganizerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def photo(self, name, data=b"synthetic photo"):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    def apply(self, org, plan):
        org.execute(plan, self.root / ".photo-organizer" / "audit.jsonl")

    def test_nested_month_and_existing_categories(self):
        a = self.photo("202603_a/202604_b/video.mp4")
        b = self.photo("202603_a/text/Screenshot.jpg", b"text")
        org = Organizer(self.root)
        self.assertEqual(org.target(a), self.root / "202604/video/video.mp4")
        self.assertEqual(org.target(b), self.root / "202603/text/Screenshot.jpg")

    def test_preview_does_not_write(self):
        p = self.photo("202603_a/a.jpg")
        before = sorted(str(x) for x in self.root.rglob("*"))
        Organizer(self.root).plan(deduplicate=True, remove_aae=True)
        self.assertEqual(before, sorted(str(x) for x in self.root.rglob("*")))
        self.assertTrue(p.exists())

    def test_deletions_require_opt_in(self):
        a = self.photo("202603/a.jpg")
        b = self.photo("202603/b.jpg")
        c = self.photo("202603/edit.aae", b"edits")
        org = Organizer(self.root)
        self.apply(org, org.plan())
        self.assertTrue(all(p.exists() for p in [a, b, c]))

    def test_verified_move_and_idempotency(self):
        src = self.photo("202603_a/a.jpg")
        org = Organizer(self.root)
        self.apply(org, org.plan())
        self.assertFalse(src.exists())
        self.assertEqual((self.root / "202603/a.jpg").read_bytes(), b"synthetic photo")
        self.assertEqual(org.plan(), [])
        for name in ["video", "text", "screenshot"]:
            self.assertTrue((self.root / "202603" / name).is_dir())
        statuses = [json.loads(s)["status"] for s in (self.root / ".photo-organizer/audit.jsonl").read_text().splitlines()]
        self.assertIn("verified", statuses)

    def test_collision_keeps_both_and_preview_reserves_names(self):
        self.photo("202603/x.jpg", b"a")
        self.photo("202603_a/x.jpg", b"b")
        self.photo("202603_b/x.jpg", b"c")
        org = Organizer(self.root)
        self.apply(org, org.plan())
        self.assertEqual({p.read_bytes() for p in (self.root / "202603").glob("*.jpg")}, {b"a", b"b", b"c"})

    def test_dedup_keeps_categorized_copy_after_move(self):
        self.photo("202603/a.jpg")
        self.photo("202603_b/text/a.jpg")
        org = Organizer(self.root)
        self.apply(org, org.plan(deduplicate=True))
        self.assertTrue((self.root / "202603/text/a.jpg").exists())
        self.assertFalse((self.root / "202603/a.jpg").exists())

    def test_only_aae_is_deleted(self):
        a = self.photo("202603/a.aae", b"edits")
        b = self.photo("202603/a.xmp", b"metadata")
        org = Organizer(self.root)
        self.apply(org, org.plan(remove_aae=True))
        self.assertFalse(a.exists())
        self.assertTrue(b.exists())

    def test_changed_source_aborts(self):
        src = self.photo("202603_a/a.jpg")
        org = Organizer(self.root)
        plan = org.plan()
        src.write_bytes(b"new data")
        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            self.apply(org, plan)
        self.assertEqual(src.read_bytes(), b"new data")

    def test_hidden_unknown_and_root_files_preserved(self):
        names = ["_private/a.jpg", "Holiday/a.jpg", "a.jpg", "202603/.hidden/a.jpg", "202603/unknown.txt"]
        paths = [self.photo(n) for n in names]
        org = Organizer(self.root)
        self.apply(org, org.plan(deduplicate=True))
        self.assertTrue(all(p.exists() for p in paths))

    def test_linked_destination_refused(self):
        self.photo("202603_a/a.jpg")
        target = self.root / "_elsewhere"
        target.mkdir()
        try:
            (self.root / "202603").symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not permit symlink creation")
        with self.assertRaises(ValueError):
            Organizer(self.root).plan()

    def test_traversal_refused(self):
        with self.assertRaises(ValueError):
            Organizer(self.root).safe(self.root / ".." / "outside.jpg")

    def test_exclusive_copy_does_not_overwrite(self):
        src = self.photo("202603_a/a.jpg", b"a")
        dest = self.photo("202603/a.jpg", b"b")
        with self.assertRaises(FileExistsError):
            Organizer(self.root).copy_verified(src, dest, sha256(src))
        self.assertEqual(dest.read_bytes(), b"b")
        self.assertTrue(src.exists())

    def test_screenshot_deferral(self):
        src = self.photo("202603_a/Screenshot.jpg")
        self.assertEqual(Organizer(self.root, defer_screenshots=True).target(src), self.root / "202603/Screenshot.jpg")
        self.assertEqual(Organizer(self.root).target(src), self.root / "202603/screenshot/Screenshot.jpg")

    def test_existing_category_takes_priority_over_extension(self):
        src = self.photo("202603_a/text/clip.mp4")
        self.assertEqual(Organizer(self.root).target(src), self.root / "202603/text/clip.mp4")

    def test_empty_and_nonmedia_months_get_categories(self):
        (self.root / "202601").mkdir()
        self.photo("202602/notes.txt", b"notes")
        self.photo("202603/edit.aae", b"edits")
        org = Organizer(self.root)
        self.apply(org, org.plan())
        for month in ["202601", "202602", "202603"]:
            for category in ["text", "screenshot", "video"]:
                self.assertTrue((self.root / month / category).is_dir())
        self.assertTrue((self.root / "202603/edit.aae").exists())


if __name__ == "__main__":
    unittest.main()
