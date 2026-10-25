"""YouTube Clip Uploader.

No arguments: open the setup/upload window.
--run:        upload new clips once without a window, then exit (used at Windows sign-in).
--dry-run:    print what would be uploaded (when run from source in a console).
"""
import argparse
import json
import logging

import uploader


def plural(count, word):
    return f'{count} {word}' if count == 1 else f'{count} {word}s'


def run_silently():
    def announce(count):
        uploader.notify(uploader.APP_NAME, f'Uploading {plural(count, "new clip")}…')
    try:
        summary = uploader.run_once(unattended=True, on_found=announce)
    except uploader.AlreadyRunning:
        logging.info('Another upload run is active; exiting.')
        return 0
    except uploader.SetupError as exc:
        logging.warning('%s', exc)
        uploader.notify('YouTube Clip Uploader needs attention', str(exc))
        return 1
    except Exception as exc:
        logging.exception('Upload run stopped.')
        uploader.notify('Upload run stopped', str(exc))
        return 1
    uploaded, failed = summary['uploaded'], summary['failed']
    if uploaded:
        uploader.notify(f'Uploaded {plural(len(uploaded), "clip")}', '\n'.join(uploaded[:3]))
    if failed:
        uploader.notify(f'{plural(len(failed), "clip")} failed',
                        'They stay in place and are retried next time. Open the app to see the log.')
    if summary['stopped']:
        uploader.notify('Upload limit reached', summary['stopped'])
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Upload gameplay clips to YouTube.')
    parser.add_argument('--run', '--non-interactive', dest='run', action='store_true',
                        help='upload new clips once without a window, then exit')
    parser.add_argument('--dry-run', action='store_true', help='list clips and titles; upload nothing')
    args = parser.parse_args(argv)
    uploader.setup_logging()
    if args.run:
        return run_silently()
    if args.dry_run:
        clips = uploader.preview()
        for path, metadata in clips:
            print(json.dumps({'path': str(path), 'snippet': metadata}, ensure_ascii=False))
        print(f'{len(clips)} clip(s). Nothing was uploaded or moved.')
        return 0
    import gui
    gui.main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
