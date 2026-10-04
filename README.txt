YouTube Clip Uploader — gameplay clips and channel maintenance

UPLOADS
Run first_run.bat for one upload pass. The tool recursively scans the
configured watch folder, uploads MP4s, adds each to its game playlist,
automatically prepares new clips, archives originals on success, and exits.
At Windows sign-in, the startup launcher runs one pass and exits.
Nothing stays running between uploads. Automatic trimming remains enabled.
Video and playlist privacy remain public by default.
The configured channel ID is checked before uploading.

Preview without uploading or moving files:
    py -3 clip_uploader.py --dry-run

Run unattended (never opens a sign-in browser):
    py -3 clip_uploader.py --non-interactive

install_startup.bat installs a per-user Windows Startup shortcut with a
resolved Python executable and absolute script path. At each sign-in it
runs one scan, prepares/uploads any clips found, then exits.
run_silent.vbs runs one pass manually. remove_startup.bat removes autorun.
No administrator access or open Codex app is needed.
Clips recorded after that scan are processed at your next sign-in or manual run.

PRESENTATION
Recording filenames lose repeated game names and cutter-site suffixes.
Fallback titles stay factual. For a specific title, put a JSON file beside
the MP4, using the same stem:

clip.mp4
clip.json:
{
    "title": "M4A1-S Through the Smoke on Office | CS2",
    "description": "M4A1-S gameplay on Office in Counter-Strike 2.",
    "tags": ["CS2", "Office", "M4A1-S"],
    "thumbnail": "clip.jpg"
}

Title, description, tags, and thumbnail are optional. The thumbnail path
is relative to the recording's folder. Custom thumbnails require channel
eligibility. Notes are archived with their MP4; thumbnail assets stay in
their source folder.

AUTOMATIC PREPARATION
Enabled in config.json. No start/end times or editorial JSON are needed.
For each new stable recording, auto_prepare.py analyzes per-second motion
and audio. It removes only consecutive quiet, low-motion sections at the
start/end, with 3 seconds before and 5 seconds after the activity. It keeps
all footage between the first and last activity, including quiet gaps.
Short clips, missing audio, and ambiguous selections keep their full length.
This is a local signal heuristic, not a model that understands kills or wins.
Automatic titles remain factual; event-specific titles are not invented.

The tool selects a clear thumbnail, retains resolution/frame rate/audio,
and validates rendered trims by decoding them. Originals are archived intact.
Prepared media and analysis reports are cached under prepared/ (outside
the watched folder) so retries do not keep re-encoding. Preparation errors
leave the source pending instead of uploading an unchecked edit.
Set auto_prepare.enabled to false to disable preparation.
If custom thumbnails are forbidden, keep the YouTube thumbnail and finish.
Quota and temporary API errors are still retried on later passes.

MANUAL PREVIEW AND TRIM (OPTIONAL)
Optional local tools (already installed into .tools on this computer):
    py -3 -m pip install --target .tools imageio-ffmpeg Pillow

Create a 12-frame contact sheet:
    py -3 prepare_clip.py "path\to\clip.mp4" --review "reviews\clip.jpg"

Create a new trim and editorial notes, preserving the original:
    py -3 prepare_clip.py "path\to\clip.mp4" --output "reviews\prepared\clip.mp4" --start 34 --end 53 --title "M4A1-S Through the Smoke on Office | CS2" --thumbnail-at 42

Times refer to the original recording. Export preserves resolution and
frame rate with high-quality H.264/AAC. It never uploads automatically.
Review output before placing a new clip and its JSON/thumbnail inside the
relevant game folder under the configured watch folder.
Do not re-upload a trim of an existing public clip as an accidental duplicate.

RECOVERY
.upload_records.json stores each successful upload's video ID immediately,
before playlist/thumbnail/archive work. Later failures resume those steps
on the next run. Failed clips stay in their source folders for retry.
.uploaded_hashes.json retains legacy duplicate history. State writes are
atomic; overlapping uploader runs are blocked by an OS lock.
Replay cache and archive/failed folders are excluded. Files still growing
after the stability timeout are left for a later run.
Back up both history files together; corrupt history stops uploads.

AUTHORIZATION
Keep client_secret.json, token.json, uploader.log, and local audit/history
files private. If sign-in expires, run first_run.bat to authorize again.
Do not delete upload history when renewing authorization.
Repeated weekly invalid_grant errors may mean the OAuth app is in Testing
status. Check the Google Cloud OAuth consent configuration.
See https://developers.google.com/identity/protocols/oauth2#expiration

CHANNEL MANAGEMENT
    py -3 channel_manager.py audit
    py -3 channel_manager.py plan
    py -3 channel_manager.py apply

Audit reads live data into channel_audit.json. Plan writes a reviewable
channel_changes_plan.json. Apply verifies ownership/current metadata,
saves originals, then writes channel description/video metadata.
Initial cleanup selects recognized automated public uploads with the old
boilerplate description. It preserves other editorial metadata.
Backups and API snapshots stay local and are excluded from Git.

CHECKS
    py -3 -m unittest -v test_uploader test_auto_prepare

See MAINTENANCE.md for priorities and weekly review context.
