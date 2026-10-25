"""Find finished gameplay recordings, upload them to YouTube, add each to a
playlist named after its game, then move the original into the archive folder.

Safety rules kept from the original tool:
- The video ID is saved the moment an upload finishes, so a later failure
  (playlist, thumbnail, moving the file) is resumed next run, never re-uploaded.
- Uploads only go to the channel that was chosen at sign-in.
- Only one upload run can happen at a time.
- Damaged upload history stops uploads instead of risking duplicates.
"""
import hashlib
import json
import logging
import logging.handlers
import os
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import autotrim
from metadata import build_metadata, load_notes

APP_NAME = 'YouTube Clip Uploader'
SCOPES = ['https://www.googleapis.com/auth/youtube']  # playlists need more than upload-only
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def _data_dir():
    """Settings, sign-in and history live beside the .exe (or the project folder)."""
    if getattr(sys, 'frozen', False):
        folder = Path(sys.executable).resolve().parent
    else:
        folder = Path(__file__).resolve().parent.parent
    try:
        probe = folder / '.write_test'
        probe.write_bytes(b'')
        probe.unlink()
        return folder
    except OSError:
        # For example inside Program Files: keep data in the user's profile instead.
        fallback = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'YouTubeClipUploader'
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


BASE = _data_dir()
CONFIG_FILE = BASE / 'config.json'
CLIENT_SECRET_FILE = BASE / 'client_secret.json'
TOKEN_FILE = BASE / 'token.json'
RECORDS_FILE = BASE / '.upload_records.json'
LEGACY_HASH_FILE = BASE / '.uploaded_hashes.json'
LOG_FILE = BASE / 'uploader.log'
LOCK_FILE = BASE / '.uploader.lock'
PREPARED_DIR = BASE / 'prepared'
STARTUP_SHORTCUT = (Path(os.environ.get('APPDATA', Path.home())) / 'Microsoft' / 'Windows' /
                    'Start Menu' / 'Programs' / 'Startup' / f'{APP_NAME}.lnk')

DEFAULT_CONFIG = {
    'watch_folder': '',
    'uploaded_folder': '',           # empty: <watch_folder>\YouTubeUploader\Uploaded
    'privacy': 'unlisted',
    'playlist_privacy': '',          # empty: same as privacy
    'category_id': '20',             # Gaming
    'title_format': '{game} | {filename}',
    'description': '{game} gameplay.',
    'tags': ['gameplay', 'gaming', 'clip'],
    'expected_channel_id': '',
    'channel_title': '',
    'settle_seconds': 30,            # a recording must be unchanged this long before upload
    'auto_prepare': {'enabled': True, 'quiet_motion': 3.0, 'quiet_audio_db': -35,
                     'context_before': 3, 'context_after': 5, 'min_edge_trim': 4,
                     'min_clip_seconds': 15, 'max_analysis_seconds': 600},
}
OBSOLETE_KEYS = ('stable_checks', 'stable_seconds', 'stable_timeout', 'failed_folder')
SKIP_DIRS = {'replay_cache', 'thumb_cache', 'youtubeuploader'}
RETRYABLE_STATUS = {500, 502, 503, 504}
LIMIT_REASONS = {'quotaExceeded', 'uploadLimitExceeded', 'dailyLimitExceeded'}


class SetupError(RuntimeError):
    """Something the user must fix in the app before uploads can run."""


class SignInRequired(SetupError):
    pass


class AlreadyRunning(RuntimeError):
    pass


class StopRun(RuntimeError):
    """YouTube refuses more uploads for now; remaining clips wait for the next run."""


def setup_logging():
    handler = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024,
                                                   backupCount=3, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s | %(levelname)s | %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    logging.getLogger('googleapiclient').setLevel(logging.WARNING)
    # Its local sign-in server would otherwise log the redirect URL with the one-time code.
    logging.getLogger('google_auth_oauthlib').setLevel(logging.WARNING)


