#!/usr/bin/env python3
"""Peakle 쇼츠(세로 영상) 자동 제작기

사용 예:
  python tools/make_short.py --index 12 --lang en --out shorts/out.mp4
  python tools/make_short.py --date 2026-11-02 --lang en --out shorts/2026-11-02.mp4   # 날짜로 건축물 자동 선택

영상 구성 (약 11초, 1080x1920, 30fps):
  0.0~5.5초  사진 + "이 건물의 높이는?" + 5초 카운트다운 (째깍 소리)
  5.5~8.5초  정답 공개: 이름 + 높이 숫자가 올라가는 애니메이션 (효과음)
  8.5~11초   "더 가깝게 맞힐 수 있나요? → 무료로 플레이" + 게임 주소
음악은 쓰지 않고 직접 합성한 효과음만 사용 (저작권 문제 없음).
사진 출처(촬영자·라이선스)는 --credit-out 파일로 따로 써서 영상 설명란에 넣음.
"""
import argparse, datetime, hashlib, json, math, os, re, struct, subprocess, sys, tempfile, wave

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H, FPS, DUR = 1080, 1920, 30, 11.0
T_REVEAL, T_CTA = 5.5, 8.5
BG0, BG1 = (11, 16, 38), (23, 36, 74)
ORANGE, SNOW, DIM = (255, 142, 110), (242, 246, 249), (143, 163, 191)

TXT = {
    "en": {"q": "How tall is this?", "cta1": "Think you can guess closer?", "cta2": "Play free", "cta3": "Daily challenge · 1v1 battles",
           "cats": {1: "Building", 2: "Tower", 3: "Bridge", 4: "Monument / Statue", 5: "Religious", 6: "Stadium / Structure"}},
    "ko": {"q": "이 건축물의 높이는?", "cta1": "더 가깝게 맞힐 수 있나요?", "cta2": "무료로 플레이", "cta3": "데일리 챌린지 · 1대1 대결",
           "cats": {1: "건물", 2: "타워", 3: "다리", 4: "기념물·동상", 5: "종교 건축", 6: "경기장·구조물"}},
    "es": {"q": "¿Cuánto mide?", "cta1": "¿Puedes acercarte más?", "cta2": "Juega gratis", "cta3": "Reto diario · Duelos 1 vs 1",
           "cats": {1: "Edificio", 2: "Torre", 3: "Puente", 4: "Monumento / Estatua", 5: "Edificio religioso", 6: "Estadio / Estructura"}},
    "pt": {"q": "Qual é a altura?", "cta1": "Consegue chegar mais perto?", "cta2": "Jogue grátis", "cta3": "Desafio diário · Duelos 1 x 1",
           "cats": {1: "Edifício", 2: "Torre", 3: "Ponte", 4: "Monumento / Estátua", 5: "Edifício religioso", 6: "Estádio / Estrutura"}},
    "ja": {"q": "この高さは?", "cta1": "もっと近づけられる?", "cta2": "無料でプレイ", "cta3": "デイリーチャレンジ · 1対1バトル",
           "cats": {1: "ビル", 2: "タワー", 3: "橋", 4: "記念碑・像", 5: "宗教建築", 6: "競技場・構造物"}},
}

FONT_FILES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def font(size, lang="en"):
    idx = {"ko": 1, "ja": 0, "zh": 2}.get(lang, 1)  # NotoSansCJK ttc: 0=JP 1=KR 2=SC 3=TC 4=HK
    for f in FONT_FILES:
        if os.path.exists(f):
            try:
                return ImageFont.truetype(f, size, index=idx if f.endswith(".ttc") else 0)
            except Exception:
                continue
    return ImageFont.load_default()


def load_data():
    s = open(os.path.join(ROOT, "mountains.js"), encoding="utf-8").read()
    m = re.search(r"window\.MOUNTAINS\s*=\s*(\[.*?\]);\s*window\.MOUNTAINS_VERSION", s, re.S)
    mts = json.loads(m.group(1))
    c = re.search(r"window\.COUNTRY_EN\s*=\s*(\{.*?\});", s, re.S)
    countries = json.loads(c.group(1)) if c else {}
    photos, credits = {}, {}
    pj = os.path.join(ROOT, "photos.js")
    if os.path.exists(pj):
        t = open(pj, encoding="utf-8").read()
        p = re.search(r"window\.PHOTOS=(\{.*?\});", t, re.S)
        q = re.search(r"window\.CREDITS=(\{.*?\});", t, re.S)
        photos = json.loads(p.group(1)) if p else {}
        credits = json.loads(q.group(1)) if q else {}
    return mts, countries, photos, credits


