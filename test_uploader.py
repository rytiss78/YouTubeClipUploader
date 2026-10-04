import json
import logging
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import clip_uploader as uploader
from clip_metadata import build_metadata, clean_filename
from channel_manager import make_plan


def setUpModule():
    # Recovery scenarios intentionally fail; never mix these fixtures with
    # the operational log used to diagnose real uploads.
    global _log_isolation
    _log_isolation = patch.object(logging.getLogger(), 'handlers', [logging.NullHandler()])
    _log_isolation.start()


def tearDownModule():
    _log_isolation.stop()


class MetadataTests(unittest.TestCase):
    def test_replay_cleanup_and_specific_title(self):
        self.assertEqual(clean_filename('Apex Legends_replay_2026.09.27-19.51 (online-video-cutter.com)', 'Apex Legends'),
                         'Gameplay Clip | 27 Sep 2026, 19:51')
        p = Path('Apex Legends_replay_2026.09.27-19.51.mp4')
        result = build_metadata(p, 'Apex Legends', uploader.DEFAULT_CONFIG,
                                {'title': 'Final Ring Fight | Apex Legends', 'tags': ['APEX LEGENDS']})
        self.assertEqual(result['title'], 'Final Ring Fight | Apex Legends')
        self.assertEqual(result['tags'].count('Apex Legends'), 1)

    def test_overlong_editorial_title_is_rejected(self):
        with self.assertRaises(ValueError):
            build_metadata(Path('clip.mp4'), 'CS2', uploader.DEFAULT_CONFIG, {'title': 'x' * 101})

    def test_plan_preserves_custom_titles_and_nonpublic_videos(self):
        a = {'channels': [{'id': 'channel', 'brandingSettings': {}}], 'playlists': [], 'videos': [
            {'id': 'a', 'snippet': {'title': 'My Custom Title', 'description': 'Automatically uploaded gameplay clip.'}, 'status': {'privacyStatus': 'public'}},
            {'id': 'b', 'snippet': {'title': 'CS2 — CS2 replay 2026.09.27-19.51', 'description': 'Automatically uploaded gameplay clip.'}, 'status': {'privacyStatus': 'unlisted'}}]}
        self.assertEqual(make_plan(a)['videos'], [])


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.watch = self.base / 'watch'
        self.path = self.watch / 'Apex Legends' / 'clip.mp4'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'video fixture')
        self.cfg = {**uploader.DEFAULT_CONFIG, 'uploaded_folder': str(self.base / 'uploaded'), 'auto_prepare': {'enabled': False}}
        self.records, self.hashes = {}, set()
        for name, value in [('RECORDS_FILE', self.base / 'records.json'), ('HASH_DB_FILE', self.base / 'hashes.json')]:
            mock = patch.object(uploader, name, value)
            mock.start()
            self.addCleanup(mock.stop)
        for name, value in [('wait_until_stable', True), ('notify', None)]:
            mock = patch.object(uploader, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def test_playlist_failure_retries_without_reupload(self):
        with patch.object(uploader, 'upload_video', return_value=('video123', 'Title')) as upload, \
             patch.object(uploader, 'get_or_create_playlist', side_effect=[RuntimeError('offline'), 'playlist']), \
             patch.object(uploader, 'add_to_playlist'):
            self.assertFalse(uploader.process_file(Mock(), self.path, self.watch, self.cfg, self.hashes, self.records))
            self.assertTrue(self.path.exists())
            # Simulate restart from the actual persisted record.
            records = json.loads(uploader.RECORDS_FILE.read_text(encoding='utf-8'))
            self.assertTrue(uploader.process_file(Mock(), self.path, self.watch, self.cfg, self.hashes, records))
            self.assertEqual(upload.call_count, 1)
            self.assertFalse(self.path.exists())
            self.assertTrue(next(iter(records.values()))['complete'])

    def test_move_failure_cannot_cause_another_upload(self):
        with patch.object(uploader, 'upload_video', return_value=('video123', 'Title')) as upload, \
             patch.object(uploader, 'get_or_create_playlist', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'), \
             patch.object(uploader, 'safe_move', side_effect=[OSError('locked file'), None]):
            self.assertFalse(uploader.process_file(Mock(), self.path, self.watch, self.cfg, self.hashes, self.records))
            uploader.process_file(Mock(), self.path, self.watch, self.cfg, self.hashes, self.records)
            self.assertEqual(upload.call_count, 1)

    def test_corrupt_hash_history_fails_closed(self):
        uploader.HASH_DB_FILE.write_text('{corrupt', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            uploader._load_hash_db()

    def test_cached_record_without_hash_db_is_still_safe(self):
        import hashlib
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.records[digest] = {'video_id': 'video123', 'title': 'Title', 'complete': False}
        with patch.object(uploader, 'upload_video') as upload, \
             patch.object(uploader, 'get_or_create_playlist', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'):
            uploader.process_file(Mock(), self.path, self.watch, self.cfg, self.hashes, self.records)
            upload.assert_not_called()


class RuntimeTests(unittest.TestCase):
    def test_wrong_channel_is_rejected(self):
        youtube = Mock()
        youtube.channels().list().execute.return_value = {'items': [{'id': 'wrong'}]}
        with self.assertRaises(RuntimeError):
            uploader.verify_channel(youtube, uploader.DEFAULT_CONFIG)

    def test_stability_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'growing.mp4'
            p.write_bytes(b'x')
            with patch.object(uploader.time, 'monotonic', side_effect=[0, 0, 121]), \
                 patch.object(uploader.time, 'sleep'):
                self.assertFalse(uploader.wait_until_stable(p, 3, 5, 120))

    def test_one_process_lock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(uploader, 'BASE', Path(tmp)):
            with uploader.uploader_lock():
                with self.assertRaises(RuntimeError):
                    with uploader.uploader_lock():
                        pass

    def test_empty_run_does_not_authorize(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = {**uploader.DEFAULT_CONFIG, 'watch_folder': tmp,
                   'uploaded_folder': str(Path(tmp) / 'uploaded'), 'failed_folder': str(Path(tmp) / 'failed')}
            with patch.object(uploader, 'load_config', return_value=cfg), \
                 patch.object(uploader, '_load_hash_db', return_value=set()), \
                 patch.object(uploader, 'RECORDS_FILE', Path(tmp) / 'records.json'), \
                 patch.object(uploader, 'youtube_service') as service:
                self.assertEqual(uploader.run_once(Mock(dry_run=False, non_interactive=True)), 0)
                service.assert_not_called()


if __name__ == '__main__':
    unittest.main()
