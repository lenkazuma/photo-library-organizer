"""Offline tests using synthetic directories/bytes; no real images or OCR needed."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import ocr_detector
import text_review as review


TEXT = {'n': 8, 'coverage': .4, 'uniform': .8, 'rows': .5}
PHOTO = {'n': 0, 'coverage': 0, 'uniform': .1, 'rows': 0}


class TextReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'photos'
        self.root.mkdir()
        self.state_dir = self.base / 'private-state'
        self.state = self.state_dir / 'text-review'

    def photo(self, relative='202601/a.jpg', content=b'synthetic image bytes'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def scan(self, **options):
        return review.scan(self.root, self.state_dir, detector=lambda path: TEXT, **options)

    def decisions(self, values=None):
        data = review.read_json(self.state / 'decisions.template.json')
        data['decisions'] = values if values is not None else {key: 'text' for key in data['decisions']}
        output = self.state / 'decisions.json'
        review.write_json(output, data)
        return output

    def test_scope_and_no_historical_cache_import(self):
        self.photo()
        for relative in ('202601/text/chat.jpg', '202601/screenshot/chat.jpg',
                         '202601/video/still.jpg', '202601/nested/a.jpg',
                         '202601_a/a.jpg', '202613/a.jpg', 'outside.jpg'):
            self.photo(relative)
        historical = self.root / '_monthly_text'
        historical.mkdir()
        (historical / 'cache.json').write_text('not valid json', encoding='utf-8')
        result = self.scan()
        self.assertEqual(result['images'], 1)
        self.assertEqual(result['candidates'], 1)
        self.assertEqual(review.read_json(self.state / 'current.json')['candidates'][0]['path'], '202601/a.jpg')

    def test_cache_fingerprint_and_reconsider_decisions(self):
        source = self.photo()
        self.scan()
        result = review.scan(self.root, self.state_dir, detector=lambda path: self.fail('cache missed'))
        self.assertEqual(result['cached'], 1)
        chosen = self.decisions({'0': 'keep'})
        review.apply_decisions(self.root, chosen, self.state_dir, apply=True)
        self.assertEqual(self.scan()['candidates'], 0)
        self.assertEqual(self.scan(reconsider_all=True)['candidates'], 1)
        source.write_bytes(b'different synthetic image bytes')
        self.assertEqual(self.scan()['detected'], 1)

    def test_dry_run_and_collision_safe_hash_verified_move(self):
        source = self.photo()
        existing = self.photo('202601/text/a.jpg', b'already categorized')
        original_digest = review.sha256(source)
        self.scan()
        chosen = self.decisions()
        result = review.apply_decisions(self.root, chosen, self.state_dir)
        self.assertTrue(result['dry_run'])
        self.assertTrue(source.exists())
        self.assertFalse((self.root / '202601/text/a_text_1.jpg').exists())
        self.assertFalse((self.state / 'reviewed.json').exists())
        result = review.apply_decisions(self.root, chosen, self.state_dir, apply=True)
        self.assertFalse(source.exists())
        self.assertEqual(existing.read_bytes(), b'already categorized')
        self.assertEqual(review.sha256(self.root / '202601/text/a_text_1.jpg'), original_digest)
        events = [json.loads(line) for line in Path(result['journal']).read_text(encoding='utf-8').splitlines()]
        self.assertEqual([event['status'] for event in events], ['planned', 'copy_verified', 'verified'])
        self.assertEqual(self.scan()['candidates'], 0)

    def test_all_decisions_required_and_stale_scan_rejected(self):
        self.photo()
        self.scan()
        template = self.state / 'decisions.template.json'
        with self.assertRaisesRegex(ValueError, 'explicit text or keep'):
            review.apply_decisions(self.root, template, self.state_dir, apply=True)
        chosen = self.decisions()
        self.scan()
        with self.assertRaisesRegex(ValueError, 'different scan'):
            review.apply_decisions(self.root, chosen, self.state_dir, apply=True)

    def test_preflight_stale_source_preserves_entire_batch(self):
        first = self.photo()
        second = self.photo('202601/b.jpg')
        self.scan()
        chosen = self.decisions()
        second.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'changed since scan'):
            review.apply_decisions(self.root, chosen, self.state_dir, apply=True)
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertFalse((self.root / '202601/text').exists())

    def test_source_traversal_rejected(self):
        self.photo()
        self.scan()
        chosen = self.decisions()
        manifest = review.read_json(self.state / 'current.json')
        manifest['candidates'][0]['path'] = '../outside.jpg'
        review.write_json(self.state / 'current.json', manifest)
        with self.assertRaisesRegex(ValueError, 'outside allowed month'):
            review.apply_decisions(self.root, chosen, self.state_dir, apply=True)

    def test_state_bound_to_library(self):
        self.scan()
        other = self.base / 'other'
        other.mkdir()
        with self.assertRaisesRegex(ValueError, 'different root'):
            review.scan(other, self.state_dir, detector=lambda path: TEXT)

    def test_unrecognized_state_refused(self):
        self.state.mkdir(parents=True)
        (self.state / 'cache.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'unrecognized'):
            self.scan()

    def test_detection_failure_keeps_image_and_reports_error(self):
        source = self.photo()
        def broken(path):
            raise RuntimeError('decoder unavailable')
        result = review.scan(self.root, self.state_dir, detector=broken)
        self.assertEqual(result['errors_retained'], 1)
        self.assertEqual(result['candidates'], 0)
        self.assertTrue(source.exists())

    def test_include_all_for_low_score_images(self):
        self.photo()
        result = review.scan(self.root, self.state_dir, detector=lambda path: PHOTO)
        self.assertEqual(result['candidates'], 0)
        result = review.scan(self.root, self.state_dir, include_all=True, detector=lambda path: self.fail('cache missed'))
        self.assertEqual(result['candidates'], 1)

    def test_file_appearing_after_plan_is_never_overwritten(self):
        source = self.photo()
        self.scan()
        chosen = self.decisions()
        original = review.destination_for
        def race(source, reserved):
            destination = original(source, reserved)
            destination.parent.mkdir()
            destination.write_bytes(b'new file appeared')
            return destination
        with patch.object(review, 'destination_for', side_effect=race):
            with self.assertRaises(FileExistsError):
                review.apply_decisions(self.root, chosen, self.state_dir, apply=True)
        self.assertTrue(source.exists())
        self.assertEqual((self.root / '202601/text/a.jpg').read_bytes(), b'new file appeared')

    def test_copy_hash_failure_retains_original(self):
        source = self.photo()
        self.scan()
        chosen = self.decisions()
        with patch.object(review.shutil, 'copyfileobj', side_effect=lambda incoming, outgoing, length: outgoing.write(b'corrupt')):
            with self.assertRaisesRegex(RuntimeError, 'Verification failed'):
                review.apply_decisions(self.root, chosen, self.state_dir, apply=True)
        self.assertTrue(source.exists())
        self.assertEqual(source.read_bytes(), b'synthetic image bytes')

    def test_links_skipped_and_category_link_refused(self):
        source = self.photo()
        outside = self.base / 'outside'
        outside.mkdir()
        link = self.root / '202602'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f'Symlink creation unavailable: {exc}')
        self.addCleanup(link.unlink)
        (outside / 'b.jpg').write_bytes(b'synthetic')
        self.assertEqual(self.scan()['images'], 1)
        chosen = self.decisions()
        category = source.parent / 'text'
        category.symlink_to(outside, target_is_directory=True)
        self.addCleanup(category.unlink)
        with self.assertRaisesRegex(ValueError, 'links and junctions'):
            review.apply_decisions(self.root, chosen, self.state_dir, apply=True)

    def test_preview_escapes_names_and_needs_no_pillow(self):
        # Ampersands are valid filenames on Windows and exercise HTML escaping.
        self.photo('202601/a&b.jpg')
        self.scan()
        result = review.preview(self.root, self.state_dir)
        page = Path(result['preview']).read_text(encoding='utf-8')
        self.assertIn('a&amp;b.jpg', page)
        self.assertIn('file:///', page)

    def test_help_needs_no_third_party_packages(self):
        result = subprocess.run([sys.executable, '-S', str(Path(review.__file__)), '--help'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--root', result.stdout)

    def test_sparse_chat_shortlisted(self):
        self.assertTrue(ocr_detector.is_candidate({'n': 2, 'coverage': .02, 'uniform': .5, 'rows': .02}))
        self.assertFalse(ocr_detector.is_candidate(PHOTO))


if __name__ == '__main__':
    unittest.main()
