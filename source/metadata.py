"""Titles, descriptions and tags for uploads, with optional <clip>.json overrides."""
import json
import re
from datetime import datetime


def load_notes(path):
    """Optional editorial notes saved beside the recording as <clip>.json."""
    sidecar = path.with_suffix('.json')
    if not sidecar.exists():
        return {}
    notes = json.loads(sidecar.read_text(encoding='utf-8-sig'))
    if not isinstance(notes, dict):
        raise ValueError(f'{sidecar.name}: expected an object')
    for field in ('title', 'description', 'thumbnail'):
        if field in notes and not isinstance(notes[field], str):
            raise ValueError(f'{sidecar.name}: {field} must be text')
    if 'tags' in notes and (not isinstance(notes['tags'], list) or
                           any(not isinstance(x, str) for x in notes['tags'])):
        raise ValueError(f'{sidecar.name}: tags must be a list of text')
    return notes


def clean_filename(stem, game):
    """Turn 'Apex Legends_replay_2026.09.27-19.51' into 'Gameplay Clip | 27 Sep 2026, 19:51'."""
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


def fill(template, game, filename):
    # Plain replacement, so other braces in a description are left alone.
    return template.replace('{game}', game).replace('{filename}', filename)


def no_brackets(text):
    # YouTube rejects titles and descriptions containing < or >.
    return text.replace('<', '').replace('>', '')


def build_metadata(path, game, cfg, notes=None):
    notes = load_notes(path) if notes is None else notes
    sidecar = path.with_suffix('.json').name
    filename = clean_filename(path.stem, game)

    if notes.get('title'):
        title = notes['title'].strip()
        if not title or len(title) > 100 or '<' in title or '>' in title:
            raise ValueError(f'{sidecar}: title must be 1–100 characters with no < or >')
    else:
        title = no_brackets(fill(cfg.get('title_format') or '{game} | {filename}', game, filename)).strip()
        title = title[:100].rstrip() or 'Gameplay Clip'

    if 'description' in notes:
        description = notes['description']
        if '<' in description or '>' in description:
            raise ValueError(f'{sidecar}: description cannot contain < or >')
    else:
        description = no_brackets(fill(cfg.get('description', ''), game, filename))
    if len(description.encode('utf-8')) > 5000:
        raise ValueError('Description is longer than YouTube allows (5000 bytes)')

    tags = []
    for tag in [game, *cfg.get('tags', []), *notes.get('tags', [])]:
        tag = no_brackets(str(tag)).strip()
        if tag and tag.casefold() not in (t.casefold() for t in tags):
            tags.append(tag)
    # YouTube allows 500 characters of tags, counting commas and quotes around spaced tags.
    while tags and sum(len(t) + (2 if ' ' in t else 0) for t in tags) + len(tags) - 1 > 500:
        tags.pop()

    return {'title': title, 'description': description, 'tags': tags,
            'categoryId': str(cfg.get('category_id', '20'))}
