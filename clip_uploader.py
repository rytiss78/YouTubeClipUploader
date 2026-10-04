import json
import argparse
import hashlib
import os
import logging
import logging.handlers
import shutil
import time
from pathlib import Path
from contextlib import contextmanager
from clip_metadata import build_metadata, load_notes

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

try:
    from winotify import Notification
except ImportError:
    Notification = None

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
TOKEN_FILE = BASE / "token.json"
CLIENT_SECRET_FILE = BASE / "client_secret.json"
LOG_FILE = BASE / "uploader.log"
HASH_DB_FILE = BASE / ".uploaded_hashes.json"
RECORDS_FILE = BASE / ".upload_records.json"

# Playlist creation/editing requires the broader YouTube scope.
SCOPES = ["https://www.googleapis.com/auth/youtube"]

DEFAULT_CONFIG = {
    "watch_folder": r"M:\Videos\Radeon ReLive",
    "uploaded_folder": r"M:\Videos\Radeon ReLive\YouTubeUploader\Uploaded",
    "failed_folder": r"M:\Videos\Radeon ReLive\YouTubeUploader\Failed",
    "privacy": "public",
    "playlist_privacy": "public",
    "category_id": "20",
    "title_format": "{game} | {filename}",
    "description": "{game} gameplay from GrybuMedziotojas.\n\nBrowse the channel's game playlists for more clips.\nhttps://www.youtube.com/@grybumedziotojas",
    "tags": ["gameplay", "gaming", "clip"],
    "stable_checks": 3,
    "stable_seconds": 5,
    "stable_timeout": 120,
    "expected_channel_id": "UCmUiCtHLZgj8R7xagvqGMxQ",
    "auto_prepare": {"enabled": True, "quiet_motion": 3.0, "quiet_audio_db": -35,
                     "context_before": 3, "context_after": 5, "min_edge_trim": 4,
                     "min_clip_seconds": 15, "max_analysis_seconds": 600}
}

# Rotating log: 5 MB per file, keep 3 backups (~20 MB total)
_log_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,
    backupCount=3,
    encoding="utf-8",
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    handlers=[_log_handler],
)


def notify(title, message):
    if Notification is None:
        logging.warning("Desktop notification skipped because winotify is unavailable.")
        return

    try:
        Notification(
            app_id="YouTube Clip Uploader",
            title=title,
            msg=message
        ).show()
    except Exception:
        logging.exception("Desktop notification failed.")


def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
        return DEFAULT_CONFIG.copy()

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)
    return merged


def get_credentials(interactive=True):
    creds = None

    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception:
            logging.exception("Could not read token.json.")
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            return creds
        except Exception:
            logging.exception("Token refresh failed; reauthorization required.")
            creds = None

    if not CLIENT_SECRET_FILE.exists():
        raise RuntimeError("client_secret.json is missing.")

    if not interactive:
        raise RuntimeError('YouTube authorization needs renewal. Run first_run.bat to sign in.')

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE), SCOPES
    )
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def youtube_service(interactive=True):
    return build("youtube", "v3", credentials=get_credentials(interactive), cache_discovery=False)


def verify_channel(youtube, cfg):
    items = youtube.channels().list(part='id,snippet', mine=True).execute().get('items', [])
    if len(items) != 1 or items[0]['id'] != cfg['expected_channel_id']:
        raise RuntimeError('Authorized channel does not match the configured channel. Upload aborted.')
    return items[0]


def atomic_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def load_records():
    if not RECORDS_FILE.exists():
        return {}
    records = json.loads(RECORDS_FILE.read_text(encoding='utf-8'))
    if not isinstance(records, dict) or any(
        not isinstance(record, dict) or not isinstance(record.get('video_id'), str)
        or not isinstance(record.get('title'), str)
        or not isinstance(record.get('complete'), bool)
        for record in records.values()
    ):
        raise RuntimeError('Upload records are invalid; repair history before uploading.')
    return records


@contextmanager
def uploader_lock(filename='.uploader.lock'):
    # OS releases the lock after a crash; the small file can stay in place.
    import msvcrt
    with (BASE / filename).open('a+b') as handle:
        if handle.tell() == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('Another uploader is already running.') from None
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def is_inside(path, parent):
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def find_mp4s(watch, excluded_dirs):
    for p in watch.rglob("*.mp4"):
        if not p.is_file():
            continue

        # Never touch AMD ReLive temporary/cache files.
        try:
            rel = p.relative_to(watch)
            if any(part.casefold() == "replay_cache" for part in rel.parts):
                continue
        except ValueError:
            continue

        if any(is_inside(p, d) for d in excluded_dirs):
            continue

        yield p


