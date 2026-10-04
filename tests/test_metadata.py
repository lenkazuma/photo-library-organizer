"""Synthetic-only tests: no personal files, photos, network, or API credentials."""
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from fractions import Fraction

import photo_metadata as pm


def sample(path="sample.jpg", captured="2024-03-01T12:00:00+11:00", country="AU", **changes):
    result = {
        "schema_version": 1, "path": path, "filename": Path(path).name,
        "captured_at": captured, "capture_time_source": "exif:DateTimeOriginal",
        "timezone_status": "known_offset", "camera": {"make": "Example", "model": "Camera 1"},
        "gps": {"latitude": -33.86, "longitude": 151.21},
        "place": {"id": "sydney", "name": "Sydney", "name_zh": "悉尼", "country": country} if country else None,
    }
    result.update(changes)
    return result


class ExifTests(unittest.TestCase):
    def test_original_offset_gps_and_camera(self):
        exif = {271: "Example\x00", 272: "Synthetic Camera"}
        exif_ifd = {36867: "2024:03:01 12:00:00", 36881: "+11:00"}
        gps = {1: "S", 2: (33, 51, 36), 3: "E", 4: (151, 12, 36), 5: b"\x01", 6: Fraction(25, 2), 29: "2024:03:01", 7: (1, 0, Fraction(1, 2))}
        record = pm.metadata_from_exif(exif, exif_ifd, gps)
        self.assertEqual(record["captured_at"], "2024-03-01T12:00:00+11:00")
        self.assertEqual(record["timezone_status"], "known_offset")
        self.assertEqual(record["camera"]["make"], "Example")
        self.assertAlmostEqual(record["gps"]["latitude"], -33.86)
        self.assertAlmostEqual(record["gps"]["longitude"], 151.21)
        self.assertEqual(record["gps"]["altitude_m"], -12.5)
        self.assertEqual(record["gps"]["timestamp_utc"], "2024-03-01T01:00:00.500000+00:00")

    def test_bad_offset_preserves_wall_clock_without_inventing_zone(self):
        record = pm.metadata_from_exif({36867: "2024:03:01 12:00:00", 36881: "+25:00"})
        self.assertEqual(record["captured_at"], "2024-03-01T12:00:00")
        self.assertEqual(record["timezone_status"], "unknown")
        self.assertTrue(record["warnings"])

    def test_gps_time_fallback_has_distinct_source(self):
        record = pm.metadata_from_exif({}, gps_ifd={29: "2024:03:01", 7: (1, 2, 3)})
        self.assertEqual(record["captured_at"], "2024-03-01T01:02:03+00:00")
        self.assertEqual(record["capture_time_source"], "exif:GPSDateStamp+GPSTimeStamp")
        self.assertIsNone(record["gps"])

    def test_gps_clock_does_not_supply_timezone_to_original(self):
        record = pm.metadata_from_exif({36867: "2024:03:01 12:00:00"}, gps_ifd={29: "2024:03:01", 7: (1, 0, 0)})
        self.assertEqual(record["timezone_status"], "unknown")
        self.assertEqual(record["captured_at"], "2024-03-01T12:00:00")

    def test_modification_exif_is_not_capture_time(self):
        record = pm.metadata_from_exif({306: "2024:03:01 12:00:00"})
        self.assertIsNone(record["captured_at"])
        self.assertEqual(record["timezone_status"], "missing")

    def test_invalid_gps_is_retained_as_warning_not_location(self):
        for lat in ((91, 0, 0), (1, 60, 0), (float("nan"), 0, 0)):
            record = pm.metadata_from_exif({}, gps_ifd={1: "N", 2: lat, 3: "E", 4: (1, 0, 0)})
            self.assertIsNone(record["gps"])
            self.assertTrue(record["warnings"])

    def test_missing_hemisphere_does_not_guess(self):
        record = pm.metadata_from_exif({}, gps_ifd={2: (33, 0, 0), 3: "E", 4: (151, 0, 0)})
        self.assertIsNone(record["gps"])


