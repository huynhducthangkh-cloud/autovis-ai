import os, uuid, re, time, asyncio, httpx, json, base64
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

app = FastAPI(title="AutoVis AI", version="4.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE   = Path(__file__).parent
UPLOAD = BASE / "uploads";  UPLOAD.mkdir(exist_ok=True)
OUTPUT = BASE / "outputs";  OUTPUT.mkdir(exist_ok=True)
STATIC = BASE / "static";   STATIC.mkdir(exist_ok=True)
MUSIC  = BASE / "assets" / "music"; MUSIC.mkdir(parents=True, exist_ok=True)

app.mount("/static",  StaticFiles(directory=str(STATIC)),  name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT)),  name="outputs")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# Font paths
FONT_VI   = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/google-fonts/Poppins-Bold.ttf"
if not Path(FONT_VI).exists():
    FONT_VI = "/usr/share/fonts/truetype/freefont/FreeSans.ttf"
if not Path(FONT_BOLD).exists():
    FONT_BOLD = FONT_VI

jobs: dict = {}
def upd(jid, **kw):
    if jid in jobs: jobs[jid].update(kw)

AVATARS = [
    {"id":"Abigail_expressive_2024112501",  "name":"Abigail",  "emoji":"👩",  "style":"Tre trung"},
    {"id":"Angela-inblackskirt-20220820",   "name":"Angela",   "emoji":"👩‍💼", "style":"Chuyen nghiep"},
    {"id":"Anna_public_3_20240108",         "name":"Anna",     "emoji":"🧑‍🦰", "style":"Than thien"},
    {"id":"Emily-inpinkskirt-20220820",     "name":"Emily",    "emoji":"💃",  "style":"Nang dong"},
    {"id":"Susan-inbluetshirt-20220821",    "name":"Susan",    "emoji":"🙋‍♀️", "style":"Tu nhien"},
    {"id":"Lily-inpinkskirt-20220822",      "name":"Lily",     "emoji":"🌸",  "style":"Diu dang"},
]
VOICES = [
    {"id":"vi-VN-HoaiMyNeural",   "name":"Hoai My - Nu mien Nam (Khuyen nghi)"},
    {"id":"vi-VN-NamMinhNeural",  "name":"Nam Minh - Giong nam"},
    {"id":"vi-VN-Standard-A",     "name":"Giong nu chuan Viet"},
]

PLATFORM_HINTS = {
    "shopee.vn":"Shopee","lazada.vn":"Lazada","tiki.vn":"Tiki",
    "tiktok.com":"TikTok Shop","sendo.vn":"Sendo","zalora.vn":"Zalora",
}
KIDS_KEYWORDS = [
    "be","tre em","tre so sinh","baby","kids","children","infant",
    "toddler","boy","girl","be trai","be gai","do tre em",
    "ao tre em","quan tre em","bo tre em","vay be",
]
AGE_MAP = {
    "so sinh":("0-12 thang","newborn"),"0-1":("0-12 thang","newborn"),
    "1-3":("1-3 tuoi","toddler"),"toddler":("1-3 tuoi","toddler"),
    "4-6":("4-6 tuoi","preschool"),"mam non":("4-6 tuoi","preschool"),
    "7-10":("7-10 tuoi","school"),"tieu hoc":("7-10 tuoi","school"),
}

async def analyze_product(url: str) -> dict:
    try:
        headers = {"User-Agent":"Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari/604.1"}
        async with httpx.AsyncClient(timeout=18, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            html = r.text
        platform = "Website"
        for domain, name in PLATFORM_HINTS.items():
            if domain in url: platform = name; break
        t = re.search(r'<title[^>]*>(.*?)</title>', html, re.I|re.S)
        title = re.sub(r'\s+', ' ', t.group(1)).strip()[:100] if t else "San pham"
        dm = re.search(r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])[^>]+content=["\']([^"\']{10,300})', html, re.I)
        desc = dm.group(1).strip() if dm else ""
        pm = re.search(r'(\d[\d\.,]+)\s*(?:d|VND|vnd|dong)', html, re.I)
        price = pm.group(0) if pm else ""
        imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
        if not imgs:
            imgs = re.findall(r'<img[^>]+src=["\']([^"\']{20,}\.(?:jpg|jpeg|png|webp))["\']', html, re.I)
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
        text_lower = (title + " " + desc).lower()
        is_kids = any(k in text_lower for k in KIDS_KEYWORDS)
        gender = "be gai" if any(w in text_lower for w in ["gai","girl","vay","dam","hong","tim"]) \
            else "be trai" if any(w in text_lower for w in ["trai","boy","xanh duong","xe","robot"]) else "be"
        age_label, age_key = "1-3 tuoi", "toddler"
        for kw, (lbl, key) in AGE_MAP.items():
            if kw in text_lower:
                age_label, age_key = lbl, key; break
        style = "cute & colorful"
        if any(w in text_lower for w in ["sang","luxury","cao cap"]): style = "luxury kids"
        elif any(w in text_lower for w in ["the thao","sport","active"]): style = "sporty kids"
        return {"title":title,"description":desc,"price":price,"platform":platform,
                "img_url":img_url,"local_img":local_img,"is_kids":is_kids,
                "gender":gender,"age_label":age_label,"age_key":age_key,"style":style,"source_url":url}
    except Exception as e:
        return {"title":"San pham thoi trang","description":"","price":"","platform":"Shopee",
                "img_url":"","local_img":None,"is_kids":True,"gender":"be",
                "age_label":"1-3 tuoi","age_key":"toddler","style":"cute","source_url":url}

