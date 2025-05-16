import random
import json
import time
from utils.deezer import load_artists
import urllib.request
import urllib.error


def fetch_track_with_preview(artist_id, difficulty):
    start_time = time.time()
    url = f"https://api.deezer.com/artist/{artist_id}/top?limit=50"
    try:
        with urllib.request.urlopen(url) as response:
            if response.status != 200:
                return None
            data = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return None
    except Exception as e:
        return None

    tracks = data.get("data", [])
    if not tracks:
        return None

    valid_tracks = [track for track in tracks if track.get('preview')]
    if not valid_tracks:
        return None

    sorted_tracks = sorted(
        valid_tracks,
        key=lambda x: int(x.get("rank", 0)) if x.get("rank") is not None and str(x.get("rank")).isdigit() else 0,
        reverse=True
    )

    if difficulty == 'easy':
        track = random.choice(sorted_tracks[:5]) if len(sorted_tracks) >= 5 else sorted_tracks[0]
    elif difficulty == 'medium':
        mid_start = min(5, len(sorted_tracks))
        mid_end = min(10, len(sorted_tracks))
        track = random.choice(sorted_tracks[mid_start:mid_end]) if mid_end > mid_start else sorted_tracks[0]
    else:  # hard
        low_start = min(10, len(sorted_tracks))
        track = random.choice(sorted_tracks[low_start:]) if len(sorted_tracks) > low_start else sorted_tracks[0]

    return track


def fetch_track_from_file(artist, difficulty):
    tracks = artist.get('tracks', [])
    if not tracks:
        return None

    processed_tracks = []
    for idx, track in enumerate(tracks):
        if isinstance(track, str):
            try:
                track = json.loads(track)
            except json.JSONDecodeError:
                track = {
                    "title": track,
                    "id": f"generated_{artist['id']}_{idx}",
                    "rank": 0
                }
        if isinstance(track, dict):
            processed_tracks.append(track)
        else:
            continue

    if not processed_tracks:
        return None

    sorted_tracks = sorted(
        processed_tracks,
        key=lambda x: int(x.get("rank", 0)) if x.get("rank") is not None and str(x.get("rank")).isdigit() else 0,
        reverse=True
    )

    if difficulty == 'easy':
        track = random.choice(sorted_tracks[:5]) if len(sorted_tracks) >= 5 else sorted_tracks[0]
    elif difficulty == 'medium':
        mid_start = min(5, len(sorted_tracks))
        mid_end = min(10, len(sorted_tracks))
        track = random.choice(sorted_tracks[mid_start:mid_end]) if mid_end > mid_start else sorted_tracks[0]
    else:
        low_start = min(10, len(sorted_tracks))
        track = random.choice(sorted_tracks[low_start:]) if len(sorted_tracks) > low_start else sorted_tracks[0]

    track['artist'] = {'name': artist['name']}
    track['id'] = f"track_{track['id']}_{artist['id']}"
    return track


def select_track_and_options(session_data, difficulty, style='any', country=None):
    start_time = time.time()
    session_data.setdefault('used_track_ids', [])
    session_data.setdefault('used_artists', {'easy': [], 'medium': [], 'hard': []})
    session_data.setdefault('last_artist_index', {'easy': 0, 'medium': 0, 'hard': 0})
    session_data.setdefault('failed_artists', [])

    used_track_ids = set(session_data['used_track_ids'][-100:])
    used_artists = set(session_data['used_artists'][difficulty][-100:])
    failed_artists = session_data['failed_artists'][-100:]

    all_artists = load_artists(genre=None)
    if not all_artists:
        return None, [], session_data

    if difficulty == 'easy':
        artist_pool = all_artists[:100]
    elif difficulty == 'medium':
        artist_pool = all_artists[:1000]
    else:
        artist_pool = all_artists[100:] if len(all_artists) > 100 else all_artists

    if not artist_pool:
        return None, [], session_data

    available_artists = [artist for artist in artist_pool if artist['name'] not in used_artists]
    if len(available_artists) < 4:
        session_data['used_artists'][difficulty] = []
        session_data['used_track_ids'] = []
        used_artists = set()
        available_artists = artist_pool

    if not available_artists:
        return None, [], session_data

    correct_track = None
    correct_artist = None
    max_attempts = min(10, len(available_artists))
    attempted_artists = []
    for attempt in range(max_attempts):
        correct_artist = random.choice(available_artists)
        attempted_artists.append(correct_artist['name'])
        correct_track = fetch_track_with_preview(correct_artist['id'], difficulty)
        if correct_track and correct_track.get('preview'):
            correct_track['artist'] = {'name': correct_artist['name']}
            correct_track['id'] = f"track_{correct_track['id']}_{correct_artist['id']}"
            break
        failed_artists.append(correct_artist['name'])
        available_artists = [a for a in available_artists if a['name'] != correct_artist['name']]
        correct_track = None
        correct_artist = None

    if not correct_track or not correct_artist:
        session_data['used_track_ids'] = []
        session_data['failed_artists'] = failed_artists[-100:]
        return None, [], session_data

    incorrect_artists = []
    incorrect_tracks = []
    max_incorrect_attempts = min(20, len(available_artists) - 1)
    available_for_incorrect = [a for a in available_artists if a['name'] != correct_artist['name']]
    for _ in range(max_incorrect_attempts):
        if len(incorrect_artists) >= 3:
            break
        if not available_for_incorrect:
            break
        artist = random.choice(available_for_incorrect)
        track = fetch_track_from_file(artist, difficulty)
        if track:
            incorrect_artists.append(artist)
            incorrect_tracks.append(track)
        available_for_incorrect = [a for a in available_for_incorrect if a['name'] != artist['name']]

    if len(incorrect_tracks) < 3:
        session_data['failed_artists'] = failed_artists[-100:]
        return None, [], session_data

    options = [correct_track] + incorrect_tracks[:3]
    random.shuffle(options)

    used_artists.add(correct_artist['name'])
    for track in incorrect_tracks:
        used_artists.add(track['artist']['name'])
    for track in options:
        used_track_ids.add(track['id'])

    session_data['used_track_ids'] = list(used_track_ids)[-100:]
    session_data['used_artists'][difficulty] = list(used_artists)[-100:]
    session_data['last_artist_index'][difficulty] = 0
    session_data['failed_artists'] = failed_artists[-100:]

    return correct_track, options, session_data
