import json
import logging
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import httplib2
from googleapiclient.errors import HttpError

import uploader
from metadata import build_metadata, clean_filename


def http_error(status, reason):
    content = json.dumps({'error': {'errors': [{'reason': reason}]}}).encode()
    return HttpError(httplib2.Response({'status': status}), content)


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

    def test_automatic_title_is_shortened_not_rejected(self):
        result = build_metadata(Path('y' * 120 + '.mp4'), 'CS2', uploader.DEFAULT_CONFIG, {})
        self.assertEqual(len(result['title']), 100)

    def test_description_with_other_braces_is_safe(self):
        cfg = {**uploader.DEFAULT_CONFIG, 'description': '{game} clip {not a placeholder} <3'}
        result = build_metadata(Path('clip.mp4'), 'CS2', cfg, {})
        self.assertEqual(result['description'], 'CS2 clip {not a placeholder} 3')


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.watch = self.base / 'watch'
        self.path = self.watch / 'Apex Legends' / 'clip.mp4'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'video fixture')
        self.cfg = {**uploader.DEFAULT_CONFIG, 'watch_folder': str(self.watch),
                    'uploaded_folder': str(self.base / 'uploaded'), 'auto_prepare': {'enabled': False}}
        self.records, self.playlists = {}, {}
        for name, value in [('RECORDS_FILE', self.base / 'records.json'),
                            ('LEGACY_HASH_FILE', self.base / 'hashes.json'),
                            ('LOCK_FILE', self.base / 'lock'),
                            ('PREPARED_DIR', self.base / 'prepared')]:
            self.patch(name, value)
        self.patch('is_ready', Mock(return_value=True))
        self.patch('load_config', Mock(return_value=self.cfg))
        self.patch('time', Mock(wraps=time, sleep=Mock()))
        self.patch('logging', Mock(wraps=logging, exception=Mock(), warning=Mock()))

    def patch(self, name, value):
        mock = patch.object(uploader, name, value)
        mock.start()
        self.addCleanup(mock.stop)

    def process(self, records=None):
        return uploader.process_clip(Mock(), self.path, self.watch, self.cfg,
                                     self.records if records is None else records, self.playlists)

    def test_playlist_failure_retries_without_reupload(self):
        with patch.object(uploader, 'upload_video', return_value='video123') as upload, \
             patch.object(uploader, 'playlist_for', side_effect=[RuntimeError('offline'), 'playlist']), \
             patch.object(uploader, 'add_to_playlist'):
            with self.assertRaises(RuntimeError):
                self.process()
            self.assertTrue(self.path.exists())
            # Simulate a restart from the persisted record.
            records = json.loads(uploader.RECORDS_FILE.read_text(encoding='utf-8'))
            self.assertEqual(self.process(records)[0], 'uploaded')
            self.assertEqual(upload.call_count, 1)
            self.assertFalse(self.path.exists())
            self.assertTrue(next(iter(records.values()))['complete'])

    def test_move_failure_cannot_cause_another_upload(self):
        with patch.object(uploader, 'upload_video', return_value='video123') as upload, \
             patch.object(uploader, 'playlist_for', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'), \
             patch.object(uploader, 'archive', side_effect=[OSError('locked file'), None]):
            with self.assertRaises(OSError):
                self.process()
            self.assertEqual(self.process()[0], 'already uploaded')
            self.assertEqual(upload.call_count, 1)

    def test_original_is_archived_by_game(self):
        with patch.object(uploader, 'upload_video', return_value='video123'), \
             patch.object(uploader, 'playlist_for', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'):
            self.process()
        self.assertEqual((self.base / 'uploaded' / 'Apex Legends' / 'clip.mp4').read_bytes(), b'video fixture')

    def test_prepared_media_uploaded_but_original_archived(self):
        prepared = self.base / 'prepared.mp4'
        prepared.write_bytes(b'prepared fixture')
        with patch('autotrim.prepare', return_value={'media_path': str(prepared), 'reason': 'trimmed'}), \
             patch.object(uploader, 'upload_video', return_value='video123') as upload, \
             patch.object(uploader, 'playlist_for', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'):
            self.assertEqual(self.process()[0], 'uploaded')
        self.assertEqual(upload.call_args.args[1], str(prepared))
        self.assertEqual((self.base / 'uploaded' / 'Apex Legends' / 'clip.mp4').read_bytes(), b'video fixture')

    def test_corrupt_history_fails_closed(self):
        uploader.RECORDS_FILE.write_text('{corrupt', encoding='utf-8')
        with self.assertRaises(uploader.SetupError):
            uploader.load_records()
        uploader.RECORDS_FILE.unlink()
        uploader.LEGACY_HASH_FILE.write_text('["short"]', encoding='utf-8')
        with self.assertRaises(uploader.SetupError):
            uploader.load_records()

    def test_legacy_hash_history_counts_as_uploaded(self):
        uploader.LEGACY_HASH_FILE.write_text(json.dumps([uploader.file_hash(self.path)]), encoding='utf-8')
        with patch.object(uploader, 'upload_video') as upload:
            self.assertEqual(self.process(uploader.load_records())[0], 'already uploaded')
            upload.assert_not_called()

    def test_unfinished_record_resumes_without_upload(self):
        self.records[uploader.file_hash(self.path)] = {'video_id': 'video123', 'title': 'Title', 'complete': False}
        with patch.object(uploader, 'upload_video') as upload, \
             patch.object(uploader, 'playlist_for', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist') as add:
            self.process()
            upload.assert_not_called()
            add.assert_called_once()

    def test_daily_limit_stops_the_run(self):
        second = self.watch / 'CS2' / 'other.mp4'
        second.parent.mkdir()
        second.write_bytes(b'second fixture')
        with patch.object(uploader, 'connect'), patch.object(uploader, 'verify_channel'), \
             patch.object(uploader, 'upload_video', side_effect=uploader.StopRun('limit')) as upload:
            summary = uploader.run_once()
        self.assertEqual(upload.call_count, 1)
        self.assertEqual(summary['stopped'], 'limit')
        self.assertTrue(self.path.exists() and second.exists())

    def test_quota_error_becomes_stop_run(self):
        with self.assertRaises(uploader.StopRun):
            uploader.stop_if_limited(http_error(403, 'quotaExceeded'))
        uploader.stop_if_limited(http_error(403, 'forbidden'))  # not a limit: no exception

    def test_one_failure_does_not_stop_other_clips(self):
        second = self.watch / 'CS2' / 'other.mp4'
        second.parent.mkdir()
        second.write_bytes(b'second fixture')
        os.utime(second, (time.time() + 10, time.time() + 10))  # processed after the first
        with patch.object(uploader, 'connect'), patch.object(uploader, 'verify_channel'), \
             patch.object(uploader, 'upload_video', side_effect=[RuntimeError('rejected'), 'video2']), \
             patch.object(uploader, 'playlist_for', return_value='playlist'), \
             patch.object(uploader, 'add_to_playlist'):
            summary = uploader.run_once()
        self.assertEqual(len(summary['failed']), 1)
        self.assertEqual(len(summary['uploaded']), 1)
        self.assertTrue(self.path.exists())

    def test_empty_run_does_not_sign_in(self):
        self.path.unlink()
        with patch.object(uploader, 'connect') as connect:
            self.assertEqual(uploader.run_once()['found'], 0)
            connect.assert_not_called()

    def test_finds_clips_but_skips_caches_and_archive(self):
        for folder in ('replay_cache', 'Apex Legends/thumb_cache', 'YouTubeUploader/Uploaded/CS2'):
            (self.watch / folder).mkdir(parents=True, exist_ok=True)
            (self.watch / folder / 'skip.mp4').write_bytes(b'x')
        (self.watch / 'root clip.mp4').write_bytes(b'x')
        found = uploader.find_clips({**self.cfg, 'uploaded_folder': ''})
        self.assertEqual(sorted(p.name for p in found), ['clip.mp4', 'root clip.mp4'])
        self.assertEqual(uploader.game_name(self.watch / 'root clip.mp4', self.watch), 'Gameplay')


class RuntimeTests(unittest.TestCase):
    def test_wrong_channel_is_rejected(self):
        youtube = Mock()
        youtube.channels().list().execute.return_value = {'items': [{'id': 'wrong', 'snippet': {'title': 'Other'}}]}
        with self.assertRaises(uploader.SetupError):
            uploader.verify_channel(youtube, {'expected_channel_id': 'mine'})

    def test_recent_file_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'growing.mp4'
            p.write_bytes(b'x')
            self.assertFalse(uploader.is_ready(p, settle_seconds=30))

    def test_one_process_lock(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(uploader, 'LOCK_FILE', Path(tmp) / 'lock'):
            with uploader.uploader_lock():
                with self.assertRaises(uploader.AlreadyRunning):
                    with uploader.uploader_lock():
                        pass

    def test_web_client_key_is_rejected_with_a_clear_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            key = Path(tmp) / 'client_secret.json'
            key.write_text(json.dumps({'web': {'client_id': 'x'}}), encoding='utf-8')
            with patch.object(uploader, 'CLIENT_SECRET_FILE', Path(tmp) / 'installed.json'):
                with self.assertRaisesRegex(uploader.SetupError, 'Desktop app'):
                    uploader.install_client_secret(key)

    def test_pasted_keys_make_a_desktop_client_file(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(uploader, 'CLIENT_SECRET_FILE', Path(tmp) / 'client_secret.json'), \
             patch.object(uploader, 'TOKEN_FILE', Path(tmp) / 'token.json'):
            with self.assertRaises(uploader.SetupError):
                uploader.install_client_keys('GOCSPX-secret', '123.apps.googleusercontent.com')  # swapped
            uploader.install_client_keys(' 123-abc.apps.googleusercontent.com ', 'GOCSPX-secret\n')
            saved = json.loads(uploader.CLIENT_SECRET_FILE.read_text(encoding='utf-8'))['installed']
            self.assertEqual((saved['client_id'], saved['client_secret']),
                             ('123-abc.apps.googleusercontent.com', 'GOCSPX-secret'))
            from google_auth_oauthlib.flow import InstalledAppFlow
            InstalledAppFlow.from_client_secrets_file(str(uploader.CLIENT_SECRET_FILE), uploader.SCOPES)

    def test_partial_config_keeps_trim_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'auto_prepare': {'enabled': False}, 'stable_checks': 3}), encoding='utf-8')
            with patch.object(uploader, 'CONFIG_FILE', config):
                cfg = uploader.load_config()
                self.assertEqual(cfg['auto_prepare']['context_after'], 5)
                uploader.save_config(cfg)
                self.assertNotIn('stable_checks', json.loads(config.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
