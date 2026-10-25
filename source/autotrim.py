"""Trim quiet, still footage from the start and end of a recording.

A local heuristic using per-second motion and loudness. It never cuts the
middle, keeps a few seconds of context around the action, and uploads the
untouched original whenever the evidence is unclear or anything goes wrong.
It does not recognise kills, wins or other game events.
"""
import array
import hashlib
import json
import logging
import math
import re
import shutil
import subprocess
from pathlib import Path

VERSION = 1
# No console windows from the .exe, and keep the PC responsive while encoding.
PROCESS_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)


def ffmpeg_executable():
    try:
        import imageio_ffmpeg  # ships its own ffmpeg; bundled into the .exe
        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError):
        found = shutil.which('ffmpeg')
        if found:
            return found
        raise RuntimeError('ffmpeg is not available') from None


def run_ffmpeg(args):
    return subprocess.run([ffmpeg_executable(), '-v', 'error', *args], capture_output=True,
                          check=True, timeout=900, creationflags=PROCESS_FLAGS).stdout


def inspect_clip(path):
    result = subprocess.run([ffmpeg_executable(), '-hide_banner', '-i', str(path)],
                            capture_output=True, timeout=120, creationflags=PROCESS_FLAGS)
    info = result.stderr.decode('utf-8', errors='replace')
    match = re.search(r'Duration: (\d+):(\d+):(\d+\.\d+)', info)
    if not match:
        raise ValueError(f'Cannot read video duration: {path}')
    h, m, s = map(float, match.groups())
    return h * 3600 + m * 60 + s, info


def frame(path, timestamp, width=1280):
    from PIL import Image
    import io
    png = run_ffmpeg(['-ss', str(timestamp), '-i', str(path), '-frames:v', '1',
                      '-vf', f'scale={width}:-2', '-f', 'image2pipe', '-vcodec', 'png', '-'])
    return Image.open(io.BytesIO(png)).convert('RGB')