def wait_until_stable(path, checks, interval, timeout=120):
    if checks < 1 or interval <= 0 or timeout <= checks * interval:
        raise ValueError('Invalid stability check settings')
    deadline = time.monotonic() + timeout
    last_size = -1
    stable = 0

    while stable < checks:
        if time.monotonic() >= deadline:
            return False
        try:
            stat = path.stat()
            size = (stat.st_size, stat.st_mtime_ns)
        except FileNotFoundError:
            return False

        if stat.st_size > 0 and size == last_size:
            stable += 1
        else:
            stable = 0
            last_size = size

        time.sleep(interval)

    return True


def game_name(path, watch):
    rel = path.resolve().relative_to(watch.resolve())

    if len(rel.parts) >= 2:
        return rel.parts[0]

    return path.parent.name


def make_title(path, watch, cfg):
    return build_metadata(path, game_name(path, watch), cfg)['title']


def make_tags(path, watch, cfg):
    game = game_name(path, watch)

    tags = [game]
    tags.extend(str(x).strip() for x in cfg.get("tags", []))

    result = []
    for tag in tags:
        if tag and tag not in result:
            result.append(tag)

    return result


def _load_hash_db():
    if HASH_DB_FILE.exists():
        try:
            with HASH_DB_FILE.open("r", encoding="utf-8") as f:
                hashes = json.load(f)
                if not isinstance(hashes, list) or any(not isinstance(h, str) or len(h) != 64 for h in hashes):
                    raise ValueError('Invalid upload hash history')
                return set(hashes)
        except Exception:
            raise RuntimeError('Cannot read upload history; repair it before uploading.')
    return set()


def _save_hash_db(db):
    atomic_json(HASH_DB_FILE, sorted(db))


def find_playlist(youtube, name):
    page_token = None

    while True:
        response = youtube.playlists().list(
            part="snippet",
            mine=True,
            maxResults=50,
            pageToken=page_token
        ).execute()

        for item in response.get("items", []):
            current_name = item["snippet"]["title"].strip()
            if current_name.casefold() == name.strip().casefold():
                return item["id"]

        page_token = response.get("nextPageToken")

        if not page_token:
            return None


def get_or_create_playlist(youtube, name, cfg):
    existing = find_playlist(youtube, name)

    if existing:
        return existing

    response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": name,
                "description": f"Automatically uploaded gameplay clips from {name}."
            },
            "status": {
                "privacyStatus": cfg.get("playlist_privacy", "public")
            }
        }
    ).execute()

    playlist_id = response["id"]
    logging.info("Created playlist: %s | %s", name, playlist_id)
    return playlist_id


def add_to_playlist(youtube, playlist_id, video_id):
    if youtube.playlistItems().list(part='id', playlistId=playlist_id,
                                   videoId=video_id, maxResults=1).execute().get('items'):
        return
    youtube.playlistItems().insert(
        part="snippet",
        body={
            "snippet": {
                "playlistId": playlist_id,
                "resourceId": {
                    "kind": "youtube#video",
                    "videoId": video_id
                }
            }
        }
    ).execute()


def upload_video(youtube, path, watch, cfg, media_path=None):
    snippet = build_metadata(path, game_name(path, watch), cfg)
    title = snippet['title']

    body = {
        "snippet": snippet,
        "status": {
            "privacyStatus": cfg.get("privacy", "public"),
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(media_path or path),
        mimetype="video/mp4",
        chunksize=8 * 1024 * 1024,
        resumable=True
    )

    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media
    )

    response = None
    retries = 0

    while response is None:
        try:
            _, response = request.next_chunk()

        except HttpError as e:
            if e.resp.status in (500, 502, 503, 504):
                retries += 1

                if retries > 10:
                    raise

                delay = min(60, 2 ** retries)
                logging.warning(
                    "Temporary YouTube error %s; retrying in %ss.",
                    e.resp.status,
                    delay
                )
                time.sleep(delay)
            else:
                raise

    return response["id"], title


def safe_move(path, destination_dir):
    destination_dir.mkdir(parents=True, exist_ok=True)

    destination = destination_dir / path.name

    while destination.exists() or destination.with_suffix('.json').exists():
        destination = destination_dir / (
            f"{path.stem}_{time.time_ns()}{path.suffix}"
        )

    shutil.move(str(path), str(destination))
    sidecar = path.with_suffix('.json')
    if sidecar.exists():
        shutil.move(str(sidecar), str(destination.with_suffix('.json')))
    return destination


