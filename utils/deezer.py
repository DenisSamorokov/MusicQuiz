import json
from pathlib import Path
import urllib.request
import urllib.error

GENRES_DIR = Path("genres")
ALL_ARTISTS_FILE = Path("artists_with_tracks.json")


def load_artists(genre=None):
    artists = []
    if genre and genre != "any":
        file_path = GENRES_DIR / f"{genre}.json"
        if not file_path.exists():
            return []
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                genre_artists = json.load(f)
                artists = [
                    {
                        "id": artist.get("id", f"unknown_{idx}"),
                        "name": artist["name"],
                        "genre": genre,
                        "tracks": artist["tracks"]
                    }
                    for idx, artist in enumerate(genre_artists)
                    if artist.get("tracks", [])
                ]
        except (json.JSONDecodeError, IOError) as e:
            return []
    else:
        if not ALL_ARTISTS_FILE.exists():
            return []
        try:
            with open(ALL_ARTISTS_FILE, "r", encoding="utf-8") as f:
                all_artists = json.load(f)
                artists = [
                    {
                        "id": artist.get("id", f"unknown_{idx}"),
                        "name": artist["name"],
                        "genre": ", ".join(artist.get("genres", ["unknown"])),
                        "tracks": artist["tracks"]
                    }
                    for idx, artist in enumerate(all_artists)
                    if artist.get("tracks", [])  # Проверяем, что tracks не пустой
                ]
        except (json.JSONDecodeError, IOError) as e:
            return []
    return artists


def fetch(url):
    try:
        with urllib.request.urlopen(url) as response:
            if response.status != 200:
                return None
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        return None
    except Exception as e:
        return None


def get_artist_top_tracks(artist_id, limit=20):
    url = f"https://api.deezer.com/artist/{artist_id}/top?limit={limit}"
    data = fetch(url)
    if not data:
        return []
    tracks = data.get("data", [])
    valid_tracks = []
    for track in tracks:
        if "preview" in track and track["preview"]:
            headers = {'Origin': 'http://127.0.0.1:5000'}
            req = urllib.request.Request(track["preview"], headers=headers, method='HEAD')
            with urllib.request.urlopen(req) as response:
                content_type = response.headers.get('Content-Type', '')
                if response.status == 200 and 'audio' in content_type.lower():
                    valid_tracks.append(track)
    return valid_tracks