def signals(path, info):
    """Per-second motion (mean pixel change) and loudness (dBFS)."""
    # Downsample only the analysis, never the uploaded recording.
    from PIL import Image, ImageChops, ImageStat
    raw = run_ffmpeg(['-i', str(path), '-an', '-vf', 'fps=1,scale=160:90,format=gray',
                      '-f', 'rawvideo', '-pix_fmt', 'gray', '-'])
    size = 160 * 90
    frames = [Image.frombytes('L', (160, 90), raw[i:i+size]).crop((16, 9, 144, 81))
              for i in range(0, len(raw)-size+1, size)]
    motion = [0.0]
    for before, after in zip(frames, frames[1:]):
        motion.append(ImageStat.Stat(ImageChops.difference(before, after)).mean[0])
    audio = None
    if 'Audio:' in info:
        pcm = run_ffmpeg(['-i', str(path), '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
        samples = array.array('h')
        samples.frombytes(pcm[:len(pcm) - len(pcm) % 2])
        audio = []
        for index in range(len(motion)):
            chunk = samples[index*8000:(index+1)*8000]
            rms = math.sqrt(sum(x*x for x in chunk) / max(1, len(chunk))) / 32768
            audio.append(20 * math.log10(max(rms, 1e-6)))
    return motion, audio


def select_bounds(duration, motion, audio, settings):
    """Only remove consecutive quiet, low-motion edges, retaining context."""
    full = (0.0, duration)
    if duration < 20 or len(motion) < 10 or audio is None:
        return (*full, 'kept original: short clip or not enough audio/video to judge')
    motion_limit = float(settings.get('quiet_motion', 3.0))
    audio_limit = float(settings.get('quiet_audio_db', -35.0))
    # Requiring both prevents quiet moving footage or loud stationary action
    # from being classified as dead time. Never remove anything in the middle.
    quiet = [m < motion_limit and a < audio_limit for m, a in zip(motion, audio)]
    active = [i for i, value in enumerate(quiet) if not value]
    if len(active) < 3:
        return (*full, 'kept original: activity is ambiguous')
    first, last = active[0], active[-1]
    start = max(0.0, first - float(settings.get('context_before', 3)))
    end = min(duration, last + 1 + float(settings.get('context_after', 5)))
    min_edge = float(settings.get('min_edge_trim', 4))
    if start < min_edge:
        start = 0.0
    if duration - end < min_edge:
        end = duration
    if end - start < float(settings.get('min_clip_seconds', 15)) or end - start < duration * 0.35:
        return (*full, 'kept original: trim would remove too much')
    return start, end, 'trimmed quiet edges' if (start, end) != full else 'kept original: no clear quiet edges'


def validate_export(path, expected_duration, source_info):
    duration, info = inspect_clip(path)
    if abs(duration - expected_duration) > 0.3:
        raise RuntimeError('Trimmed duration does not match the selection')
    source_size = re.search(r'Video:.*? (\d{2,5}x\d{2,5})', source_info)
    output_size = re.search(r'Video:.*? (\d{2,5}x\d{2,5})', info)
    if not source_size or not output_size or source_size[1] != output_size[1]:
        raise RuntimeError('Trimmed video resolution changed')
    if 'Audio:' in source_info and 'Audio:' not in info:
        raise RuntimeError('Trimmed video lost its audio')
    source_fps = re.search(r'(\d+(?:\.\d+)?) fps', source_info)
    output_fps = re.search(r'(\d+(?:\.\d+)?) fps', info)
    if source_fps and (not output_fps or abs(float(source_fps[1]) - float(output_fps[1])) > 0.05):
        raise RuntimeError('Trimmed frame rate changed')
    run_ffmpeg(['-i', str(path), '-f', 'null', '-'])  # must decode cleanly


def pick_thumbnail(path, motion, start, end, folder):
    """A sharp, well-exposed frame near the action, or None."""
    from PIL import ImageFilter, ImageStat
    candidates = sorted((i for i in range(len(motion)) if start + 1 <= i < end - 1),
                        key=lambda i: motion[i], reverse=True)[:6] or [start + (end - start) * 0.4]
    best, best_score = None, -1
    for timestamp in candidates:
        try:
            image = frame(path, timestamp)
        except Exception:
            continue
        gray = image.convert('L')
        brightness = ImageStat.Stat(gray).mean[0]
        score = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0] if 20 < brightness < 235 else 0
        if score > best_score:
            best, best_score = image, score
    if best is None:
        return None
    thumbnail = folder / 'thumbnail.jpg'
    best.save(thumbnail, quality=92)
    return str(thumbnail.resolve())


def prepare(path, digest, settings, work_dir):
    """What to upload: {'media_path', 'thumbnail', 'start', 'end', 'reason'}.

    The original recording is never changed. Any failure means "upload the original".
    """
    if not settings.get('enabled', False):
        return {'media_path': str(path), 'thumbnail': None, 'reason': 'automatic trimming is off'}
    try:
        return _prepare(path, digest, settings, Path(work_dir))
    except Exception:
        logging.warning('Automatic trimming failed; uploading the original instead.', exc_info=True)
        return {'media_path': str(path), 'thumbnail': None, 'reason': 'trimming failed; original kept'}


def _prepare(path, digest, settings, work_dir):
    key = hashlib.sha256(json.dumps({'settings': settings, 'version': VERSION}, sort_keys=True).encode()).hexdigest()[:12]
    folder = work_dir / digest / key
    manifest = folder / 'preparation.json'
    if manifest.exists():
        # Reuse an earlier result so a retry does not re-encode.
        result = json.loads(manifest.read_text(encoding='utf-8'))
        if Path(result['media_path']).is_file():
            return result
    duration, info = inspect_clip(path)
    if duration > float(settings.get('max_analysis_seconds', 600)):
        # Do not decode very long recordings into memory.
        motion, start, end, reason = [], 0.0, duration, 'kept original: longer than the analysis limit'
    else:
        motion, audio = signals(path, info)
        start, end, reason = select_bounds(duration, motion, audio, settings)
    folder.mkdir(parents=True, exist_ok=True)
    media_path = path
    if start > 0 or end < duration:
        media_path = folder / 'clip.mp4'
        partial = folder / 'clip.partial.mp4'
        run_ffmpeg(['-y', '-ss', str(start), '-i', str(path), '-t', str(end - start),
                    '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-crf', '18',
                    '-preset', 'fast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                    '-movflags', '+faststart', str(partial)])
        validate_export(partial, end - start, info)
        partial.replace(media_path)
    try:
        thumbnail = pick_thumbnail(path, motion, start, end, folder)
    except Exception:
        logging.warning('Could not pick a thumbnail; YouTube will choose one.', exc_info=True)
        thumbnail = None
    result = {'media_path': str(Path(media_path).resolve()), 'thumbnail': thumbnail,
              'start': start, 'end': end, 'original_duration': duration,
              'reason': reason, 'algorithm_version': VERSION}
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2), encoding='utf-8')
    temporary.replace(manifest)
    return result
