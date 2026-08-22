import json
import logging
import shutil
import time
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "config.json"
TOKEN_FILE = BASE / "token.json"
CLIENT_SECRET_FILE = BASE / "client_secret.json"
LOG_FILE = BASE / "uploader.log"

# Playlist creation/editing requires the broader YouTube scope.
SCOPES = ["https://www.googleapis.com/auth/youtube"]

DEFAULT_CONFIG = {
    "watch_folder": r"M:\Videos\Radeon ReLive",
    "uploaded_folder": r"M:\Videos\Radeon ReLive\YouTubeUploader\Uploaded",
    "failed_folder": r"M:\Videos\Radeon ReLive\YouTubeUploader\Failed",
    "privacy": "public",
    "playlist_privacy": "public",
    "category_id": "20",
    "title_format": "{game} — {filename}",
    "description": "Automatically uploaded gameplay clip.",
    "tags": ["gameplay", "gaming", "clip"],
    "stable_checks": 3,
    "stable_seconds": 5
}

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

def load_config():
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(json.dumps(DEFAULT_CONFIG, indent=4), encoding="utf-8")
        return DEFAULT_CONFIG.copy()

    with CONFIG_FILE.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    merged = DEFAULT_CONFIG.copy()
    merged.update(cfg)
    return merged

def get_credentials():
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

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRET_FILE), SCOPES
    )
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds

def youtube_service():
    return build("youtube", "v3", credentials=get_credentials())

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

def wait_until_stable(path, checks, interval):
    last_size = -1
    stable = 0

    while stable < checks:
        try:
            size = path.stat().st_size
        except FileNotFoundError:
            return False

        if size > 0 and size == last_size:
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
    game = game_name(path, watch)
    filename = path.stem.replace("_", " ").strip()

    return cfg.get("title_format", "{game} — {filename}").format(
        game=game,
        filename=filename
    )[:100]

def make_tags(path, watch, cfg):
    game = game_name(path, watch)

    tags = [game]
    tags.extend(str(x).strip() for x in cfg.get("tags", []))

    result = []
    for tag in tags:
        if tag and tag not in result:
            result.append(tag)

    return result

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

def upload_video(youtube, path, watch, cfg):
    title = make_title(path, watch, cfg)

    body = {
        "snippet": {
            "title": title,
            "description": cfg["description"],
            "tags": make_tags(path, watch, cfg),
            "categoryId": str(cfg["category_id"])
        },
        "status": {
            "privacyStatus": cfg.get("privacy", "public"),
            "selfDeclaredMadeForKids": False
        }
    }

    media = MediaFileUpload(
        str(path),
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

    if destination.exists():
        destination = destination_dir / (
            f"{path.stem}_{int(time.time())}{path.suffix}"
        )

    shutil.move(str(path), str(destination))

def process_file(youtube, path, watch, cfg):
    game = game_name(path, watch)

    logging.info("Detected: %s", path)

    if not wait_until_stable(
        path,
        int(cfg["stable_checks"]),
        int(cfg["stable_seconds"])
    ):
        logging.warning("File disappeared before it became stable: %s", path)
        return

    try:
        video_id, title = upload_video(youtube, path, watch, cfg)

        playlist_id = get_or_create_playlist(youtube, game, cfg)
        add_to_playlist(youtube, playlist_id, video_id)

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

    except Exception:
        logging.exception("Failed: %s", path)

        try:
            safe_move(
                path,
                Path(cfg["failed_folder"]) / game
            )
        except Exception:
            logging.exception("Could not move failed file.")

def main():
    cfg = load_config()

    watch = Path(cfg["watch_folder"]).resolve()
    uploaded = Path(cfg["uploaded_folder"]).resolve()
    failed = Path(cfg["failed_folder"]).resolve()

    watch.mkdir(parents=True, exist_ok=True)
    uploaded.mkdir(parents=True, exist_ok=True)
    failed.mkdir(parents=True, exist_ok=True)

    replay_cache = watch / "replay_cache"

    excluded = [uploaded, failed, replay_cache]

    logging.info("One-shot uploader started.")

    youtube = youtube_service()

    clips = sorted(
        find_mp4s(watch, excluded),
        key=lambda p: p.stat().st_mtime
    )

    logging.info("Found %d clip(s).", len(clips))

    for path in clips:
        process_file(youtube, path, watch, cfg)

    logging.info("One-shot uploader finished. Exiting.")

if __name__ == "__main__":
    main()
