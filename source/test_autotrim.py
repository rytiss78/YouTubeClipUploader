import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autotrim
from autotrim import select_bounds


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


class PrepareTests(unittest.TestCase):
    def test_disabled_uploads_original(self):
        self.assertEqual(autotrim.prepare(Path('clip.mp4'), 'h', {'enabled': False}, '.')['media_path'], 'clip.mp4')

    def test_failure_falls_back_to_original(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(autotrim, 'inspect_clip', side_effect=RuntimeError('ffmpeg broke')), \
             patch.object(autotrim.logging, 'warning'):
            result = autotrim.prepare(Path('clip.mp4'), 'h', {'enabled': True}, tmp)
        self.assertEqual(result['media_path'], 'clip.mp4')
        self.assertIsNone(result['thumbnail'])


if __name__ == '__main__':
    unittest.main()
