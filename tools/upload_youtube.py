#!/usr/bin/env python3
"""만든 쇼츠를 유튜브에 올림 (GitHub Actions 에서 실행)

필요한 비밀 값 (GitHub 저장소 → Settings → Secrets and variables → Actions):
  YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
값은 수형 님이 직접 넣고, 이 스크립트는 실행할 때만 읽음 (화면이나 기록에 출력하지 않음).

사용: python tools/upload_youtube.py out/peakle-2026-11-02-en.mp4   (같은 이름의 .json 에서 제목·설명을 읽음)
"""
import json, os, sys

import requests


def access_token():
    r = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": os.environ["YT_CLIENT_ID"], "client_secret": os.environ["YT_CLIENT_SECRET"],
        "refresh_token": os.environ["YT_REFRESH_TOKEN"], "grant_type": "refresh_token"}, timeout=60)
    if r.status_code != 200:
        sys.exit(f"유튜브 로그인 실패 (HTTP {r.status_code}). 연결을 다시 해야 할 수 있어요.")
    return r.json()["access_token"]


def upload(path, tok):
    meta = json.load(open(os.path.splitext(path)[0] + ".json", encoding="utf-8"))
    body = {
        "snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                    "tags": ["peakle", "architecture", "guessing game", "skyscraper", "shorts"], "categoryId": "20"},
        "status": {"privacyStatus": os.environ.get("YT_PRIVACY", "public"), "selfDeclaredMadeForKids": False},
    }
    size = os.path.getsize(path)
    r = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                      headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json; charset=UTF-8",
                               "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                      data=json.dumps(body), timeout=60)
    if r.status_code != 200 or "Location" not in r.headers:
        sys.exit(f"업로드 준비 실패 (HTTP {r.status_code}): {r.text[:300]}")
    with open(path, "rb") as f:
        up = requests.put(r.headers["Location"], data=f, headers={"Content-Type": "video/mp4"}, timeout=600)
    if up.status_code not in (200, 201):
        sys.exit(f"업로드 실패 (HTTP {up.status_code}): {up.text[:300]}")
    print("올림:", meta["title"], "→ https://youtube.com/shorts/" + up.json().get("id", "?"))


if __name__ == "__main__":
    t = access_token()
    for p in sys.argv[1:]:
        upload(p, t)
