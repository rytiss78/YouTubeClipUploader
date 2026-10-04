"""Read channel statistics and apply a saved, reviewable metadata plan."""
import argparse
import copy
import json
from datetime import datetime, timezone
from pathlib import Path

from clip_metadata import clean_filename
from clip_uploader import BASE, atomic_json, load_config, verify_channel, youtube_service

SNIPPET_FIELDS = ('title', 'description', 'tags', 'categoryId', 'defaultLanguage', 'defaultAudioLanguage')
OBSERVED_TITLES = {
    'K15nPFQZU98': 'Final Ring Fight Ends in Second Place | Apex Legends',
    'yXJTsptg-zg': 'M4A1-S Through the Smoke on Office | CS2',
    'Z7zJBjieAgw': 'AK-47 and Hostage Room Hold on Office | CS2',
}


def pages(request):
    items, token = [], None
    while True:
        response = request(token).execute()
        items.extend(response.get('items', []))
        token = response.get('nextPageToken')
        if not token:
            return items


def snapshot(youtube, cfg):
    channel = verify_channel(youtube, cfg)
    channel = youtube.channels().list(part='snippet,statistics,contentDetails,brandingSettings', id=channel['id']).execute()['items'][0]
    uploads = pages(lambda token: youtube.playlistItems().list(part='contentDetails',
        playlistId=channel['contentDetails']['relatedPlaylists']['uploads'], maxResults=50, pageToken=token))
    ids = [v['contentDetails']['videoId'] for v in uploads]
    videos = []
    for index in range(0, len(ids), 50):
        videos.extend(youtube.videos().list(part='snippet,statistics,contentDetails,status',
            id=','.join(ids[index:index+50])).execute().get('items', []))
    playlists = pages(lambda token: youtube.playlists().list(part='snippet,status,contentDetails',
        mine=True, maxResults=50, pageToken=token))
    return {'captured_at': datetime.now(timezone.utc).isoformat(), 'channels': [channel],
            'videos': videos, 'playlists': playlists}


def editable_snippet(video):
    return {k: copy.deepcopy(video['snippet'][k]) for k in SNIPPET_FIELDS if k in video['snippet']}


def make_plan(audit):
    channel = audit['channels'][0]
    before = copy.deepcopy(channel.get('brandingSettings', {}))
    after = copy.deepcopy(before)
    after.setdefault('channel', {})['description'] = (
        'Gameplay moments from GrybuMedziotojas: Counter-Strike 2, Apex Legends, '
        'and other games I play.\n\nBrowse the playlists to find clips from your favorite game. '
        'Subscribe for more gameplay moments.')
    plan = {'channel_id': channel['id'], 'channel': {'before': before, 'after': after}, 'videos': []}
    game_playlists = {p['snippet']['title']: p['id'] for p in audit['playlists']}
    for video in audit['videos']:
        old = editable_snippet(video)
        # Limit automatic edits to public uploads with our known boilerplate.
        if video['status']['privacyStatus'] != 'public' or old.get('description') != 'Automatically uploaded gameplay clip.':
            continue
        title = old['title']
        if ' — ' not in title:
            continue
        game, stem = title.split(' — ', 1)
        if not stem.casefold().startswith(game.casefold() + ' replay'):
            continue
        new = copy.deepcopy(old)
        new['title'] = OBSERVED_TITLES.get(video['id'], f'{game} | {clean_filename(stem, game)}')
        new['description'] = f'{game} gameplay from GrybuMedziotojas.'
        if video['id'] == 'K15nPFQZU98':
            new['description'] = 'A final-ring fight with two squads remaining in Apex Legends. The squad finishes in second place.'
        elif video['id'] == 'yXJTsptg-zg':
            new['description'] = 'M4A1-S gameplay around the smoke-filled hallways on Office in Counter-Strike 2.'
        elif video['id'] == 'Z7zJBjieAgw':
            new['description'] = 'AK-47 gameplay followed by a hostage-room hold on Office in Counter-Strike 2.'
        if game in game_playlists:
            new['description'] += f'\n\nMore {game} clips:\nhttps://www.youtube.com/playlist?list={game_playlists[game]}'
        new['description'] += '\n\nMore gameplay from GrybuMedziotojas:\nhttps://www.youtube.com/@grybumedziotojas'
        plan['videos'].append({'id': video['id'], 'before': old, 'after': new})
    return plan


def apply_plan(youtube, cfg, plan):
    channel = verify_channel(youtube, cfg)
    if plan['channel_id'] != channel['id']:
        raise ValueError('Plan belongs to a different channel')
    fresh = youtube.channels().list(part='brandingSettings', id=channel['id']).execute()['items'][0]
    current = fresh.get('brandingSettings', {})
    change = plan['channel']
    if current not in (change['before'], change['after']):
        raise ValueError('Channel settings changed after planning; regenerate the plan')
    # Persist exact intended changes before sending writes; keep each result too.
    atomic_json(BASE / 'channel_changes_before.json', plan)
    applied = []
    if current != change['after']:
        youtube.channels().update(part='brandingSettings', body={'id': channel['id'],
            'brandingSettings': change['after']}).execute()
    applied.append({'channel_id': channel['id'], 'description_updated': True})
    atomic_json(BASE / 'channel_changes_applied.json', applied)
    for entry in plan['videos']:
        video = youtube.videos().list(part='snippet,status', id=entry['id']).execute()['items'][0]
        if video['snippet']['channelId'] != channel['id'] or video['status']['privacyStatus'] != 'public':
            raise ValueError(f"Video ownership or visibility changed: {entry['id']}")
        current = editable_snippet(video)
        if current not in (entry['before'], entry['after']):
            raise ValueError(f"Video changed after planning: {entry['id']}")
        if current != entry['after']:
            youtube.videos().update(part='snippet', body={'id': entry['id'], 'snippet': entry['after']}).execute()
        applied.append({'id': entry['id'], 'title': entry['after']['title']})
        atomic_json(BASE / 'channel_changes_applied.json', applied)
    return applied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['audit', 'plan', 'apply'])
    parser.add_argument('--file', type=Path, default=BASE / 'channel_changes_plan.json')
    args = parser.parse_args()
    cfg = load_config()
    if args.command == 'plan':
        audit = json.loads((BASE / 'channel_audit.json').read_text(encoding='utf-8'))
        plan = make_plan(audit)
        atomic_json(args.file, plan)
        print(f"Saved plan: channel description + {len(plan['videos'])} video metadata updates")
        for entry in plan['videos']:
            print(entry['id'], json.dumps(entry['after']['title'], ensure_ascii=True))
        return
    youtube = youtube_service(interactive=False)
    if args.command == 'apply':
        plan = json.loads(args.file.read_text(encoding='utf-8'))
        print(f'Applied {len(apply_plan(youtube, cfg, plan))} changes')
    audit = snapshot(youtube, cfg)
    atomic_json(BASE / 'channel_audit.json', audit)
    channel = audit['channels'][0]
    print(json.dumps({'channel': channel['snippet']['title'], 'statistics': channel['statistics'],
                      'videos_read': len(audit['videos']), 'playlists': len(audit['playlists'])}))


if __name__ == '__main__':
    main()
