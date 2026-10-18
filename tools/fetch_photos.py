#!/usr/bin/env python3
"""Peakle 사진 자동 처리 (GitHub Actions에서 실행)

1) mountains.js 에 들어 있는 모든 사진(대표 + 예비)을 위키미디어에서 내려받아
   가로 최대 800px JPEG 로 줄여 img/ 폴더에 저장 (이미 있는 건 건너뜀)
2) 위키미디어 Commons API 로 사진마다 촬영자(Artist)와 라이선스를 수집
3) 결과를 photos.js 로 저장 → 게임이 사이트 안의 사진을 먼저 쓰고, 정답 공개 후 출처를 표시

위키미디어 정책에 맞게 User-Agent 를 밝히고, 요청 사이에 쉬는 시간을 둠.
"""
import hashlib, html, io, json, os, re, sys, time, urllib.parse

import requests
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMG_DIR = os.path.join(ROOT, "img")
OUT_JS = os.path.join(ROOT, "photos.js")
UA = "PeakleBot/1.0 (https://github.com/Leesu1026/peakle; photo credits and thumbnails)"
API = "https://commons.wikimedia.org/w/api.php"
MAX_W = 800
S = requests.Session()
S.headers["User-Agent"] = UA


def load_mountains():
    s = open(os.path.join(ROOT, "mountains.js"), encoding="utf-8").read()
    m = re.search(r"window\.MOUNTAINS\s*=\s*(\[.*?\]);\s*window\.MOUNTAINS_VERSION", s, re.S)
    return json.loads(m.group(1))


def thumb_url(path):
    """index.html 의 photoURLs() 와 같은 규칙"""
    t = re.match(r"^(.*)/([^/]+)@(\d+)$", path)
    if t:
        return f"https://upload.wikimedia.org/wikipedia/commons/thumb/{t.group(1)}/{t.group(2)}/{t.group(3)}px-{t.group(2)}", t.group(2)
    return "https://upload.wikimedia.org/wikipedia/commons/" + path, path.split("/")[-1]


def local_name(path):
    return "img/" + hashlib.sha1(path.encode("utf-8")).hexdigest()[:16] + ".jpg"


def get(url, **kw):
    for attempt in range(6):
        try:
            r = S.get(url, timeout=60, **kw)
            if r.status_code == 200:
                return r
            if r.status_code in (429, 500, 502, 503, 504):
                time.sleep(5 * (attempt + 1)); continue
            print("  HTTP", r.status_code, url[:120]); return None
        except requests.RequestException as e:
            print("  error", e); time.sleep(5 * (attempt + 1))
    return None


def download_all(paths, old):
    os.makedirs(IMG_DIR, exist_ok=True)
    out, fails = {}, []
    for i, p in enumerate(paths, 1):
        name = local_name(p)
        full = os.path.join(ROOT, name)
        if os.path.exists(full) and p in old:
            out[p] = old[p]; continue
        url, _ = thumb_url(p)
        r = get(url)
        if not r:
            fails.append(p); continue
        try:
            im = Image.open(io.BytesIO(r.content))
            im = im.convert("RGB")
            if im.width > MAX_W:
                im = im.resize((MAX_W, round(im.height * MAX_W / im.width)), Image.LANCZOS)
            im.save(full, "JPEG", quality=80, optimize=True, progressive=True)
            out[p] = [name, im.width, im.height]
        except Exception as e:  # 깨진 파일
            print("  bad image", p, e); fails.append(p)
        if i % 50 == 0:
            print(f"  사진 {i}/{len(paths)}")
        time.sleep(0.3)
    return out, fails


def clean(v):
    v = re.sub(r"<[^>]+>", " ", v or "")
    v = html.unescape(v)
    return re.sub(r"\s+", " ", v).strip()[:120]


def fetch_credits(files):
    """files: 위키미디어 파일 이름(디코딩된 형태, 밑줄 포함)"""
    out = {}
    for i in range(0, len(files), 50):
        chunk = files[i:i + 50]
        data = {"action": "query", "format": "json", "formatversion": "2", "prop": "imageinfo",
                "iiprop": "extmetadata", "iiextmetadatafilter": "Artist|LicenseShortName|LicenseUrl",
                "titles": "|".join("File:" + f for f in chunk)}
        for attempt in range(6):
            try:
                r = S.post(API, data=data, timeout=60)
                if r.status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(5 * (attempt + 1))
        else:
            print("  credits batch failed", i); continue
        j = r.json().get("query", {})
        back = {}  # API 가 정규화한 제목 → 원래 이름
        for n in j.get("normalized", []):
            back[n["to"]] = n["from"]
        for pg in j.get("pages", []):
            title = pg.get("title", "")
            orig = back.get(title, title)
            fname = orig[5:] if orig.startswith("File:") else orig
            fname = fname.replace(" ", "_")
            info = (pg.get("imageinfo") or [{}])[0].get("extmetadata", {})
            artist = clean(info.get("Artist", {}).get("value"))
            lic = clean(info.get("LicenseShortName", {}).get("value"))
            url = clean(info.get("LicenseUrl", {}).get("value"))
            if artist or lic:
                out[fname] = [artist, lic, url]
        print(f"  출처 {min(i + 50, len(files))}/{len(files)}")
        time.sleep(1)
    return out


def read_old():
    if not os.path.exists(OUT_JS):
        return {}, {}
    s = open(OUT_JS, encoding="utf-8").read()
    p = re.search(r"window\.PHOTOS\s*=\s*(\{.*?\});", s, re.S)
    c = re.search(r"window\.CREDITS\s*=\s*(\{.*?\});", s, re.S)
    return (json.loads(p.group(1)) if p else {}), (json.loads(c.group(1)) if c else {})


def main():
    mts = load_mountains()
    paths = []
    for m in mts:
        for p, _r in m[5]:
            if p not in paths:
                paths.append(p)
    print("사진 수:", len(paths))
    old_photos, old_credits = read_old()
    photos, fails = download_all(paths, old_photos)
    files = sorted({urllib.parse.unquote(thumb_url(p)[1]) for p in paths})
    need = [f for f in files if f not in old_credits]
    credits = dict(old_credits)
    credits.update(fetch_credits(need))
    # 더 이상 쓰지 않는 사진 파일 정리
    keep = {v[0] for v in photos.values()}
    for f in os.listdir(IMG_DIR):
        if "img/" + f not in keep:
            os.remove(os.path.join(IMG_DIR, f))
    with open(OUT_JS, "w", encoding="utf-8") as fp:
        fp.write("/* 자동 생성 파일 (tools/fetch_photos.py) — 직접 수정하지 마세요 */\n")
        fp.write("window.PHOTOS=" + json.dumps(photos, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + ";\n")
        fp.write("window.CREDITS=" + json.dumps({k: credits[k] for k in sorted(credits) if k in set(files)},
                                                 ensure_ascii=False, separators=(",", ":")) + ";\n")
    print(f"완료: 사진 {len(photos)}/{len(paths)}, 출처 {len([f for f in files if f in credits])}/{len(files)}, 실패 {len(fails)}")
    for p in fails[:30]:
        print("  실패:", p)
    # 실패가 너무 많으면 작업을 실패로 표시 (그래도 받은 것은 저장됨)
    if len(photos) < len(paths) * 0.8:
        sys.exit(1)


if __name__ == "__main__":
    main()