def process_file(youtube, path, watch, cfg, hash_db, records=None):
    records = {} if records is None else records
    game = game_name(path, watch)

    logging.info("Detected: %s", path)

    if not wait_until_stable(
        path,
        int(cfg["stable_checks"]),
        int(cfg["stable_seconds"]),
        int(cfg.get('stable_timeout', 120))
    ):
        logging.warning("File not ready; leaving for a later run: %s", path)
        return

    # Skip duplicates by file hash
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        file_hash = h.hexdigest()
    except Exception:
        logging.exception("Cannot hash %s; leaving for a later run.", path)
        return False

    record = records.get(file_hash)
    if file_hash in hash_db and (not record or record.get('complete')):
        logging.info("Skipping duplicate (hash match): %s", path)
        safe_move(path, Path(cfg["uploaded_folder"]) / game)
        return True

    try:
        if record:
            video_id, title = record['video_id'], record['title']
            logging.info('Resuming post-upload steps for %s', video_id)
        else:
            # Validate editorial notes before any API mutations.
            notes = load_notes(path)
            build_metadata(path, game, cfg, notes)
            thumbnail = notes.get('thumbnail')
            if thumbnail and not (path.parent / thumbnail).is_file():
                raise ValueError('Thumbnail file does not exist')
            from auto_prepare import prepare
            preparation = prepare(path, file_hash, cfg)
            logging.info('Automatic preparation: %s | %.2f–%.2fs', preparation['reason'],
                         preparation.get('start', 0), preparation.get('end', 0))
            video_id, title = upload_video(youtube, path, watch, cfg, media_path=preparation['media_path'])
            record = {'video_id': video_id, 'title': title, 'source': str(path),
                      'game': game, 'complete': False, 'preparation': preparation}
            records[file_hash] = record
            # Save the video ID before playlists, thumbnails, or local moves.
            atomic_json(RECORDS_FILE, records)

        notes = load_notes(path)
        thumbnail = (str(path.parent / notes['thumbnail']) if notes.get('thumbnail')
                     else record.get('preparation', {}).get('thumbnail'))
        if thumbnail and not record.get('thumbnail_done'):
            try:
                youtube.thumbnails().set(videoId=video_id,
                    media_body=MediaFileUpload(thumbnail)).execute()
            except HttpError as exc:
                reasons = {item.get('reason') for item in json.loads(exc.content.decode('utf-8')).get('error', {}).get('errors', [])}
                if exc.resp.status != 403 or 'forbidden' not in reasons:
                    raise
                # Custom thumbnails can be unavailable for this channel.
                logging.warning('Custom thumbnail unavailable for %s: HTTP 403; retaining YouTube thumbnail.', video_id)
                record['thumbnail_skipped'] = True
            record['thumbnail_done'] = True
            atomic_json(RECORDS_FILE, records)

        playlist_id = get_or_create_playlist(youtube, game, cfg)
        add_to_playlist(youtube, playlist_id, video_id)

        record['playlist_id'] = playlist_id
        record['complete'] = True
        atomic_json(RECORDS_FILE, records)
        hash_db.add(file_hash)
        _save_hash_db(hash_db)

        safe_move(
            path,
            Path(cfg["uploaded_folder"]) / game
        )

        logging.info(
            "Uploaded: %s | game=%s | playlist=%s | video=%s",
            title,
            game,
            playlist_id,
            video_id
        )
        notify("Clip uploaded", title)
        return True

    except Exception:
        logging.exception("Failed: %s", path)
        notify("Clip upload failed", f"{game} — {path.name}")

        # A published clip must never be re-uploaded just because a later step failed.
        # Keep the original and durable record so the next run can finish.
        logging.warning('Leaving source in place for retry: %s', path)
        return False


def main():
    parser = argparse.ArgumentParser(description='Upload gameplay clips once.')
    parser.add_argument('--dry-run', action='store_true', help='Preview metadata without authorizing, uploading, or moving files')
    parser.add_argument('--non-interactive', action='store_true', help='Fail promptly if sign-in is required')
    args = parser.parse_args()
    with uploader_lock():
        return run_once(args)


def run_once(args):
    cfg = load_config()

    watch = Path(cfg["watch_folder"]).resolve()
    uploaded = Path(cfg["uploaded_folder"]).resolve()
    failed = Path(cfg["failed_folder"]).resolve()

    if not watch.is_dir():
        raise RuntimeError(f'Watch folder unavailable: {watch}')

    replay_cache = watch / "replay_cache"

    excluded = [uploaded, failed, replay_cache]

    hash_db = _load_hash_db()
    records = load_records()

    logging.info("One-shot uploader started.")

    clips = sorted(
        find_mp4s(watch, excluded),
        key=lambda p: p.stat().st_mtime
    )

    logging.info("Found %d clip(s).", len(clips))

    if args.dry_run:
        for path in clips:
            print(json.dumps({'path': str(path), 'snippet': build_metadata(path, game_name(path, watch), cfg)}, ensure_ascii=True))
        print(f'{len(clips)} clip(s). No uploads or moves performed.')
        return 0
    if not clips:
        return 0
    youtube = youtube_service(interactive=not args.non_interactive)
    verify_channel(youtube, cfg)

    if clips:
        noun = "clip" if len(clips) == 1 else "clips"
        notify("Clips found", f"Found {len(clips)} {noun} ready to upload.")

    failures = 0
    for path in clips:
        if process_file(youtube, path, watch, cfg, hash_db, records) is False:
            failures += 1

    _save_hash_db(hash_db)

    logging.info("One-shot uploader finished. Exiting.")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception('Uploader stopped.')
        notify('Uploader stopped', str(exc))
        raise SystemExit(1)
