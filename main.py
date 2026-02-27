import os, uuid, re, asyncio, httpx, json
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request
import shutil, numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

app = FastAPI(title="AutoVis AI", version="4.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE   = Path(__file__).parent
UPLOAD = BASE / "uploads";  UPLOAD.mkdir(exist_ok=True)
OUTPUT = BASE / "outputs";  OUTPUT.mkdir(exist_ok=True)
STATIC = BASE / "static";   STATIC.mkdir(exist_ok=True)
(BASE / "assets" / "music").mkdir(parents=True, exist_ok=True)

app.mount("/static",  StaticFiles(directory=str(STATIC)),  name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT)),  name="outputs")
templates = Jinja2Templates(directory=str(BASE / "templates"))

W, H, FPS = 1080, 1920, 24

FONT_PATHS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
FONT_PATH = next((f for f in FONT_PATHS if Path(f).exists()), None)
print(f"[Startup] Font: {FONT_PATH}")
print(f"[Startup] OpenCV: {cv2.__version__}")

jobs: dict = {}
def upd(jid, **kw):
    if jid in jobs: jobs[jid].update(kw)

AVATARS = [
    {"id":"Abigail_expressive_2024112501","name":"Abigail","emoji":"👩","style":"Tre trung"},
    {"id":"Angela-inblackskirt-20220820","name":"Angela","emoji":"👩‍💼","style":"Chuyen nghiep"},
    {"id":"Anna_public_3_20240108","name":"Anna","emoji":"🧑‍🦰","style":"Than thien"},
    {"id":"Emily-inpinkskirt-20220820","name":"Emily","emoji":"💃","style":"Nang dong"},
    {"id":"Susan-inbluetshirt-20220821","name":"Susan","emoji":"🙋‍♀️","style":"Tu nhien"},
    {"id":"Lily-inpinkskirt-20220822","name":"Lily","emoji":"🌸","style":"Diu dang"},
]
VOICES = [
    {"id":"vi-VN-HoaiMyNeural","name":"Hoai My - Nu mien Nam (Khuyen nghi)"},
    {"id":"vi-VN-NamMinhNeural","name":"Nam Minh - Giong nam"},
    {"id":"vi-VN-Standard-A","name":"Giong nu chuan Viet"},
]

PLATFORM_HINTS = {
    "shopee.vn":"Shopee","lazada.vn":"Lazada","tiki.vn":"Tiki",
    "tiktok.com":"TikTok Shop","sendo.vn":"Sendo",
}
AGE_MAP = {
    "so sinh":("0-12 thang","newborn"),"0-1":("0-12 thang","newborn"),
    "1-3":("1-3 tuoi","toddler"),"toddler":("1-3 tuoi","toddler"),
    "4-6":("4-6 tuoi","preschool"),"mam non":("4-6 tuoi","preschool"),
    "7-10":("7-10 tuoi","school"),"tieu hoc":("7-10 tuoi","school"),
}

