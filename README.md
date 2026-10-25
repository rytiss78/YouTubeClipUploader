# YouTube Clip Uploader

Uploads your gameplay recordings to YouTube by itself. Each game folder becomes a
playlist, and each uploaded recording is moved into an archive folder. It runs once
when you sign in to Windows, then closes. Nothing stays running in the background.

## Setup (once, about 10 minutes)

Open **YouTubeClipUploader.exe** and follow the three steps in the window:

1. **Google API key.** Click **How do I get this?** for step-by-step instructions.
   You create a free key in Google Cloud
   ([your keys](https://console.cloud.google.com/auth/clients)). Then either click
   **Paste key…** and paste the Client ID and Client secret, or download the JSON
   file and click **Choose file…**.
2. **YouTube channel.** Click **Sign in…**, pick your account and channel in the
   browser, and allow access. Google warns that the app is not verified. That is
   normal for your own key: click *Advanced*, then *Go to … (unsafe)*.
3. **Settings.** Pick your recordings folder (for example `Videos\Radeon ReLive`)
   and the visibility. Leave **Upload new clips automatically each time I sign in to
   Windows** ticked. Then click **Save settings**.

Click **Upload now** to test it.

## Everyday use

There is nothing to do. Record clips as usual. At the next Windows sign-in, the
uploader waits for the drive and the internet and uploads every finished clip. Then
it shows a notification and closes.

- **Upload right away:** open the app and click **Upload now**.
- **See what would be uploaded:** click **Preview**. Nothing is uploaded.
- **Something went wrong:** a failed clip stays where it is and is retried next
  time. Click **Open full log** for details.

## Good to know

- **Sign-in stops working every 7 days:** your Google project is still in
  "Testing". In Google Cloud, go to *Google Auth Platform → Audience*, click
  **Publish app**, and then sign in again in the app.
- **Daily limit:** a normal Google project can upload about 6 videos a day. Extra
  clips wait for the next run.
- **Videos stuck on Private:** Google can lock uploads from new, unaudited projects
  as private. Request the free YouTube API compliance audit from Google.
- **Custom thumbnails** need a phone-verified YouTube channel. Without one, YouTube
  picks the thumbnail.
- **Automatic trimming** cuts only quiet, still footage at the start and end of a
  clip, and keeps a few seconds of context. Your original recording is never
  changed. It does not detect kills or wins.
- **Your own title for one clip:** put a `.json` file with the same name next to
  the recording:

  ```json
  {
      "title": "M4A1-S Through the Smoke on Office | CS2",
      "description": "M4A1-S gameplay on Office in Counter-Strike 2.",
      "tags": ["CS2", "Office"],
      "thumbnail": "my-thumbnail.jpg"
  }
  ```

  Every field is optional.

## Files in this folder

| File | What it is |
|---|---|
| `YouTubeClipUploader.exe` | The app. Keep it in this folder. |
| `config.json` | Your settings. More options: `title_format`, `tags`, `category_id`, `playlist_privacy`, and the trimming thresholds under `auto_prepare`. |
| `client_secret.json`, `token.json` | Your Google key and sign-in. **Keep them private.** |
| `.upload_records.json` | The upload history, which prevents duplicate uploads. Back it up and don't delete it. |
| `uploader.log` | What happened on each run. |
| `source\` | The source code. Run `source\build.bat` to rebuild the .exe. |

To stop the automatic uploads, untick the Windows sign-in option in the app and
click **Save settings**.

## For developers

```bash
cd source
py -3 -m unittest -v      # tests
py -3 app.py              # window
py -3 app.py --dry-run    # list clips and titles
py -3 app.py --run        # one silent upload pass
build.bat                 # build ..\YouTubeClipUploader.exe
```
