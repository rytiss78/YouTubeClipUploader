"""Truthful upload metadata, with optional <clip>.json editorial notes."""
import json
import re
from datetime import datetime


def load_notes(path):
    sidecar = path.with_suffix('.json')
    if not sidecar.exists():
        return {}
    notes = json.loads(sidecar.read_text(encoding='utf-8-sig'))
    if not isinstance(notes, dict):
        raise ValueError(f'{sidecar}: expected an object')
    for field in ('title', 'description', 'thumbnail'):
        if field in notes and not isinstance(notes[field], str):
            raise ValueError(f'{sidecar}: {field} must be text')
    if 'tags' in notes and (not isinstance(notes['tags'], list) or
                           any(not isinstance(x, str) for x in notes['tags'])):
        raise ValueError(f'{sidecar}: tags must be a list of strings')
    return notes


def clean_filename(stem, game):
    name = stem.replace('_', ' ').strip()
    name = re.sub(r'\s*\(online-video-cutter\.com\)', '', name, flags=re.I)
    name = re.sub(r'^' + re.escape(game) + r'\s*(?:[—|\-]\s*)?', '', name, flags=re.I)
    match = re.fullmatch(r'replay\s+(\d{4})[. -](\d{2})[. -](\d{2})[- ](\d{2})[. :](\d{2})', name, flags=re.I)
    if match:
        try:
            stamp = datetime(*map(int, match.groups()))
            return f'Gameplay Clip | {stamp:%d %b %Y, %H:%M}'
        except ValueError:
            pass
    return name or 'Gameplay Clip'


def build_metadata(path, game, cfg, notes=None):
    notes = load_notes(path) if notes is None else notes
    filename = clean_filename(path.stem, game)
    title = notes.get('title') or cfg.get('title_format', '{game} | {filename}').format(game=game, filename=filename)
    title = title.strip()
    if not title or len(title) > 100 or '<' in title or '>' in title:
        raise ValueError('Title must contain 1–100 characters and no angle brackets')
    description = notes.get('description', cfg['description'].format(game=game, filename=filename))
    if len(description.encode('utf-8')) > 5000 or '<' in description or '>' in description:
        raise ValueError('Description exceeds YouTube limits')
    tags = []
    for tag in [game, *cfg.get('tags', []), *notes.get('tags', [])]:
        tag = str(tag).strip()
        if tag and tag.casefold() not in [t.casefold() for t in tags]:
            tags.append(tag)
    # YouTube counts quotes around tags containing spaces, and commas.
    if sum(len(t) + (2 if ' ' in t else 0) for t in tags) + max(0, len(tags)-1) > 500:
        raise ValueError('Tags exceed the 500-character limit')
    return {'title': title, 'description': description, 'tags': tags,
            'categoryId': str(cfg.get('category_id', '20'))}
