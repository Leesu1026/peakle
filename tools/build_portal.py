#!/usr/bin/env python3
"""게임 포털(CrazyGames 등)에 올릴 zip 만들기

  python tools/build_portal.py crazygames      → dist/peakle-crazygames.zip

포털 버전의 차이:
  - window.PORTAL 표시 → 서비스워커 끔, 바깥 사이트 링크 숨김 (사진 출처는 글자로 표시 + 출처 페이지 포함)
  - 우리 광고(AdSense)는 넣지 않음 (포털은 자기 광고 SDK 만 허용)
"""
import os, re, sys, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
name = sys.argv[1] if len(sys.argv) > 1 else "crazygames"
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", f"peakle-{name}.zip")

html = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
html = html.replace('<script src="mountains.js"></script>', f'<script>window.PORTAL="{name}";</script>\n<script src="mountains.js"></script>', 1)
html = re.sub(r'\s*<link rel="manifest"[^>]*>', "", html)
assert 'window.PORTAL="' in html

files = ["mountains.js", "photos.js", "credits.html", "icon-192.png", "icon-512.png"]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("index.html", html)
    for f in files:
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            z.write(p, f)
    img = os.path.join(ROOT, "img")
    if os.path.isdir(img):
        for f in sorted(os.listdir(img)):
            z.write(os.path.join(img, f), "img/" + f)
n = len(zipfile.ZipFile(out).namelist())
print(f"{out}  ({os.path.getsize(out) / 1e6:.1f} MB, 파일 {n}개)")
