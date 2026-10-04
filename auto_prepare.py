"""Local automatic edge trimming using motion and audio, with conservative fallback."""
import argparse
import array
import hashlib
import json
import math
import subprocess
from pathlib import Path

from prepare_clip import BASE, ffmpeg_executable, frame, inspect_clip

VERSION = 1


def run_ffmpeg(args):
    return subprocess.run([ffmpeg_executable(), '-v', 'error', *args],
                          capture_output=True, check=True, timeout=900).stdout


def signals(path, duration, info):
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
        pcm = run_ffmpeg(['-i', str(path), '-vn', '-ac', '1', '-ar', '8000',
                          '-f', 's16le', '-'])
        samples = array.array('h')
        samples.frombytes(pcm)
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
        return (*full, 'kept original: short clip or insufficient audio/video evidence')
    motion_limit = float(settings.get('quiet_motion', 3.0))
    audio_limit = float(settings.get('quiet_audio_db', -35.0))
    # Requiring both prevents quiet moving footage or loud stationary action
    # from being classified as dead time. Never remove anything in the middle.
    quiet = [m < motion_limit and a < audio_limit for m, a in zip(motion, audio)]
    active = [i for i, value in enumerate(quiet) if not value]
    if len(active) < 3:
        return (*full, 'kept original: activity is ambiguous')
    first, last = active[0], active[-1]
    before = float(settings.get('context_before', 3))
    after = float(settings.get('context_after', 5))
    start = max(0.0, first-before)
    end = min(duration, last+1+after)
    min_edge = float(settings.get('min_edge_trim', 4))
    if start < min_edge:
        start = 0.0
    if duration-end < min_edge:
        end = duration
    if end-start < float(settings.get('min_clip_seconds', 15)) or end-start < duration*0.35:
        return (*full, 'kept original: proposed trim would remove too much context')
    return start, end, 'trimmed quiet edges' if (start, end) != full else 'kept original: no clear quiet edges'


def validate_export(path, expected_duration, source_info):
    duration, info = inspect_clip(path)
    if abs(duration-expected_duration) > 0.3:
        raise RuntimeError('Prepared duration does not match selected trim')
    import re
    source_size = re.search(r'Video:.*? (\d{2,5}x\d{2,5})', source_info)
    output_size = re.search(r'Video:.*? (\d{2,5}x\d{2,5})', info)
    if not source_size or not output_size or source_size[1] != output_size[1]:
        raise RuntimeError('Prepared video resolution changed')
    if 'Audio:' in source_info and 'Audio:' not in info:
        raise RuntimeError('Prepared video lost audio')
    source_fps = re.search(r'(\d+(?:\.\d+)?) fps', source_info)
    output_fps = re.search(r'(\d+(?:\.\d+)?) fps', info)
    if source_fps and (not output_fps or abs(float(source_fps[1])-float(output_fps[1])) > 0.05):
        raise RuntimeError('Prepared frame rate changed')
    run_ffmpeg(['-i', str(path), '-f', 'null', '-'])


def prepare(path, digest, cfg):
    settings = cfg.get('auto_prepare', {})
    if not settings.get('enabled', False):
        return {'media_path': str(path), 'reason': 'automatic preparation disabled'}
    key = hashlib.sha256(json.dumps({'settings': settings, 'version': VERSION}, sort_keys=True).encode()).hexdigest()[:12]
    folder = Path(cfg.get('prepared_folder', str(BASE / 'prepared'))) / digest / key
    manifest = folder / 'preparation.json'
    if manifest.exists():
        result = json.loads(manifest.read_text(encoding='utf-8'))
        if Path(result['media_path']).is_file() and Path(result['thumbnail']).is_file():
            return result
    duration, info = inspect_clip(path)
    if duration > float(settings.get('max_analysis_seconds', 600)):
        # Do not decode unbounded long recordings in memory.
        motion, audio = [], None
        start, end, reason = 0.0, duration, 'kept original: exceeds analysis length limit'
    else:
        motion, audio = signals(path, duration, info)
        start, end, reason = select_bounds(duration, motion, audio, settings)
    folder.mkdir(parents=True, exist_ok=True)
    media_path = path
    if start > 0 or end < duration:
        media_path = folder / 'clip.mp4'
        temporary = folder / 'clip.partial.mp4'
        run_ffmpeg(['-y', '-ss', str(start), '-i', str(path), '-t', str(end-start),
                    '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264', '-crf', '18',
                    '-preset', 'fast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k',
                    '-movflags', '+faststart', str(temporary)])
        validate_export(temporary, end-start, info)
        temporary.replace(media_path)
    # Prefer a clear frame near activity, excluding the initial/final screens.
    candidates = [i for i in range(len(motion)) if start+1 <= i < end-1]
    candidates = sorted(candidates, key=lambda i: motion[i], reverse=True)[:6]
    if not candidates:
        candidates = [start + (end-start)*0.4]
    from PIL import ImageFilter, ImageStat
    best, best_score, at = None, -1, None
    for timestamp in candidates:
        im = frame(path, timestamp, 1280)
        gray = im.convert('L')
        brightness = ImageStat.Stat(gray).mean[0]
        score = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).mean[0] if 20 < brightness < 235 else 0
        if score > best_score:
            best, best_score, at = im, score, timestamp
    thumbnail = folder / 'thumbnail.jpg'
    best.save(thumbnail, quality=92)
    result = {'media_path': str(media_path.resolve()), 'thumbnail': str(thumbnail.resolve()),
              'start': start, 'end': end, 'original_duration': duration,
              'thumbnail_at': at, 'reason': reason, 'algorithm_version': VERSION}
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2), encoding='utf-8')
    temporary.replace(manifest)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    args = parser.parse_args()
    from clip_uploader import load_config
    h = hashlib.sha256()
    with args.source.open('rb') as f:
        for chunk in iter(lambda: f.read(1024*1024), b''):
            h.update(chunk)
    print(json.dumps(prepare(args.source, h.hexdigest(), load_config()), indent=2))