def analyze_image_locally(img_path: str) -> dict:
    return {"title":"Thoi trang be yeu","description":"San pham chat luong cao","price":"",
            "platform":"Upload","img_url":"","local_img":img_path,"is_kids":True,
            "gender":"be","age_label":"1-3 tuoi","age_key":"toddler","style":"cute","source_url":""}

SCRIPT_TEMPLATES = {
    "newborn": ["Oi cac me oi! {title} sieu cute cho be so sinh nha minh day! Chat vai 100% cotton mem mai, an toan cho lan da nhay cam cua be. {price_text}Dat ngay hom nay, giao hang toan quoc nhe cac me!"],
    "toddler": [
        "Cac me oi xem {title} nay xinh khong! Phu hop cho {gender} {age_label}, chat vai thoang mat de chiu. {price_text}Me nao dang tim do cho be thi dung bo lo nhe!",
        "Oi troi oi cute qua di! {title} - hot trend 2025 day cac me! Be mac vao la dep ngay, chup anh cuc ky photogenic. {price_text}Binh luan GIA de minh bao ngay!",
    ],
    "preschool": ["Me bim dang tim do cho be {age_label}? {title} la lua chon hoan hao! Thiet ke {style}, be mac vao tu tin hon han. {price_text}Giao hang nhanh, doi tra de dang!"],
    "school": ["Thoi trang hoc duong cuc chat! {title} cho {gender} {age_label}. Vai ben dep, co gian tot, be mac ca ngay van thoai mai. {price_text}Dat ngay keo het size nhe!"],
}

def make_script(p: dict) -> str:
    import random
    age_key = p.get("age_key","toddler")
    templates = SCRIPT_TEMPLATES.get(age_key, SCRIPT_TEMPLATES["toddler"])
    tpl = random.choice(templates)
    pr = p.get("price","")
    price_text = f"Gia chi {pr}! " if pr else "Gia cuc hap dan! "
    return tpl.format(
        title=p.get("title","san pham")[:45],
        gender=p.get("gender","be"),
        age_label=p.get("age_label","1-3 tuoi"),
        style=p.get("style","cute"),
        price_text=price_text,
    )

def make_content(p: dict) -> dict:
    t = (p.get("title") or "Thoi trang be")[:40]
    pr = p.get("price","")
    g = p.get("gender","be")
    age = p.get("age_label","")
    platform = p.get("platform","")
    pstr = f"\nGia chi {pr}" if pr else ""
    captions = [
        f"Be {t}{pstr}\nChat vai mem mai, an toan cho {g}\nGiao toan quoc - Doi tra de dang\nBinh luan GIA de dat hang ngay!",
        f"HOT TREND - {t}{pstr}\nPhu hop {g} {age}\nChinh hang 100% tu {platform}\nLink mua trong bio - Dat ngay keo het!",
        f"Cute qua cac me oi!\n{t}{pstr}\nThiet ke {p.get('style','de thuong')}\nNhan tin ngay de duoc tu van mien phi!",
    ]
    hashtags = [
        "#thoitrangtreem #mevabe #beyeu #tiktokshop #sanphamhot #muahang #trending #viral #cute",
        f"#thoitrangbe #dotreem #{g.replace(' ','')} #baby #kids #fashion #shopee #lazada #affiliate",
        "#reviewsanpham #unboxing #haul #recommend #chinhang #giaonhanh #sale #deal #tiktok #fyp",
    ]
    return {"captions":captions,"hashtags":hashtags}

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
    except Exception as e:
        print(f"[HeyGen Upload] {e}"); return None