class PlaceTests(unittest.TestCase):
    def load(self, entries):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "places.json"
            path.write_text(json.dumps({"places": entries}), encoding="utf-8")
            return pm.load_places(path)

    def test_overlap_prefers_smallest_and_records_all_matches(self):
        places = self.load([
            {"id": "big", "name": "Broad example", "country": "A", "bbox": [140, -40, 160, -20]},
            {"id": "small", "name": "Small example", "country": "B", "bbox": [151, -34, 152, -33]},
        ])
        match = pm.match_place(-33.86, 151.21, places)
        self.assertEqual(match["id"], "small")
        self.assertEqual(match["matched_ids"], ["small", "big"])
        self.assertEqual(match["matched_countries"], ["B", "A"])
        self.assertIsNone(pm.match_place(0, 0, places))

    def test_antimeridian_rectangle(self):
        places = self.load([{"id": "date-line", "name": "Example", "country": "Example", "bbox": [170, -20, -170, 20]}])
        self.assertIsNotNone(pm.match_place(0, 179, places))
        self.assertIsNotNone(pm.match_place(0, -179, places))
        self.assertIsNone(pm.match_place(0, 0, places))

    def test_invalid_regions_fail_before_indexing(self):
        for bbox in ([0, 10, 1, 0], [0, 0, 0, 1], [0, 0, float("nan"), 1], [-181, 0, 1, 1]):
            with self.assertRaises(ValueError):
                self.load([{"id": "x", "name": "X", "country": "X", "bbox": bbox}])

    def test_duplicate_ids_rejected(self):
        item = {"id": "x", "name": "X", "country": "X", "bbox": [0, 0, 1, 1]}
        with self.assertRaises(ValueError):
            self.load([item, item])


class SearchTests(unittest.TestCase):
    def test_inclusive_local_date_boundaries(self):
        records = [sample("late.jpg", "2024-03-01T23:59:59+11:00"), sample("naive.jpg", "2024-03-01T00:00:00"), sample("next.jpg", "2024-03-02T00:00:00"), sample("missing.jpg", None)]
        result, skipped = pm.search_records(records, after=pm.parse_boundary("2024-03-01"), before=pm.parse_boundary("2024-03-01"))
        self.assertEqual([r["path"] for r in result], ["late.jpg", "naive.jpg"])
        self.assertEqual(skipped["missing_or_invalid_time"], 1)

    def test_aware_boundary_compares_real_instants_and_skips_unknown_zones(self):
        records = [sample("aware.jpg", "2024-03-01T12:00:00+11:00"), sample("naive.jpg", "2024-03-01T12:00:00")]
        result, skipped = pm.search_records(records, after=pm.parse_boundary("2024-03-01T00:59:00Z"), before=pm.parse_boundary("2024-03-01T01:01:00Z"))
        self.assertEqual([r["path"] for r in result], ["aware.jpg"])
        self.assertEqual(skipped["incomparable_timezone"], 1)

    def test_naive_boundary_does_not_strip_known_zone(self):
        result, skipped = pm.search_records([sample()], after=pm.parse_boundary("2024-03-01T00:00:00"))
        self.assertEqual(result, [])
        self.assertEqual(skipped["incomparable_timezone"], 1)

    def test_combined_filters(self):
        result, _ = pm.search_records([sample("Holiday.JPG"), sample("other.jpg")], camera="CAMERA 1", filename="holiday", place="悉尼", country="au", near=(-33.86, 151.21, 1))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["distance_km"], 0)
        result, _ = pm.search_records([sample()], near=(0, 0, 1))
        self.assertFalse(result)

    def test_invalid_near_values(self):
        for values in ((91, 0, 1), (0, 181, 1), (0, 0, -1), (0, 0, float("nan")), (float("inf"), 0, 1)):
            with self.assertRaises(ValueError):
                pm.search_records([], near=values)

    def test_invalid_boundary_order_and_awareness(self):
        with self.assertRaises(ValueError):
            pm.search_records([], after=pm.parse_boundary("2024-03-02"), before=pm.parse_boundary("2024-03-01"))
        with self.assertRaises(ValueError):
            pm.search_records([], after=pm.parse_boundary("2024-03-01T00:00:00Z"), before=pm.parse_boundary("2024-03-02T00:00:00"))

    def test_antipodal_distance_and_invalid_index_coordinates(self):
        self.assertAlmostEqual(pm.distance_km(0, 0, 0, 180), 20015.114, places=2)
        result, skipped = pm.search_records([sample(gps={"latitude": 100, "longitude": 0})], near=(0, 0, 5))
        self.assertEqual(result, [])
        self.assertEqual(skipped["invalid_gps"], 1)


