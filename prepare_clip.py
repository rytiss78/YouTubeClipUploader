"""Preview and trim clips locally. Original recordings are never modified."""
import argparse
import io
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE / '.tools'))


def ffmpeg_executable():
    executable = shutil.which('ffmpeg')
    if executable:
        return executable
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        raise RuntimeError('Install optional tools: py -3 -m pip install --target .tools imageio-ffmpeg Pillow') from None


def inspect_clip(path):
    result = subprocess.run([ffmpeg_executable(), '-hide_banner', '-i', str(path)],
                            capture_output=True, text=True)
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', result.stderr)
    if not match:
        raise ValueError(f'Cannot read video duration: {path}')
    h, m, s = map(float, match.groups())
    return h * 3600 + m * 60 + s, result.stderr


def frame(path, timestamp, width=640):
    from PIL import Image
    result = subprocess.run([ffmpeg_executable(), '-v', 'error', '-ss', str(timestamp),
                             '-i', str(path), '-frames:v', '1', '-vf', f'scale={width}:-2',
                             '-f', 'image2pipe', '-vcodec', 'png', '-'],
                            capture_output=True, check=True)
    return Image.open(io.BytesIO(result.stdout)).convert('RGB')


def contact_sheet(path, output):
    from PIL import Image, ImageDraw
    duration, info = inspect_clip(path)
    times = [duration * i / 12 for i in range(12)]
    frames = [frame(path, t) for t in times]
    width, height = frames[0].size
    sheet = Image.new('RGB', (width * 3, (height + 30) * 4), '#10141d')
    draw = ImageDraw.Draw(sheet)
    for i, (im, t) in enumerate(zip(frames, times)):
        x, y = i % 3 * width, i // 3 * (height + 30)
        sheet.paste(im, (x, y))
        draw.text((x + 10, y + height + 7), f'{t:.1f}s', fill='white')
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output)
    return duration, info


def trim_clip(path, output, start, end, title, thumbnail_at=None):
    duration, _ = inspect_clip(path)
    if not 0 <= start < end <= duration:
        raise ValueError(f'Trim must be inside 0–{duration:.2f} seconds')
    if not title.strip() or len(title) > 100:
        raise ValueError('Supply a title of 1–100 characters')
    if thumbnail_at is not None and not start <= thumbnail_at < end:
        raise ValueError('Thumbnail time must fall inside the selected trim')
    if output.resolve() == path.resolve() or output.suffix.lower() != '.mp4':
        raise ValueError('Output must be a new MP4 file')
    for target in (output, output.with_suffix('.json'), output.with_suffix('.jpg')):
        if target.exists():
            raise ValueError(f'Output already exists: {target}')
    output.parent.mkdir(parents=True, exist_ok=True)
    # Frame-accurate trim; preserve resolution and frame rate, without portrait cropping.
    subprocess.run([ffmpeg_executable(), '-v', 'error', '-n', '-ss', str(start),
                    '-i', str(path), '-t', str(end-start), '-map', '0:v:0', '-map', '0:a:0?',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p',
                    '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', str(output)], check=True)
    notes = {'title': title, 'reviewed': True}
    if thumbnail_at is not None:
        thumbnail = output.with_suffix('.jpg')
        frame(path, thumbnail_at, width=1280).save(thumbnail, quality=92)
        notes['thumbnail'] = thumbnail.name
    output.with_suffix('.json').write_text(json.dumps(notes, indent=2), encoding='utf-8')
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--review', type=Path, help='Create a contact sheet at this path')
    parser.add_argument('--output', type=Path, help='New MP4 output for a trim')
    parser.add_argument('--start', type=float)
    parser.add_argument('--end', type=float)
    parser.add_argument('--title')
    parser.add_argument('--thumbnail-at', type=float, help='Time in original recording')
    args = parser.parse_args()
    if args.review:
        duration, info = contact_sheet(args.source, args.review)
        print(info)
        print(f'Review saved: {args.review.resolve()} ({duration:.2f}s)')
    if args.output:
        if args.start is None or args.end is None or args.title is None:
            parser.error('--output requires --start, --end, and --title')
        print(trim_clip(args.source, args.output, args.start, args.end, args.title, args.thumbnail_at).resolve())
    if not args.review and not args.output:
        parser.error('Supply --review or --output')


if __name__ == '__main__':
    main()