def notify(title, message):
    try:
        from winotify import Notification
        Notification(app_id=APP_NAME, title=title, msg=message[:250]).show()
    except Exception:
        logging.warning('Could not show a desktop notification.', exc_info=True)


# ---------------------------------------------------------------- settings

def load_config():
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if CONFIG_FILE.exists():
        try:
            saved = json.loads(CONFIG_FILE.read_text(encoding='utf-8-sig'))
        except ValueError as exc:
            raise SetupError(f'config.json has a mistake ({exc}). Fix it or delete it to start over.') from None
        if not isinstance(saved, dict):
            raise SetupError('config.json is not valid. Fix it or delete it to start over.')
        for key, value in saved.items():
            if isinstance(cfg.get(key), dict) and isinstance(value, dict):
                cfg[key].update(value)
            else:
                cfg[key] = value
    return cfg


def save_config(cfg):
    atomic_json(CONFIG_FILE, {k: v for k, v in cfg.items() if k not in OBSOLETE_KEYS})


def update_config(**changes):
    cfg = load_config()
    cfg.update(changes)
    save_config(cfg)
    return cfg


def archive_folder(cfg):
    if cfg.get('uploaded_folder'):
        return Path(cfg['uploaded_folder'])
    return Path(cfg['watch_folder']) / 'YouTubeUploader' / 'Uploaded'


def guess_recordings_folder():
    """Default save folders of common recorders (AMD ReLive, NVIDIA, Xbox Game Bar)."""
    videos = Path.home() / 'Videos'
    for name in ('Radeon ReLive', 'NVIDIA', 'Captures'):
        if (videos / name).is_dir():
            return str(videos / name)
    return ''


def setup_problems(cfg):
    """Plain-language list of what still needs doing before uploads can run."""
    problems = []
    if not CLIENT_SECRET_FILE.is_file():
        problems.append('Step 1: add your Google API key.')
    if not TOKEN_FILE.is_file():
        problems.append('Step 2: sign in to YouTube.')
    if not cfg.get('watch_folder'):
        problems.append('Step 3: choose the folder where your recordings are saved.')
    elif not Path(cfg['watch_folder']).is_dir():
        problems.append(f"Recordings folder not found: {cfg['watch_folder']}")
    return problems


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temporary, path)


# ---------------------------------------------------------------- Google sign-in