async def heygen_create(key, script, avatar, voice, bg_id, duration) -> Optional[str]:
    bg = {"type":"image","url":f"https://resource.heygen.com/image/{bg_id}"} if bg_id else {"type":"color","value":"#FFF5F9"}
    payload = {
        "video_inputs": [{"character":{"type":"avatar","avatar_id":avatar,"avatar_style":"normal"},
            "voice":{"type":"text","input_text":script,"voice_id":voice,"speed":1.0},"background":bg}],
        "dimension":{"width":1080,"height":1920},"test":False,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.heygen.com/v2/video/generate",
                headers={"X-Api-Key":key,"Content-Type":"application/json"}, json=payload)
            d = r.json(); print(f"[HeyGen Create] {d}")
            return d.get("data",{}).get("video_id") or d.get("video_id")
    except Exception as e:
        print(f"[HeyGen Create] {e}"); return None

async def heygen_poll(key, vid, jid) -> Optional[str]:
    for i in range(80):
        await asyncio.sleep(8)
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"https://api.heygen.com/v1/video_status.get?video_id={vid}",
                    headers={"X-Api-Key":key})
                d = r.json(); st = d.get("data",{}).get("status",""); url = d.get("data",{}).get("video_url","")
                upd(jid, step=f"HeyGen dang render... ({(i+1)*8}s)", progress=min(92, 48+i))
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
    except Exception as e:
        print(f"[Download] {e}")
    return ""

def safe_text(s, n=30):
    if not s: return "AutoVis AI"
    r = s[:n].encode("ascii","ignore").decode()
    for c in ["'", ":", "\\", '"', "[", "]", "{", "}"]:
        r = r.replace(c, "")
    return r.strip() or "AutoVis AI"