async def analyze_product(url: str) -> dict:
    try:
        headers = {"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit/605.1.15 Mobile Safari/604.1"}
        async with httpx.AsyncClient(timeout=18, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            html = r.text
        platform = "Website"
        for domain, name in PLATFORM_HINTS.items():
            if domain in url: platform = name; break
        t = re.search(r'<title[^>]*>(.*?)</title>', html, re.I|re.S)
        title = re.sub(r'\s+', ' ', t.group(1)).strip()[:100] if t else "San pham"
        pm = re.search(r'(\d[\d\.,]+)\s*(?:d|VND|vnd|dong)', html, re.I)
        price = pm.group(0) if pm else ""
        imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
        img_url = imgs[0] if imgs else ""
        local_img = None
        if img_url:
            try:
                async with httpx.AsyncClient(timeout=12) as c:
                    ir = await c.get(img_url, headers=headers)
                    if ir.status_code == 200 and len(ir.content) > 1000:
                        p = UPLOAD / f"sc_{uuid.uuid4().hex}.jpg"
                        p.write_bytes(ir.content)
                        local_img = str(p)
            except: pass
        text_lower = title.lower()
        gender = "be gai" if any(w in text_lower for w in ["gai","girl","vay","dam","hong"]) \
            else "be trai" if any(w in text_lower for w in ["trai","boy","xanh"]) else "be"
        age_label, age_key = "1-3 tuoi", "toddler"
        for kw, (lbl, key) in AGE_MAP.items():
            if kw in text_lower: age_label, age_key = lbl, key; break
        return {"title":title,"price":price,"platform":platform,"img_url":img_url,
                "local_img":local_img,"gender":gender,"age_label":age_label,
                "age_key":age_key,"style":"cute","source_url":url}
    except:
        return {"title":"San pham thoi trang","price":"","platform":"Shopee","img_url":"",
                "local_img":None,"gender":"be","age_label":"1-3 tuoi","age_key":"toddler",
                "style":"cute","source_url":url}

def analyze_image_locally(img_path: str) -> dict:
    return {"title":"Thoi trang be yeu","price":"","platform":"Upload","img_url":"",
            "local_img":img_path,"gender":"be","age_label":"1-3 tuoi",
            "age_key":"toddler","style":"cute","source_url":""}

SCRIPTS = {
    "newborn":["Oi cac me oi! {title} sieu cute cho be so sinh! Chat vai 100% cotton mem mai. {price_text}Giao hang toan quoc nhe!"],
    "toddler":["Cac me oi xem {title} nay xinh khong! Phu hop {gender} {age_label}, chat vai thoang mat. {price_text}Dung bo lo nhe!",
               "Oi cute qua di! {title} hot trend 2025! Be mac vao dep ngay. {price_text}Binh luan GIA de bao ngay!"],
    "preschool":["Me bim dang tim do cho be {age_label}? {title} la lua chon hoan hao! {price_text}Giao nhanh, doi tra de!"],
    "school":["{title} cho {gender} {age_label}. Vai ben dep, co gian tot. {price_text}Dat ngay keo het size!"],
}

def make_script(p: dict) -> str:
    import random
    tpl = random.choice(SCRIPTS.get(p.get("age_key","toddler"), SCRIPTS["toddler"]))
    pr = p.get("price","")
    return tpl.format(title=p.get("title","")[:40], gender=p.get("gender","be"),
        age_label=p.get("age_label","1-3 tuoi"), style=p.get("style","cute"),
        price_text=f"Gia chi {pr}! " if pr else "Gia cuc hap dan! ")

def make_content(p: dict) -> dict:
    t = (p.get("title") or "Thoi trang be")[:40]
    pr = p.get("price","")
    g = p.get("gender","be")
    age = p.get("age_label","")
    platform = p.get("platform","")
    pstr = f"\nGia chi {pr}" if pr else ""
    return {
        "captions":[
            f"Be {t}{pstr}\nChat vai mem mai, an toan cho {g}\nGiao toan quoc - Doi tra de dang\nBinh luan GIA de dat hang ngay!",
            f"HOT TREND - {t}{pstr}\nPhu hop {g} {age}\nChinh hang tu {platform}\nLink mua trong bio!",
            f"Cute qua cac me oi!\n{t}{pstr}\nNhan tin ngay de tu van mien phi!",
        ],
        "hashtags":[
            "#thoitrangtreem #mevabe #beyeu #tiktokshop #sanphamhot #muahang #trending #viral #cute",
            f"#thoitrangbe #dotreem #{g.replace(' ','')} #baby #kids #fashion #shopee #lazada #affiliate",
            "#reviewsanpham #unboxing #recommend #chinhang #giaonhanh #sale #tiktok #fyp",
        ]
    }

def get_font(size: int):
    if FONT_PATH:
        try: return ImageFont.truetype(FONT_PATH, size)
        except: pass
    return ImageFont.load_default()

def draw_frame(bg_img_rgb: np.ndarray, title: str, price_line: str, sub: str, cta: str) -> np.ndarray:
    img = Image.fromarray(bg_img_rgb).convert("RGBA")
    img = img.resize((W, H), Image.LANCZOS)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov = ImageDraw.Draw(overlay)
    ov.rectangle([(0, 0), (W, 290)], fill=(10, 5, 30, 185))
    ov.rectangle([(0, H - 160), (W, H)], fill=(10, 5, 30, 185))
    img = Image.alpha_composite(img, overlay).convert("RGB")
    draw = ImageDraw.Draw(img)
    y = 30
    for text, size, color in [(title, 42, (255,255,255)), (price_line, 34, (255,215,0)), (sub, 26, (0,220,255))]:
        if not text: continue
        font = get_font(size)
        max_w = W - 60
        while True:
            bb = draw.textbbox((0, 0), text, font=font)
            tw = bb[2] - bb[0]
            if tw <= max_w or font.size <= 18: break
            font = get_font(font.size - 2)
        x = max(30, (W - tw) // 2)
        draw.text((x+2, y+2), text, font=font, fill=(0,0,0,200))
        draw.text((x, y), text, font=font, fill=color)
        y += font.size + 14
    if cta:
        font_cta = get_font(30)
        bb = draw.textbbox((0,0), cta, font=font_cta)
        tw = bb[2]-bb[0]
        x = (W-tw)//2
        draw.text((x+2, H-128+2), cta, font=font_cta, fill=(0,0,0))
        draw.text((x, H-128), cta, font=font_cta, fill=(100,255,100))
    watermark = get_font(18)
    draw.text((W-160, H-38), "AutoVis AI", font=watermark, fill=(255,255,255,100))
    return np.array(img)

def load_image_rgb(path: str) -> Optional[np.ndarray]:
    try:
        img = Image.open(path).convert("RGB")
        return np.array(img)
    except:
        return None

async def make_cv_video(images: List[str], p: dict, jid: str) -> str:
    out = OUTPUT / f"video_{jid}.mp4"
    title = p.get("title","San pham")[:35] or "San pham"
    price = p.get("price","")
    gender = p.get("gender","be")
    age = p.get("age_label","")
    price_line = f"Gia: {price}" if price else "Gia sieu hot!"
    sub = f"{gender} {age}".strip()
    cta = "Dat hang ngay!"

    def encode_video(frames_rgb: List[np.ndarray], fps: int, path: Path):
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        vw = cv2.VideoWriter(str(path), fourcc, fps, (W, H))
        for f in frames_rgb:
            vw.write(cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
        vw.release()

    loaded = [load_image_rgb(img) for img in images if img and Path(img).exists()]
    loaded = [img for img in loaded if img is not None]

    if not loaded:
        bg = np.zeros((H, W, 3), dtype=np.uint8)
        bg[:] = (26, 10, 46)
        loaded = [bg]

    frames = []
    n = len(loaded)
    sec_per = max(3, 20 // n)

    for bg_img in loaded:
        frame = draw_frame(bg_img, title, price_line, sub, cta)
        for _ in range(FPS * sec_per):
            frames.append(frame)

    await asyncio.to_thread(encode_video, frames, FPS, out)
    if out.exists() and out.stat().st_size > 1000:
        return str(out)
    return ""

async def heygen_upload(path: str, key: str) -> Optional[str]:
    try:
        data = open(path,"rb").read()
        ext = Path(path).suffix.lower().lstrip(".") or "jpeg"
        mime = f"image/{ext}" if ext in ["jpg","jpeg","png","webp"] else "image/jpeg"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://upload.heygen.com/v1/asset",
                headers={"x-api-key":key,"Content-Type":mime}, content=data)
            d = r.json()
            return d.get("data",{}).get("id") or d.get("id")
    except: return None

async def heygen_create(key, script, avatar, voice, bg_id) -> Optional[str]:
    bg = {"type":"image","url":f"https://resource.heygen.com/image/{bg_id}"} if bg_id else {"type":"color","value":"#FFF5F9"}
    payload = {"video_inputs":[{"character":{"type":"avatar","avatar_id":avatar,"avatar_style":"normal"},
        "voice":{"type":"text","input_text":script,"voice_id":voice,"speed":1.0},"background":bg}],
        "dimension":{"width":1080,"height":1920},"test":False}
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.heygen.com/v2/video/generate",
                headers={"X-Api-Key":key,"Content-Type":"application/json"}, json=payload)
            d = r.json()
            return d.get("data",{}).get("video_id") or d.get("video_id")
    except: return None

async def heygen_poll(key, vid, jid) -> Optional[str]:
    for i in range(80):
        await asyncio.sleep(8)
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"https://api.heygen.com/v1/video_status.get?video_id={vid}",
                    headers={"X-Api-Key":key})
                d = r.json(); st = d.get("data",{}).get("status",""); url = d.get("data",{}).get("video_url","")
                upd(jid, step=f"HeyGen dang render... ({(i+1)*8}s)", progress=min(92,48+i))
                if st == "completed" and url: return url
                if st == "failed": return None
        except: pass
    return None

async def heygen_download(url, jid) -> str:
    try:
        out = OUTPUT / f"video_{jid}.mp4"
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200:
                out.write_bytes(r.content); return str(out)
    except: pass
    return ""

async def process(jid, product_url, img_paths, api_key, avatar_id, voice_id, duration):
    try:
        upd(jid, status="processing", step="Dang phan tich san pham...", progress=5)
        if product_url:
            upd(jid, step="Dang tai thong tin tu link...", progress=12)
            p = await analyze_product(product_url)
            if p.get("local_img") and not img_paths:
                img_paths = [p["local_img"]]
        elif img_paths:
            upd(jid, step="Dang phan tich hinh anh...", progress=12)
            p = analyze_image_locally(img_paths[0])
        else:
            p = {"title":"San pham","price":"","platform":"","gender":"be",
                 "age_label":"1-3 tuoi","age_key":"toddler","style":"cute","local_img":None}

        upd(jid, step="Dang tao script...", progress=22,
            product_info={"title":p.get("title",""),"price":p.get("price",""),
                         "gender":p.get("gender",""),"age":p.get("age_label",""),"platform":p.get("platform","")})
        await asyncio.sleep(0.2)
        script = make_script(p)
        content = make_content(p)

        video_path, used_heygen = "", False

        if api_key:
            bg_id = None
            if img_paths and Path(img_paths[0]).exists():
                upd(jid, step="Dang upload anh len HeyGen...", progress=30)
                bg_id = await heygen_upload(img_paths[0], api_key)
            upd(jid, step="Dang tao nguoi mau AI...", progress=40)
            vid_id = await heygen_create(api_key, script, avatar_id, voice_id, bg_id)
            if vid_id:
                upd(jid, step="HeyGen dang render...", progress=48)
                vid_url = await heygen_poll(api_key, vid_id, jid)
                if vid_url:
                    upd(jid, step="Dang tai video...", progress=94)
                    video_path = await heygen_download(vid_url, jid)
                    used_heygen = bool(video_path)

        if not video_path:
            n = len(img_paths)
            upd(jid, step=f"Dang tao video voi {n} anh..." if n else "Dang tao video...", progress=60)
            video_path = await make_cv_video(img_paths, p, jid)

        fn = Path(video_path).name if video_path else ""
        upd(jid, status="done", step="Hoan tat!", progress=100,
            result={"video_url":f"/outputs/{fn}" if fn else "","video_filename":fn,
                   "product":p,"script":script,"captions":content["captions"],
                   "hashtags":content["hashtags"],"used_heygen":used_heygen})
    except Exception as e:
        print(f"[Process error] {e}")
        upd(jid, status="error", step=f"Loi: {e}", progress=0)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request":request})

@app.get("/api/config")
async def config():
    return {"avatars":AVATARS,"voices":VOICES}

@app.post("/api/create")
async def create(
    bg: BackgroundTasks,
    product_url: Optional[str] = Form(None),
    images: List[UploadFile] = File(default=[]),
    api_key: Optional[str] = Form(None),
    avatar_id: Optional[str] = Form(None),
    voice_id: Optional[str] = Form(None),
    duration: int = Form(25),
):
    if not product_url and not images:
        raise HTTPException(400, "Can link san pham hoac anh")
    jid = uuid.uuid4().hex
    jobs[jid] = {"status":"pending","step":"Dang chuan bi...","progress":0,"result":None,"product_info":None}
    img_paths = []
    for img in images:
        if img and img.filename:
            ext = Path(img.filename).suffix or ".jpg"
            sp = UPLOAD / f"up_{jid}_{uuid.uuid4().hex[:6]}{ext}"
            sp.write_bytes(await img.read())
            img_paths.append(str(sp))
    bg.add_task(process, jid, product_url, img_paths,
        (api_key or "").strip(), avatar_id or AVATARS[0]["id"],
        voice_id or VOICES[0]["id"], duration)
    return {"job_id":jid}

@app.get("/api/job/{jid}")
async def get_job(jid: str):
    if jid not in jobs: raise HTTPException(404,"Not found")
    return jobs[jid]

@app.get("/api/download/{fn}")
async def download(fn: str):
    fp = OUTPUT / fn
    if not fp.exists(): raise HTTPException(404,"File not found")
    return FileResponse(str(fp), media_type="video/mp4", filename=fn,
        headers={"Content-Disposition":f"attachment; filename={fn}"})

@app.get("/health")
async def health():
    return {"status":"ok","version":"4.2.0","font":str(FONT_PATH),"opencv":cv2.__version__}