def install_client_secret(source):
    """Use the OAuth client file downloaded from Google Cloud."""
    try:
        data = json.loads(Path(source).read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        raise SetupError('That file could not be read as a Google client_secret JSON file.') from None
    save_client(data)


def install_client_keys(client_id, client_secret):
    """Use a Desktop client's ID and secret, copied from its page in Google Cloud."""
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id.endswith('.apps.googleusercontent.com') or ' ' in client_id:
        raise SetupError('That is not a Client ID. It ends with ".apps.googleusercontent.com" '
                         'and is shown on the client\'s page in Google Cloud.')
    if not client_secret or ' ' in client_secret or client_secret == client_id:
        raise SetupError('That is not a Client secret. It usually starts with "GOCSPX-".')
    save_client({'installed': {
        'client_id': client_id, 'client_secret': client_secret,
        'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
        'token_uri': 'https://oauth2.googleapis.com/token',
        'auth_provider_x509_cert_url': 'https://www.googleapis.com/oauth2/v1/certs',
        'redirect_uris': ['http://localhost']}})


def save_client(data):
    if not isinstance(data, dict) or 'installed' not in data:
        if isinstance(data, dict) and 'web' in data:
            raise SetupError('This key is for a "Web application". In Google Cloud, create an OAuth '
                             'client of type "Desktop app" and download that one instead.')
        raise SetupError('This is not a Google OAuth client file. It should be named like client_secret_....json.')
    new_client = data['installed'].get('client_id')
    if TOKEN_FILE.exists():
        try:
            if json.loads(TOKEN_FILE.read_text(encoding='utf-8')).get('client_id') != new_client:
                TOKEN_FILE.unlink()  # the old sign-in belongs to the old key
        except ValueError:
            TOKEN_FILE.unlink()
    atomic_json(CLIENT_SECRET_FILE, data)


def load_credentials():
    """Saved sign-in, refreshed when needed. None means the user must sign in again."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    if not TOKEN_FILE.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    except (ValueError, KeyError):
        logging.warning('Saved sign-in (token.json) could not be read.')
        return None
    if creds.valid:
        return creds
    if not creds.refresh_token:
        return None
    try:
        creds.refresh(Request())
    except RefreshError:
        logging.warning('Google no longer accepts the saved sign-in.', exc_info=True)
        return None
    # Other errors (offline) propagate: that means "try later", not "sign in again".
    TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')
    return creds


def youtube_client(creds):
    from googleapiclient.discovery import build
    return build('youtube', 'v3', credentials=creds, cache_discovery=False)


def sign_in(timeout=300):
    """Open the browser for Google sign-in. Returns (credentials, channel) without saving."""
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CLIENT_SECRET_FILE.is_file():
        raise SetupError('Add your Google client_secret.json file first (step 1).')
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    try:
        creds = flow.run_local_server(
            port=0, timeout_seconds=timeout, authorization_prompt_message='',
            success_message='Signed in. You can close this tab and return to YouTube Clip Uploader.',
            prompt='consent')  # always returns a refresh token for unattended uploads
    except AttributeError:
        # The local server timed out without Google redirecting back.
        raise SetupError('Sign-in was not finished in time. Click "Sign in" to try again.') from None
    return creds, current_channel(youtube_client(creds))


def save_sign_in(creds):
    TOKEN_FILE.write_text(creds.to_json(), encoding='utf-8')


def current_channel(youtube):
    items = youtube.channels().list(part='id,snippet', mine=True).execute().get('items', [])
    if not items:
        raise SetupError('This Google account has no YouTube channel. Sign in with the account '
                         '(or brand account) that owns your channel.')
    return {'id': items[0]['id'], 'title': items[0]['snippet']['title']}


def verify_channel(youtube, cfg):
    channel = current_channel(youtube)
    expected = cfg.get('expected_channel_id')
    if expected and channel['id'] != expected:
        raise SetupError(f'Signed in to "{channel["title"]}", which is not the channel uploads are set for. '
                         'Open the app and click "Sign in" to choose the channel.')
    return channel


def connect():
    creds = load_credentials()
    if creds is None:
        raise SignInRequired('Not signed in to YouTube, or the sign-in expired. '
                             'Open YouTube Clip Uploader and click "Sign in".')
    return youtube_client(creds)


def wait_until(condition, timeout, interval=5):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)
    return True


def internet_available():
    try:
        socket.create_connection(('www.googleapis.com', 443), timeout=5).close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- finding clips

def is_inside(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def find_clips(cfg):
    """Every .mp4 in the recordings folder that is not archived or a recorder cache, oldest first."""
    watch = Path(cfg['watch_folder'])
    excluded = [archive_folder(cfg), PREPARED_DIR]
    found = []
    for path in watch.rglob('*.mp4'):
        if any(part.casefold() in SKIP_DIRS for part in path.relative_to(watch).parts[:-1]):
            continue
        if any(is_inside(path, folder) for folder in excluded):
            continue
        try:
            if path.is_file():
                found.append((path.stat().st_mtime, path))
        except OSError:
            continue
    return [path for _, path in sorted(found)]


def is_ready(path, settle_seconds=30):
    """A recording is ready once it has stopped changing."""
    try:
        before = path.stat()
        if before.st_size == 0 or time.time() - before.st_mtime < settle_seconds:
            return False
        time.sleep(2)
        after = path.stat()
    except OSError:
        return False
    return (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns)


def game_name(path, watch):
    """Recorders such as AMD ReLive save each game in its own folder."""
    parts = path.relative_to(watch).parts
    return parts[0] if len(parts) >= 2 else 'Gameplay'


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------- upload history

def load_records():
    """Upload history keyed by file hash. Damaged history stops uploads (fail closed)."""
    damaged = SetupError(f'Upload history is damaged ({RECORDS_FILE.name}). Restore it from a backup '
                         'before uploading, so clips are not uploaded twice.')
    records = {}
    if RECORDS_FILE.exists():
        try:
            records = json.loads(RECORDS_FILE.read_text(encoding='utf-8'))
        except ValueError:
            raise damaged from None
        if not isinstance(records, dict) or any(
                not isinstance(r, dict) or not isinstance(r.get('video_id'), str)
                or not isinstance(r.get('complete'), bool) for r in records.values()):
            raise damaged
    if LEGACY_HASH_FILE.exists():
        # Older versions kept a plain list of uploaded file hashes.
        try:
            hashes = json.loads(LEGACY_HASH_FILE.read_text(encoding='utf-8'))
        except ValueError:
            raise damaged from None
        if not isinstance(hashes, list) or any(not isinstance(h, str) or len(h) != 64 for h in hashes):
            raise damaged
        for digest in hashes:
            records.setdefault(digest, {'video_id': '', 'title': '', 'complete': True})
    return records


def save_records(records):
    atomic_json(RECORDS_FILE, records)


@contextmanager
def uploader_lock():
    import msvcrt
    # Windows releases the lock if the process dies; the small file can stay.
    with LOCK_FILE.open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise AlreadyRunning('Another upload is already running.') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


# ---------------------------------------------------------------- YouTube calls

def error_reasons(exc):
    try:
        errors = json.loads(exc.content.decode('utf-8'))['error'].get('errors', [])
        return {e.get('reason') for e in errors}
    except Exception:
        return set()


def stop_if_limited(exc):
    from googleapiclient.errors import HttpError
    if isinstance(exc, HttpError) and error_reasons(exc) & LIMIT_REASONS:
        raise StopRun("YouTube's daily upload limit was reached. "
                      'The remaining clips will upload on the next run.') from exc


def is_retryable(exc):
    import httplib2
    from googleapiclient.errors import HttpError
    if isinstance(exc, HttpError):
        return exc.resp.status in RETRYABLE_STATUS
    return isinstance(exc, (OSError, httplib2.HttpLib2Error))


def upload_video(youtube, media_path, snippet, privacy, on_progress=None):
    from googleapiclient.http import MediaFileUpload
    media = MediaFileUpload(str(media_path), mimetype='video/mp4',
                            chunksize=16 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part='snippet,status', media_body=media, body={
        'snippet': snippet,
        'status': {'privacyStatus': privacy, 'selfDeclaredMadeForKids': False}})
    response, failures = None, 0
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=3)
        except Exception as exc:
            stop_if_limited(exc)
            if not is_retryable(exc) or failures >= 8:
                raise
            failures += 1
            delay = min(60, 2 ** failures)
            logging.warning('Upload interrupted (%s); resuming in %ss.', exc, delay)
            time.sleep(delay)
            continue
        failures = 0
        if status and on_progress:
            on_progress(status.progress())
    return response['id']


def load_playlists(youtube):
    playlists, token = {}, None
    while True:
        response = youtube.playlists().list(part='snippet', mine=True, maxResults=50,
                                            pageToken=token).execute()
        for item in response.get('items', []):
            playlists.setdefault(item['snippet']['title'].strip().casefold(), item['id'])
        token = response.get('nextPageToken')
        if not token:
            return playlists


def playlist_for(youtube, game, cfg, playlists):
    """The playlist named after the game, created if needed. `playlists` caches lookups per run."""
    if not playlists:
        playlists.update(load_playlists(youtube))
    key = game.strip().casefold()
    if key not in playlists:
        response = youtube.playlists().insert(part='snippet,status', body={
            'snippet': {'title': game, 'description': f'Gameplay clips from {game}.'},
            'status': {'privacyStatus': cfg.get('playlist_privacy') or cfg['privacy']}}).execute()
        playlists[key] = response['id']
        logging.info('Created playlist "%s".', game)
    return playlists[key]


def add_to_playlist(youtube, playlist_id, video_id):
    if youtube.playlistItems().list(part='id', playlistId=playlist_id, videoId=video_id,
                                    maxResults=1).execute().get('items'):
        return
    youtube.playlistItems().insert(part='snippet', body={'snippet': {
        'playlistId': playlist_id,
        'resourceId': {'kind': 'youtube#video', 'videoId': video_id}}}).execute()


def set_thumbnail(youtube, video_id, image):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload
    try:
        youtube.thumbnails().set(videoId=video_id, media_body=MediaFileUpload(str(image))).execute()
    except HttpError as exc:
        stop_if_limited(exc)
        # Custom thumbnails need a phone-verified channel; YouTube then picks its own.
        logging.warning('Custom thumbnail not used for %s (HTTP %s); YouTube will choose one.',
                        video_id, exc.resp.status)


# ---------------------------------------------------------------- one clip / one run

def archive(path, cfg, game):
    """Move the original recording (and its notes) out of the recordings folder."""
    folder = archive_folder(cfg) / game
    folder.mkdir(parents=True, exist_ok=True)
    target, n = folder / path.name, 1
    while target.exists() or target.with_suffix('.json').exists():
        target, n = folder / f'{path.stem} ({n}){path.suffix}', n + 1
    shutil.move(str(path), str(target))
    sidecar = path.with_suffix('.json')
    if sidecar.exists():
        shutil.move(str(sidecar), str(target.with_suffix('.json')))
    return target


def process_clip(youtube, path, watch, cfg, records, playlists, on_progress=None):
    """Upload one clip. Returns ('uploaded' | 'already uploaded' | 'not ready', record)."""
    if not is_ready(path, float(cfg.get('settle_seconds', 30))):
        logging.info('Still being written; leaving it for later: %s', path.name)
        return 'not ready', None
    game = game_name(path, watch)
    digest = file_hash(path)
    record = records.get(digest)

    if record and record['complete']:
        logging.info('Already uploaded; archiving: %s', path.name)
        archive(path, cfg, game)
        return 'already uploaded', record

    if record is None:
        # Check the notes before anything touches YouTube.
        notes = load_notes(path)
        snippet = build_metadata(path, game, cfg, notes)
        thumbnail = None
        if notes.get('thumbnail'):
            thumbnail = path.parent / notes['thumbnail']
            if not thumbnail.is_file():
                raise ValueError(f'Thumbnail file not found: {thumbnail}')
        prepared = autotrim.prepare(path, digest, cfg.get('auto_prepare', {}), PREPARED_DIR)
        logging.info('Trimming: %s (%.0f–%.0fs)', prepared['reason'],
                     prepared.get('start', 0), prepared.get('end', 0))
        logging.info('Uploading "%s"…', snippet['title'])
        video_id = upload_video(youtube, prepared['media_path'], snippet, cfg['privacy'], on_progress)
        record = {'video_id': video_id, 'title': snippet['title'], 'source': str(path), 'game': game,
                  'uploaded_at': datetime.now().isoformat(timespec='seconds'), 'complete': False,
                  'thumbnail': str(thumbnail or prepared.get('thumbnail') or '')}
        records[digest] = record
        save_records(records)  # saved before anything else can fail
        logging.info('Uploaded: https://youtu.be/%s', video_id)

    video_id = record['video_id']
    thumbnail = record.get('thumbnail') or record.get('preparation', {}).get('thumbnail')
    if thumbnail and not record.get('thumbnail_done'):
        if Path(thumbnail).is_file():
            set_thumbnail(youtube, video_id, thumbnail)
        record['thumbnail_done'] = True
        save_records(records)
    if not record.get('playlist_id'):
        playlist_id = playlist_for(youtube, game, cfg, playlists)
        add_to_playlist(youtube, playlist_id, video_id)
        record['playlist_id'] = playlist_id
        save_records(records)
    record['complete'] = True
    save_records(records)
    archive(path, cfg, game)
    shutil.rmtree(PREPARED_DIR / digest, ignore_errors=True)
    return 'uploaded', record


def preview():
    """[(clip, metadata or error text)] for every clip that would be uploaded."""
    cfg = load_config()
    if not cfg.get('watch_folder') or not Path(cfg['watch_folder']).is_dir():
        raise SetupError('Choose an existing recordings folder first.')
    watch = Path(cfg['watch_folder'])
    result = []
    for path in find_clips(cfg):
        try:
            result.append((path, build_metadata(path, game_name(path, watch), cfg)))
        except Exception as exc:
            result.append((path, f'Problem: {exc}'))
    return result


def run_once(unattended=False, on_found=None, on_progress=None):
    """Upload every finished clip once and return a summary.

    unattended: started at Windows sign-in, so wait a little for the drive and network.
    """
    with uploader_lock():
        cfg = load_config()
        if not cfg.get('watch_folder'):
            raise SetupError('No recordings folder is set. Open YouTube Clip Uploader to finish setup.')
        watch = Path(cfg['watch_folder'])
        if not (watch.is_dir() or (unattended and wait_until(watch.is_dir, 60))):
            raise SetupError(f'Recordings folder not found: {watch}')
        records = load_records()
        clips = find_clips(cfg)
        logging.info('Found %d clip(s) in %s.', len(clips), watch)
        summary = {'found': len(clips), 'uploaded': [], 'failed': [], 'waiting': [], 'stopped': ''}
        if not clips:
            return summary
        if on_found:
            on_found(len(clips))
        if unattended and not wait_until(internet_available, 180):
            raise RuntimeError('No internet connection. Clips will upload next time.')
        youtube = connect()
        verify_channel(youtube, cfg)
        playlists = {}
        for number, path in enumerate(clips, 1):
            logging.info('[%d/%d] %s', number, len(clips), path.name)
            try:
                status, record = process_clip(youtube, path, watch, cfg, records, playlists, on_progress)
            except StopRun as exc:
                logging.warning('%s', exc)
                summary['stopped'] = str(exc)
                break
            except Exception as exc:
                logging.exception('Failed: %s', path.name)
                summary['failed'].append(f'{path.name}: {exc}')
                continue
            if status == 'uploaded':
                summary['uploaded'].append(record['title'])
            elif status == 'not ready':
                summary['waiting'].append(path.name)
        logging.info('Done: %d uploaded, %d failed, %d still being written.',
                     len(summary['uploaded']), len(summary['failed']), len(summary['waiting']))
        return summary


# ---------------------------------------------------------------- start with Windows

def launch_command():
    """Program and arguments for one silent upload pass."""
    if getattr(sys, 'frozen', False):
        return sys.executable, '--run'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    return str(pythonw if pythonw.exists() else sys.executable), f'"{Path(__file__).with_name("app.py")}" --run'


def autostart_enabled():
    return STARTUP_SHORTCUT.exists()


def set_autostart(enabled):
    """A per-user Startup shortcut; no administrator rights needed."""
    if not enabled:
        STARTUP_SHORTCUT.unlink(missing_ok=True)
        return
    target, arguments = launch_command()

    def quote(text):
        return "'" + str(text).replace("'", "''") + "'"
    script = (f'$s = (New-Object -ComObject WScript.Shell).CreateShortcut({quote(STARTUP_SHORTCUT)}); '
              f'$s.TargetPath = {quote(target)}; $s.Arguments = {quote(arguments)}; '
              f'$s.WorkingDirectory = {quote(BASE)}; $s.WindowStyle = 7; '
              f"$s.Description = 'Upload new gameplay clips once at sign-in, then exit.'; $s.Save()")
    STARTUP_SHORTCUT.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                   check=True, capture_output=True, timeout=60, creationflags=NO_WINDOW)