class TimelineTests(unittest.TestCase):
    def test_latest_known_country_observation_and_unknown_gap(self):
        records = [sample("au1.jpg", "2024-03-01T12:00:00", "AU"), sample("au2.jpg", "2024-03-02T12:00:00", "AU"), sample("unknown.jpg", "2024-03-03T12:00:00", None), sample("cn.jpg", "2024-03-05T12:00:00", "CN")]
        result = pm.build_timeline(records)
        self.assertEqual(len(result["possible_movements"]), 1)
        movement = result["possible_movements"][0]
        self.assertEqual(movement["from_path"], "au2.jpg")
        self.assertEqual(movement["to_path"], "cn.jpg")
        self.assertEqual(movement["unclassified_observations_between"], 1)
        self.assertEqual(movement["chronology_status"], "uncertain_local_time_order")
        self.assertIn("not official", result["notice"])

    def test_mixed_zones_retained_and_warned(self):
        result = pm.build_timeline([sample("aware.jpg"), sample("naive.jpg", "2024-03-02T10:00:00", "CN"), sample("missing.jpg", None)], "zh")
        self.assertEqual(result["order_basis"], "local_capture_time")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["omitted_missing_or_invalid_time"], 1)
        self.assertEqual(result["observations"][1]["timezone_status"], "unknown")
        self.assertIn("不是官方", result["notice"])

    def test_all_offsets_known_sort_by_utc_instant(self):
        result = pm.build_timeline([sample("later.jpg", "2024-03-01T09:00:00Z", "CN"), sample("earlier.jpg", "2024-03-01T12:00:00+11:00", "AU")])
        self.assertEqual(result["order_basis"], "utc_instant")
        self.assertEqual([r["path"] for r in result["observations"]], ["earlier.jpg", "later.jpg"])
        self.assertEqual(result["possible_movements"][0]["from_country"], "AU")

    def test_modification_time_excluded_by_default(self):
        records = [sample(capture_time_source="filesystem:mtime_fallback")]
        result = pm.build_timeline(records)
        self.assertEqual(result["excluded_mtime_records"], 1)
        self.assertEqual(result["observations"], [])
        result = pm.build_timeline(records, include_mtime=True)
        self.assertEqual(len(result["observations"]), 1)
        self.assertTrue(result["warnings"])

    def test_simultaneous_different_country_observations_flagged(self):
        result = pm.build_timeline([sample("a.jpg", country="AU"), sample("b.jpg", country="CN")])
        self.assertEqual(result["possible_movements"][0]["chronology_status"], "simultaneous_or_conflicting_observations")

    def test_conflicting_regions_are_not_treated_as_country_changes(self):
        conflict = sample("ambiguous.jpg", "2024-03-02T12:00:00+11:00", "CN")
        conflict["place"]["matched_countries"] = ["CN", "AU"]
        conflict["warnings"] = ["Synthetic conflicting region labels"]
        conflict["errors"] = ["Synthetic metadata issue"]
        result = pm.build_timeline([sample("first.jpg"), conflict, sample("last.jpg", "2024-03-03T12:00:00+11:00", "AU")])
        self.assertEqual(len(result["observations"]), 3)
        self.assertEqual(result["possible_movements"], [])
        self.assertEqual(result["conflicting_region_observations"], 1)
        self.assertEqual(result["observations"][1]["country_label_status"], "conflicting_regions")
        exported = pm.timeline_csv(result, "en")
        self.assertIn("Synthetic conflicting region labels", exported)
        self.assertIn("Synthetic metadata issue", exported)
        self.assertIn("excluded from country-change inference", exported)

    def test_csv_localization_and_formula_protection(self):
        record = sample("=HYPERLINK.jpg")
        rows = list(csv.reader(io.StringIO(pm.search_csv([record], "zh"))))
        self.assertEqual(rows[0][0], "路径")
        self.assertEqual(rows[1][0], "'=HYPERLINK.jpg")
        self.assertEqual(rows[1][6], "-33.86")
        protected = list(csv.reader(io.StringIO(pm.search_csv([sample("  =HYPERLINK.jpg")], "en"))))
        self.assertTrue(protected[1][0].startswith("'"))
        timeline = pm.build_timeline([record], "zh")
        rows = list(csv.reader(io.StringIO(pm.timeline_csv(timeline, "zh"))))
        self.assertEqual(rows[0][0], "类型")
        self.assertIn("不是官方", rows[1][-1])


