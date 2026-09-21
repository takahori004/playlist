# cocofuroデイリープレイリスト作成用

import os
import glob
import json
import time
from datetime import datetime
import spotipy
from spotipy.oauth2 import SpotifyOAuth

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8080")
SCOPE = "playlist-modify-public playlist-modify-private playlist-read-private"
CACHE_PATH = ".cache-sppl"
JSON_DIR = "/Users/takashi/batch/sppl/json"

auth_manager = SpotifyOAuth(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    redirect_uri=REDIRECT_URI,
    scope=SCOPE,
    show_dialog=False,
    cache_path=CACHE_PATH
)

# 429レートリミット時の自動長時間スリープを防止
sp = spotipy.Spotify(
    auth_manager=auth_manager,
    retries=0,
    status_forcelist=[]
)

def search_track(sp, title, artist):
    queries = [
        f'track:"{title}" artist:"{artist}"',
        f'"{title}" "{artist}"',
        f'{title} {artist}',
        f'track:"{title}"',
        f'"{title}"'
    ]
    clean_target = title.replace(" ", "").replace(" ", "").replace("、", "").lower()

    for q in queries:
        try:
            res = sp.search(q=q, type="track", limit=5)
            items = res.get("tracks", {}).get("items", [])
            for item in items:
                track_name = item["name"].replace(" ", "").replace(" ", "").replace("、", "").lower()
                if clean_target in track_name or track_name in clean_target:
                    return item
        except Exception:
            continue
    return None

def extract_facility_name(data, filename):
    """JSON内の設定またはファイル名から施設名を抽出"""
    if isinstance(data, dict):
        if "facility" in data:
            return data["facility"]
        if "facility_name" in data:
            return data["facility_name"]
    return os.path.basename(filename).split("_")[0]

def render_template(template, **values):
    """{facility} {date} {year} {month} 等のプレースホルダを実値に置換"""
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result

def process_daily(json_path, user_playlists, today):
    filename = os.path.basename(json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    today_str = today.strftime("%Y-%m-%d")
    facility_name = extract_facility_name(data, filename)
    all_songs = data.get("songs", []) if isinstance(data, dict) else data

    # 今日の日付の曲だけを抽出
    today_songs = [s for s in all_songs if s.get("date") == today_str]

    print(f"\n{'='*55}")
    print(f"♨️ {facility_name}（{filename}）: {today_str} 分")
    print(f"{'='*55}")

    if not today_songs:
        print(f"ℹ️ 本日（{today_str}）の対象楽曲はありません。スキップします。")
        return

    template_values = {
        "facility": facility_name,
        "date": today_str,
        "year": today.year,
        "month": today.month,
    }
    playlist_name_template = data.get("daily_playlist_name", "【本日の曲】{facility} ミュージックロウリュ")
    playlist_desc_template = data.get(
        "daily_description",
        "{facility} の本日（{date}）のミュージックロウリュ楽曲まとめ（自動更新 / 非公式）"
    )
    playlist_name = render_template(playlist_name_template, **template_values)
    playlist_desc = render_template(playlist_desc_template, **template_values)

    # 既存プレイリストを探索
    target_playlist = next((pl for pl in user_playlists if pl["name"] == playlist_name), None)

    if target_playlist:
        print(f"既存プレイリストを更新: 「{playlist_name}」")
        playlist_id = target_playlist["id"]
        playlist_url = target_playlist["external_urls"]["spotify"]
    else:
        print(f"プレイリストを新規作成: 「{playlist_name}」")
        new_pl = sp.current_user_playlist_create(
            name=playlist_name,
            public=data.get("public", True),
            description=playlist_desc
        )
        playlist_id = new_pl["id"]
        playlist_url = new_pl["external_urls"]["spotify"]
        user_playlists.append(new_pl)

    # 楽曲検索
    print(f"全 {len(today_songs)} 曲の検索を開始...\n")
    track_uris = []
    seen_uris = set()

    for item in today_songs:
        title = item.get("title", "")
        artist = item.get("artist", "")

        matched = search_track(sp, title, artist)
        if matched:
            uri = matched["uri"]
            if uri not in seen_uris:
                track_uris.append(uri)
                seen_uris.add(uri)
                artist_disp = matched['artists'][0]['name'] if matched.get('artists') else '不明'
                print(f"✅ OK: {title} / {artist} -> {matched['name']} ({artist_disp})")
        else:
            print(f"❌ 該当なし: {title} / {artist}")
        time.sleep(0.3)  # レートリミット安全ウェイト

    # プレイリストの中身を本日の曲で上書き
    if track_uris:
        sp.playlist_replace_items(playlist_id, track_uris[:100])
        sp.playlist_change_details(
            playlist_id=playlist_id,
            name=playlist_name,
            description=playlist_desc
        )
        print(f"\n🎉 更新完了: {len(track_uris)} 曲")
        print(f"URL: {playlist_url}")
    else:
        print(f"⚠️ マッチする楽曲がありませんでした。")

def main():
    if not os.path.exists(JSON_DIR):
        print(f"❌ ディレクトリ '{JSON_DIR}' が存在しません。")
        return

    json_files = sorted(glob.glob(os.path.join(JSON_DIR, "*.json")))
    if not json_files:
        print(f"⚠️ '{JSON_DIR}' 内に JSON ファイルが見つかりません。")
        return

    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    print(f"📅 実行日: {today_str}")
    print(f"🔍 {len(json_files)} 件の店舗定義を検出。プレイリスト一覧を取得中...")

    user_playlists = sp.current_user_playlists(limit=50).get("items", [])

    for jf in json_files:
        try:
            process_daily(jf, user_playlists, today)
        except Exception as e:
            print(f"❌ エラー発生 ({jf}): {e}")

    print(f"\n{'#'*55}")
    print(f"✨ 全店舗のデイリー更新が完了しました！")
    print(f"{'#'*55}")

if __name__ == "__main__":
    main()
