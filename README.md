# YouTube Clip Uploader

A Windows tool that prepares and uploads gameplay clips to YouTube. It runs
**once at sign-in and exits**. There is no persistent watcher.

## What it does

- Scans game folders for stable MP4 recordings, excluding archives and replay caches.
- Automatically trims clearly quiet beginnings and endings using motion and audio,
  while retaining context. Short or ambiguous clips keep their full length.
- Selects a thumbnail and generates factual titles, descriptions, and game tags.
- Uploads clips to their game playlists and archives the original recordings intact.
- Saves video IDs before post-upload steps so retries can finish without re-uploading.

Trimming is a conservative local heuristic. It does not recognize kills, wins, or
other game events. Exported trims preserve resolution, frame rate, and audio and
are checked before uploading.

## Setup

Requires Windows and Python 3.10 or newer, with the `py` launcher available.

1. Run `install.bat` to install dependencies.
2. Supply your own Google OAuth desktop-client credentials as `client_secret.json`.
3. Edit `config.json` for your source/archive folders and your YouTube channel ID
   (`expected_channel_id`). The included configuration is for the owner's channel.
4. Run `first_run.bat` to authorize and perform one upload pass.
5. Run `install_startup.bat` to install a user Startup shortcut. Each Windows sign-in
   will run one unattended preparation/upload pass and exit.

Clips recorded after a startup pass are processed at your next sign-in or manual run.
No administrator access or running Codex app is required for the Startup shortcut.
Unattended runs stop with a notification if Google authorization needs renewal.

## Useful commands

```powershell
# Preview metadata without uploads or file moves
py -3 clip_uploader.py --dry-run

# One unattended run
py -3 clip_uploader.py --non-interactive

# Regression checks
py -3 -m unittest -v test_uploader test_auto_prepare
```

Optional companion JSON files allow custom titles, descriptions, tags, and thumbnails.
The manual `prepare_clip.py` utility can make contact sheets or explicitly selected
trims. See [README.txt](README.txt) for those options and recovery details.

## Credentials and local data

This repository does **not** include OAuth credentials, access/refresh tokens,
recordings, upload history, logs, or channel audit backups. Keep those files private.
They are excluded by `.gitignore`. Never commit `client_secret.json`, `token.json`,
or an `.env` file. Each user must provide their own credentials.
