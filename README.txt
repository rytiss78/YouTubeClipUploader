YouTube Clip Uploader v5 - ONE SHOT

BEHAVIOR
--------
This version does NOT stay running.

Each time it starts:
1. Recursively scans M:\Videos\Radeon ReLive
2. Finds all MP4 clips in game folders
3. Waits for each file to be stable
4. Uploads each video to YouTube as PUBLIC
5. Adds the game folder name as a YouTube tag
6. Finds or creates a PUBLIC playlist with the game folder name
7. Adds the video to that playlist
8. Moves successful files into YouTubeUploader\Uploaded\<Game Name>
9. Moves failed files into YouTubeUploader\Failed\<Game Name>
10. Exits

EXAMPLE
-------
M:\Videos\Radeon ReLive\Remnant II\clip.mp4

becomes:

Title:
    Remnant II — clip

Tags:
    Remnant II
    gameplay
    gaming
    clip

Playlist:
    Remnant II

Video:
    Public

Playlist:
    Public

STARTUP
-------
install_startup.bat creates a Windows logon task.

At logon:
- uploader starts silently
- scans once
- uploads any clips present
- exits

It does NOT remain in memory waiting for future clips.

MANUAL RUN
----------
Run first_run.bat for a visible one-shot run.

Run run_silent.vbs for a silent one-shot run.

IMPORTANT
---------
If your current token.json came from an older version that did not
have playlist permission, delete token.json and run first_run.bat
once to authorize again.

Keep client_secret.json and token.json private.