# ---- 데일리 문제와 겹치지 않게: index.html 의 mulberry32 / pickBalanced 를 그대로 옮김 ----
def _imul(a, b):
    return (a * b) & 0xFFFFFFFF


def mulberry32(a):
    st = [a & 0xFFFFFFFF]

    def rnd():
        st[0] = (st[0] + 0x6D2B79F5) & 0xFFFFFFFF
        a = st[0]
        t = _imul(a ^ (a >> 15), 1 | a)
        t = (((t + _imul(t ^ (t >> 7), 61 | t)) & 0xFFFFFFFF) ^ t) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296
    return rnd


def daily_set(mts, dn):
    catidx = {}
    for i, m in enumerate(mts):
        catidx.setdefault(m[4] or 0, []).append(i)
    keys = sorted(catidx)          # JS Object.keys 는 숫자 키를 오름차순으로 돌려줌
    rnd = mulberry32(dn * 7919 + 13)
    out = []
    while len(out) < 5:
        arr = catidx[keys[int(rnd() * len(keys))]]
        v = arr[int(rnd() * len(arr))]
        if v not in out:
            out.append(v)
    return out


def daily_num(d):
    return (d - datetime.date(2026, 7, 16)).days + 1


def pick_by_date(mts, date_str, has_photo=lambda i: True):
    """날짜마다 정해진 건축물. 앞으로 60일 동안의 데일리 문제와 사진 없는 건축물은 피함."""
    d = datetime.date.fromisoformat(date_str)
    avoid = set()
    for k in range(-1, 60):
        avoid.update(daily_set(mts, daily_num(d) + k))
    order = sorted(range(len(mts)), key=lambda i: hashlib.sha1(f"peakle-shorts-{i}".encode()).hexdigest())
    k = (d - datetime.date(2026, 1, 1)).days
    for step in range(len(order)):
        i = order[(k + step) % len(order)]
        if i not in avoid and has_photo(i):
            return i
    return order[k % len(order)]


def gradient():
    img = Image.new("RGB", (W, H))
    dr = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        dr.line([(0, y), (W, y)], fill=tuple(round(BG0[k] + (BG1[k] - BG0[k]) * t) for k in range(3)))
    return img


def fit_text(dr, text, max_w, size, lang, min_size=36):
    while size > min_size:
        f = font(size, lang)
        if dr.textlength(text, font=f) <= max_w:
            return f
        size -= 4
    return font(min_size, lang)


def center(dr, y, text, f, fill):
    w = dr.textlength(text, font=f)
    dr.text(((W - w) / 2, y), text, font=f, fill=fill)


def rounded(img, r):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], r, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img, (0, 0), mask)
    return out


def logo(dr, y, lang):
    f = font(64, lang)
    parts = [("PE", SNOW), ("A", ORANGE), ("KLE", SNOW)]
    total = sum(dr.textlength(p, font=f) for p, _ in parts) + 8 * 4
    x = (W - total) / 2
    for p, col in parts:
        dr.text((x, y), p, font=f, fill=col)
        x += dr.textlength(p, font=f) + 8


