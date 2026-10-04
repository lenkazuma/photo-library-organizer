import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class UnicodeCliTests(unittest.TestCase):
    def run_cli(self, script, *args):
        env = {**os.environ, "PYTHONIOENCODING": "cp1252:strict", "PYTHONUTF8": "0"}
        result = subprocess.run([sys.executable, "-S", str(REPO / script), *map(str, args)],
                                capture_output=True, encoding="utf-8", env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_bilingual_help_under_legacy_encoding(self):
        for script in ["organize_photos.py", "photo_metadata.py", "text_review.py"]:
            with self.subTest(script=script):
                self.assertIn("usage:", self.run_cli(script, "--help"))

    def test_unicode_json_and_paths_under_legacy_encoding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "照片"
            root.mkdir()
            output = self.run_cli("text_review.py", "scan", "--root", root)
            self.assertIn("照片", json.loads(output)["state_directory"])
            preview = self.run_cli("organize_photos.py", "--root", root, "--lang", "zh")
            self.assertIn("仅预览", preview)
            index = Path(temp) / "index.jsonl"
            index.write_text(json.dumps({"path": "照片.jpg", "captured_at": "2024-01-01T10:00:00", "camera": {"model": "相机"}}, ensure_ascii=False) + "\n", encoding="utf-8")
            output = self.run_cli("photo_metadata.py", "search", index, "--lang", "zh")
            self.assertEqual(json.loads(output)["results"][0]["path"], "照片.jpg")


if __name__ == "__main__":
    unittest.main()
