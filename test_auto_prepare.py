import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from auto_prepare import select_bounds
import clip_uploader as uploader
import test_uploader


def setUpModule():
    test_uploader.setUpModule()


def tearDownModule():
    test_uploader.tearDownModule()


class SelectionTests(unittest.TestCase):
    def test_quiet_edges_are_removed_with_context(self):
        motion = [0]*10 + [20]*10 + [0]*20
        audio = [-60]*10 + [-15]*10 + [-60]*20
        start, end, reason = select_bounds(40, motion, audio, {})
        self.assertEqual((start, end), (7, 25))
        self.assertIn('trimmed', reason)

    def test_quiet_moving_footage_is_preserved(self):
        self.assertEqual(select_bounds(40, [20]*40, [-60]*40, {})[:2], (0, 40))

    def test_stationary_loud_footage_is_preserved(self):
        self.assertEqual(select_bounds(40, [0]*40, [-15]*40, {})[:2], (0, 40))

    def test_short_clips_and_missing_audio_are_preserved(self):
        self.assertEqual(select_bounds(11, [0]*11, [-60]*11, {})[:2], (0, 11))
        self.assertEqual(select_bounds(40, [0]*40, None, {})[:2], (0, 40))

    def test_no_activity_and_tiny_event_are_preserved(self):
        self.assertEqual(select_bounds(40, [0]*40, [-60]*40, {})[:2], (0, 40))
        self.assertEqual(select_bounds(90, [0]*40+[20]*3+[0]*47, [-60]*40+[-15]*3+[-60]*47, {})[:2], (0, 90))

    def test_middle_inactivity_is_preserved(self):
        self.assertEqual(select_bounds(40, [20]*5+[0]*30+[20]*5, [-15]*5+[-60]*30+[-15]*5, {})[:2], (0, 40))


class PreparationIntegrationTests(test_uploader.RecoveryTests):
    def test_prepared_media_uploaded_but_original_archived(self):
        prepared = self.base / 'prepared.mp4'
        prepared.write_bytes(b'prepared fixture')
        with patch('auto_prepare.prepare', return_value={'media_path': str(prepared), 'reason': 'trimmed'}) as prepare, \
             patch.object(uploader, 'upload_video', return_value=('video123', 'Title')) as upload, \
             patch.object(uploader, 'get_or_create_playlist', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'):
            self.assertTrue(uploader.process_file(Mock(), self.path, self.watch, self.cfg, self.hashes, self.records))
            self.assertEqual(upload.call_args.kwargs['media_path'], str(prepared))
            self.assertTrue(prepared.exists())
            self.assertEqual((self.base/'uploaded'/'Apex Legends'/'clip.mp4').read_bytes(), b'video fixture')


if __name__ == '__main__':
    unittest.main()