class FileAndCliTests(unittest.TestCase):
    def test_walk_skips_hidden_underscore_git_and_non_photo_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("normal", ".hidden", "_archive", ".git"):
                (root / name).mkdir()
                (root / name / "one.jpg").write_bytes(b"synthetic")
            (root / "normal" / "clip.mp4").write_bytes(b"synthetic")
            (root / ".secret.jpg").write_bytes(b"synthetic")
            self.assertEqual([p.relative_to(root).as_posix() for p in pm.iter_media(root)], ["normal/one.jpg"])
            included = [p.relative_to(root).as_posix() for p in pm.iter_media(root, True)]
            self.assertEqual(len(included), 4)
            self.assertFalse(any(".git/" in p for p in included))

    def test_symlink_is_never_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real").mkdir()
            (root / "real" / "one.jpg").write_bytes(b"synthetic")
            try:
                (root / "linked").symlink_to(root / "real", target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable on this system")
            self.assertEqual(len(list(pm.iter_media(root))), 1)

    def test_output_overwrite_and_media_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "output.json"
            pm.write_output(target, ["first"])
            with self.assertRaises(ValueError):
                pm.write_output(target, ["second"])
            self.assertEqual(target.read_text(), "first")
            pm.write_output(target, ["second"], overwrite=True)
            self.assertEqual(target.read_text(), "second")
            with self.assertRaises(ValueError):
                pm.write_output(Path(directory) / "photo.jpg", ["data"])
            video = Path(directory) / "video.mp4"
            video.write_bytes(b"synthetic video")
            with self.assertRaises(ValueError):
                pm.write_output(video, ["data"], overwrite=True)
            self.assertEqual(video.read_bytes(), b"synthetic video")

    def test_output_symlink_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "real").mkdir()
            try:
                (root / "linked").symlink_to(root / "real", target_is_directory=True)
            except OSError:
                self.skipTest("symlink creation is unavailable on this system")
            with self.assertRaises(ValueError):
                pm.write_output(root / "linked" / "output.json", ["data"])
            self.assertFalse((root / "real" / "output.json").exists())

    def test_invalid_index_line_reports_position(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "index.jsonl"
            path.write_text(json.dumps(sample()) + "\n{bad\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "line 2"):
                list(pm.load_records(path))

    def test_search_cli_runs_without_site_packages_or_pillow(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.jsonl"
            index.write_text(json.dumps(sample()) + "\n", encoding="utf-8")
            result = subprocess.run([sys.executable, "-S", str(Path(pm.__file__)), "search", str(index), "--country", "AU"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["count"], 1)

    def test_cli_rejects_bad_radius_and_source_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            index = Path(directory) / "index.jsonl"
            index.write_text(json.dumps(sample()) + "\n", encoding="utf-8")
            for extra in (["--near", "91", "0", "1"], ["--output", str(index), "--overwrite"], ["--output", str(Path(directory) / "video.mp4"), "--overwrite"], ["--format", "csv", "--output", str(Path(directory) / "wrong.json")]):
                result = subprocess.run([sys.executable, str(Path(pm.__file__)), "search", str(index), *extra], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(index.read_text())["path"], "sample.jpg")

    def test_corrupt_photo_has_error_and_no_implicit_mtime(self):
        class BrokenReader:
            @staticmethod
            def open(path):
                raise OSError("synthetic corrupt image")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "broken.jpg"
            path.write_bytes(b"synthetic")
            record = pm.read_metadata(path, root, BrokenReader)
            self.assertIsNone(record["captured_at"])
            self.assertTrue(record["errors"])
            self.assertIn("gps", record["missing"])
            fallback = pm.read_metadata(path, root, BrokenReader, mtime_fallback=True)
            self.assertEqual(fallback["capture_time_source"], "filesystem:mtime_fallback")
            self.assertIsNotNone(fallback["captured_at"])

    def test_generated_jpeg_exif_round_trip(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed; metadata dictionary tests still run")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "synthetic.jpg"
            exif = Image.Exif()
            exif[271], exif[272] = "Example", "Generated fixture"
            exif[34665] = {36867: "2024:03:01 12:00:00", 36881: "+11:00"}
            exif[34853] = {1: "S", 2: (33.0, 51.0, 36.0), 3: "E", 4: (151.0, 12.0, 36.0)}
            Image.new("RGB", (8, 6), "white").save(path, exif=exif)
            before = path.read_bytes()
            record = pm.read_metadata(path, root, Image)
            self.assertEqual(record["width"], 8)
            self.assertEqual(record["height"], 6)
            self.assertEqual(record["captured_at"], "2024-03-01T12:00:00+11:00")
            self.assertAlmostEqual(record["gps"]["latitude"], -33.86)
            self.assertEqual(record["errors"], [])
            self.assertEqual(path.read_bytes(), before)

    def test_index_search_timeline_cli_end_to_end_with_generated_photos(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            photos = root / "photos"
            photos.mkdir()
            for filename, day, offset, gps in (
                ("au.jpg", "2024:03:01 12:00:00", "+11:00", {1: "S", 2: (33.0, 51.0, 36.0), 3: "E", 4: (151.0, 12.0, 36.0)}),
                ("cn.jpg", "2024:03:05 12:00:00", "+08:00", {1: "N", 2: (22.0, 36.0, 0.0), 3: "E", 4: (114.0, 6.0, 0.0)}),
            ):
                exif = Image.Exif()
                exif[34665] = {36867: day, 36881: offset}
                exif[34853] = gps
                Image.new("RGB", (8, 8), "white").save(photos / filename, exif=exif)
            Image.new("RGB", (8, 8), "white").save(photos / "missing.png")
            originals = {path.name: path.read_bytes() for path in photos.iterdir()}
            index = root / "index.jsonl"
            script = str(Path(pm.__file__))
            places = Path(pm.__file__).parent / "examples" / "places.example.json"
            result = subprocess.run([sys.executable, script, "index", str(photos), "--places", str(places), "--output", str(index)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list(pm.load_records(index))), 3)
            result = subprocess.run([sys.executable, "-S", script, "search", str(index), "--country", "AU", "--near", "-33.86", "151.21", "1"], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual([r["path"] for r in json.loads(result.stdout)["results"]], ["au.jpg"])
            timeline = root / "timeline.json"
            result = subprocess.run([sys.executable, "-S", script, "timeline", str(index), "--lang", "zh", "--output", str(timeline)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(timeline.read_text(encoding="utf-8"))
            self.assertEqual(report["omitted_missing_or_invalid_time"], 1)
            self.assertEqual(len(report["possible_movements"]), 1)
            self.assertEqual(report["possible_movements"][0]["from_country"], "AU")
            self.assertEqual(report["possible_movements"][0]["to_country"], "CN")
            self.assertIn("不能确定准确过境日期", report["notice"])
            csv_path = root / "timeline.csv"
            result = subprocess.run([sys.executable, "-S", script, "timeline", str(index), "--lang", "zh", "--format", "csv", "--output", str(csv_path)], capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(csv_path.read_bytes().startswith(b"\xef\xbb\xbf"))
            self.assertIn("可能移动区间", csv_path.read_text(encoding="utf-8-sig"))
            self.assertEqual(originals, {path.name: path.read_bytes() for path in photos.iterdir()})


if __name__ == "__main__":
    unittest.main()