async def make_ffmpeg_video(images: List[str], p: dict, jid: str) -> str:
    out = OUTPUT / f"video_{jid}.mp4"
    title = safe_text(p.get("title",""), 32)
    price = safe_text(p.get("price",""), 20)
    gender = safe_text(p.get("gender",""), 15)
    age = safe_text(p.get("age_label",""), 15)
    price_line = f"Gia: {price}" if price else "Gia sieu hot!"
    sub = f"{gender} {age}".strip()

    valid_imgs = [i for i in images if i and Path(i).exists()]

    try:
        if valid_imgs:
            # Multi-image slideshow with transitions
            sec_per_img = max(3, 20 // max(len(valid_imgs), 1))
            total = sec_per_img * len(valid_imgs)

            # Build ffmpeg inputs
            cmd = ["ffmpeg", "-y"]
            for img in valid_imgs:
                cmd += ["-loop", "1", "-t", str(sec_per_img), "-i", str(img)]

            # Build filter_complex for slideshow
            n = len(valid_imgs)
            scale_parts = []
            for i in range(n):
                scale_parts.append(
                    f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
                    f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x0a0a1a,setsar=1[v{i}];"
                )

            if n == 1:
                concat_out = "[v0]"
                filter_str = scale_parts[0].rstrip(";") + "," + (
                    f"drawtext=fontfile={FONT_VI}:text='{title}':fontsize=38:fontcolor=white:"
                    f"x=(w-text_w)/2:y=120:box=1:boxcolor=black@0.65:boxborderw=12,"
                    f"drawtext=fontfile={FONT_VI}:text='{price_line}':fontsize=30:fontcolor=yellow:"
                    f"x=(w-text_w)/2:y=172:box=1:boxcolor=black@0.55:boxborderw=10,"
                )
                if sub:
                    filter_str += (
                        f"drawtext=fontfile={FONT_VI}:text='{sub}':fontsize=24:fontcolor=cyan:"
                        f"x=(w-text_w)/2:y=218:box=1:boxcolor=black@0.5:boxborderw=8,"
                    )
                filter_str += (
                    f"drawtext=fontfile={FONT_BOLD}:text='AutoVis AI':fontsize=18:fontcolor=white@0.5:"
                    f"x=w-tw-14:y=h-th-14,"
                    f"drawtext=fontfile={FONT_VI}:text='Dat hang ngay!':fontsize=28:fontcolor=lime:"
                    f"x=(w-text_w)/2:y=h-110:box=1:boxcolor=black@0.55:boxborderw=10"
                )
                cmd += [
                    "-vf", filter_str,
                    "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-r","24",str(out)
                ]
            else:
                # Multiple images: slideshow then overlay text
                filter_complex = "".join(scale_parts)
                concat_inputs = "".join(f"[v{i}]" for i in range(n))
                filter_complex += f"{concat_inputs}concat=n={n}:v=1:a=0[base];"
                filter_complex += (
                    f"[base]drawtext=fontfile={FONT_VI}:text='{title}':fontsize=36:fontcolor=white:"
                    f"x=(w-text_w)/2:y=100:box=1:boxcolor=black@0.65:boxborderw=12,"
                    f"drawtext=fontfile={FONT_VI}:text='{price_line}':fontsize=28:fontcolor=yellow:"
                    f"x=(w-text_w)/2:y=148:box=1:boxcolor=black@0.55:boxborderw=10,"
                )
                if sub:
                    filter_complex += (
                        f"drawtext=fontfile={FONT_VI}:text='{sub}':fontsize=22:fontcolor=cyan:"
                        f"x=(w-text_w)/2:y=190:box=1:boxcolor=black@0.45:boxborderw=8,"
                    )
                filter_complex += (
                    f"drawtext=fontfile={FONT_BOLD}:text='AutoVis AI':fontsize=16:fontcolor=white@0.5:"
                    f"x=w-tw-14:y=h-th-14,"
                    f"drawtext=fontfile={FONT_VI}:text='Dat hang ngay!':fontsize=26:fontcolor=lime:"
                    f"x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.55:boxborderw=10[out]"
                )
                cmd += [
                    "-filter_complex", filter_complex,
                    "-map","[out]",
                    "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p","-r","24",str(out)
                ]

            proc = await asyncio.create_subprocess_exec(*cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if out.exists() and out.stat().st_size > 1000:
                return str(out)
            print(f"[FFmpeg imgs] {stderr.decode()[-300:]}")

        # No images fallback
        vf = (
            f"drawtext=fontfile={FONT_BOLD}:text='AutoVis AI':fontsize=52:fontcolor=white:x=(w-text_w)/2:y=h/2-120,"
            f"drawtext=fontfile={FONT_VI}:text='{title}':fontsize=32:fontcolor=yellow:x=(w-text_w)/2:y=h/2-40:box=1:boxcolor=black@0.4:boxborderw=8,"
            f"drawtext=fontfile={FONT_VI}:text='{price_line}':fontsize=28:fontcolor=lime:x=(w-text_w)/2:y=h/2+20"
        )
        cmd2 = ["ffmpeg","-y","-f","lavfi","-i","color=c=0x0a0a1a:size=1080x1920:rate=24",
                "-t","15","-vf",vf,"-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(out)]
        proc2 = await asyncio.create_subprocess_exec(*cmd2,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc2.communicate()

    except Exception as e:
        print(f"[FFmpeg error] {e}")

    return str(out) if (out.exists() and out.stat().st_size > 500) else ""

async def process(jid, product_url, img_paths: List[str], api_key, avatar_id, voice_id, duration):
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
            p = {"title":"San pham","description":"","price":"","is_kids":True,
                 "gender":"be","age_label":"1-3 tuoi","age_key":"toddler","style":"cute","platform":"","local_img":None}

        upd(jid, step="Dang tao script quang cao...", progress=22,
            product_info={"title":p.get("title",""),"price":p.get("price",""),
                         "gender":p.get("gender",""),"age":p.get("age_label",""),"platform":p.get("platform","")})

        await asyncio.sleep(0.3)
        script = make_script(p)
        content = make_content(p)

        video_path = ""
        used_heygen = False

        if api_key:
            bg_id = None
            if img_paths and Path(img_paths[0]).exists():
                upd(jid, step="Dang upload anh len HeyGen...", progress=30)
                bg_id = await heygen_upload(img_paths[0], api_key)
            upd(jid, step="Dang tao nguoi mau AI...", progress=40)
            vid_id = await heygen_create(api_key, script, avatar_id, voice_id, bg_id, duration)
            if vid_id:
                upd(jid, step="HeyGen dang render video...", progress=48)
                vid_url = await heygen_poll(api_key, vid_id, jid)
                if vid_url:
                    upd(jid, step="Dang tai video ve...", progress=94)
                    video_path = await heygen_download(vid_url, jid)
                    used_heygen = bool(video_path)

        if not video_path:
            n = len(img_paths)
            msg = f"Dang render video voi {n} hinh anh..." if n > 0 else "Dang render video..."
            upd(jid, step=msg, progress=65)
            video_path = await make_ffmpeg_video(img_paths, p, jid)

        fn = Path(video_path).name if video_path else ""
        upd(jid, status="done", step="Hoan tat!", progress=100,
            result={"video_url":f"/outputs/{fn}" if fn else "","video_filename":fn,
                   "product":p,"script":script,"captions":content["captions"],
                   "hashtags":content["hashtags"],"used_heygen":used_heygen})

    except Exception as e:
        upd(jid, status="error", step=f"Loi: {e}", progress=0)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

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
        (api_key or "").strip(),
        avatar_id or AVATARS[0]["id"],
        voice_id  or VOICES[0]["id"],
        duration)

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
    return {"status":"ok","version":"4.1.0"}