def build_base(photo_img, lang, cat_country):
    base = gradient()
    # 흐린 사진 배경 (위쪽 은은하게)
    bg = photo_img.copy().convert("RGB")
    bg = bg.resize((W, round(bg.height * W / bg.width)))
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(Image.new("RGB", bg.size, BG0), bg, 0.35)
    crop = bg.crop((0, 0, W, min(bg.height, 1500)))
    fade = Image.new("L", crop.size, 255)   # 아래쪽은 배경색으로 자연스럽게 사라지게
    fd = ImageDraw.Draw(fade)
    for y in range(max(0, crop.height - 400), crop.height):
        fd.line([(0, y), (W, y)], fill=round(255 * (crop.height - y) / 400))
    base.paste(crop, (0, 0), fade)
    dr = ImageDraw.Draw(base)
    logo(dr, 110, lang)
    center(dr, 240, TXT[lang]["q"], fit_text(dr, TXT[lang]["q"], 980, 84, lang), SNOW)
    # 사진 (최대 1000x1000 안에 맞춤)
    box_w, box_h, top = 1000, 1000, 400
    ph = photo_img.convert("RGB")
    sc = min(box_w / ph.width, box_h / ph.height)
    fit_w, fit_h = round(ph.width * sc), round(ph.height * sc)
    if fit_w < box_w * 0.8:
        # 세로로 긴 사진: 게임처럼 같은 사진을 흐리게 깔아 빈 곳을 채우고, 원본은 잘리지 않게 가운데에 전체 표시
        cov = max(box_w / ph.width, box_h / ph.height)
        bgp = ph.resize((round(ph.width * cov), round(ph.height * cov)), Image.LANCZOS)
        l, t2 = (bgp.width - box_w) // 2, (bgp.height - box_h) // 2
        bgp = bgp.crop((l, t2, l + box_w, t2 + box_h)).filter(ImageFilter.GaussianBlur(28))
        bgp = Image.blend(bgp, Image.new("RGB", bgp.size, BG0), 0.35)
        bgp = rounded(bgp, 28)
        base.paste(bgp, ((W - box_w) // 2, top), bgp)
    ph = ph.resize((fit_w, fit_h), Image.LANCZOS)
    ph = rounded(ph, 28)
    px, py = (W - ph.width) // 2, top + (box_h - ph.height) // 2
    base.paste(ph, (px, py), ph)
    center(dr, 1430, cat_country, fit_text(dr, cat_country, 980, 44, lang), DIM)
    return base


def frame(base, t, lang, name, height_m, url):
    img = base.copy()
    dr = ImageDraw.Draw(img)
    if t < T_REVEAL:
        left = max(0.0, T_REVEAL - 0.5 - t)
        n = math.ceil(left) if left > 0 else 0
        if n > 0:
            center(dr, 1540, str(n), font(200, lang), ORANGE)
        # 줄어드는 막대
        frac = max(0.0, min(1.0, (T_REVEAL - t) / T_REVEAL))
        bw = 900
        dr.rounded_rectangle([(W - bw) / 2, 1820, (W + bw) / 2, 1836], 8, fill=(40, 52, 90))
        dr.rounded_rectangle([(W - bw) / 2, 1820, (W - bw) / 2 + bw * frac, 1836], 8, fill=ORANGE)
    else:
        k = min(1.0, (t - T_REVEAL) / 1.0)
        k = 1 - (1 - k) ** 3  # 부드럽게 멈추는 증가
        val = round(height_m * k)
        if t < T_CTA + 0.4:   # 안내 화면이 완전히 덮기 전까지만
            center(dr, 1510, name, fit_text(dr, name, 980, 64, lang, 32), SNOW)
            center(dr, 1600, f"{val:,} m", font(170, lang), ORANGE)
        if t >= T_CTA:
            a = min(1.0, (t - T_CTA) / 0.4)
            ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(ov)
            od.rounded_rectangle([60, 1240, W - 60, 1880], 40, fill=(11, 16, 38, round(250 * a)))
            img.paste(ov, (0, 0), ov)
            dr = ImageDraw.Draw(img)
            col = lambda c: tuple(round(c[i] * a + BG0[i] * (1 - a)) for i in range(3))
            center(dr, 1300, TXT[lang]["cta1"], fit_text(dr, TXT[lang]["cta1"], 900, 64, lang), col(SNOW))
            center(dr, 1410, f"{name} = {height_m:,} m", fit_text(dr, f"{name} = {height_m:,} m", 900, 48, lang, 28), col(ORANGE))
            center(dr, 1540, "▶ " + TXT[lang]["cta2"], font(72, lang), col(SNOW))
            center(dr, 1650, url, fit_text(dr, url, 900, 52, lang, 28), col(ORANGE))
            center(dr, 1760, TXT[lang]["cta3"], fit_text(dr, TXT[lang]["cta3"], 900, 40, lang), col(DIM))
    return img


def make_audio(path):
    sr = 44100
    n = int(sr * DUR)
    buf = [0.0] * n

    def tone(start, dur, freqs, vol):
        s0 = int(start * sr)
        for i in range(int(dur * sr)):
            j = s0 + i
            if j >= n:
                break
            env = math.exp(-6 * i / (dur * sr))
            buf[j] += vol * env * sum(math.sin(2 * math.pi * f * i / sr) for f in freqs) / len(freqs)

    for s in range(5):  # 째깍
        tone(0.5 + s, 0.07, [1200], 0.35)
    tone(T_REVEAL, 0.9, [523.25, 659.25, 783.99], 0.45)  # 정답 화음
    tone(T_CTA, 0.25, [880], 0.18)
    with wave.open(path, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(b"".join(struct.pack("<h", max(-32767, min(32767, int(v * 32767)))) for v in buf))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", type=int)
    ap.add_argument("--date")
    ap.add_argument("--lang", default="en", choices=sorted(TXT))
    ap.add_argument("--url", default="leesu1026.github.io/peakle")
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta-out", help="제목·설명·출처를 담은 JSON 저장 위치")
    a = ap.parse_args()

    mts, countries, photos, credits = load_data()
    def has_photo(i):
        return any(p in photos and os.path.exists(os.path.join(ROOT, photos[p][0])) for p, _r in mts[i][5])
    idx = a.index if a.index is not None else pick_by_date(mts, a.date or datetime.date.today().isoformat(), has_photo)
    m = mts[idx]
    name_en, iso, height, name_ko, cat = m[0], m[1], m[2], m[3], m[4]
    name = name_ko if (a.lang == "ko" and name_ko) else name_en
    country = countries.get(iso, iso)
    cat_country = f"{TXT[a.lang]['cats'].get(cat, '')} · {country}"

    # 게임과 똑같은 실제 사진(위키미디어에서 받아 img/ 에 저장한 것)만 사용. 다른 그림은 절대 쓰지 않음.
    src, file_enc = None, None
    for p, _r in m[5]:
        mm = re.match(r"^(.*)/([^/]+)@(\d+)$", p)
        file_enc = mm.group(2) if mm else p.split("/")[-1]
        if not src and p in photos and os.path.exists(os.path.join(ROOT, photos[p][0])):
            src = os.path.join(ROOT, photos[p][0])
        if src:
            break
    if not src:
        sys.exit("이 건축물의 실제 사진이 아직 없어요. 먼저 Photos 작업으로 img/ 에 사진을 받아야 해요.")
    photo = Image.open(src)

    base = build_base(photo, a.lang, cat_country)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "a.wav")
        make_audio(wav)
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
               "-i", "-", "-i", wav, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "21",
               "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", a.out]
        pr = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        for f in range(int(DUR * FPS)):
            pr.stdin.write(frame(base, f / FPS, a.lang, name, height, a.url).convert("RGB").tobytes())
        pr.stdin.close()
        if pr.wait() != 0:
            sys.exit("ffmpeg 실패")

    file_dec = file_enc
    try:
        import urllib.parse
        file_dec = urllib.parse.unquote(file_enc)
    except Exception:
        pass
    cr = credits.get(file_dec)
    credit_line = f"Photo: {cr[0] or 'Unknown'} / {cr[1]} / Wikimedia Commons" if cr else "Photo: Wikimedia Commons"
    meta = {
        "index": idx, "name": name, "height_m": height,
        "title": {"en": "Can you guess how tall this is? 🏙️ #shorts", "ko": "이 건축물 높이, 맞힐 수 있나요? 🏙️ #shorts",
                  "es": "¿Adivinas cuánto mide? 🏙️ #shorts", "pt": "Consegue adivinhar a altura? 🏙️ #shorts",
                  "ja": "この高さ、当てられる? 🏙️ #shorts"}[a.lang],
        "description": f"Guess first, then check! Play free: https://{a.url}\n\n{credit_line}\nhttps://commons.wikimedia.org/wiki/File:{file_enc}\n\n#peakle #architecture #guessinggame #shorts",
        "credit": credit_line,
    }
    if a.meta_out:
        json.dump(meta, open(a.meta_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False))


if __name__ == "__main__":
    main()
