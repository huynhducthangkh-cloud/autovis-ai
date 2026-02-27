import os, uuid, re, time, asyncio, httpx, json, base64
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.requests import Request

app = FastAPI(title="AutoVis AI", version="4.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

BASE   = Path(__file__).parent
UPLOAD = BASE / "uploads";    UPLOAD.mkdir(exist_ok=True)
OUTPUT = BASE / "outputs";    OUTPUT.mkdir(exist_ok=True)
STATIC = BASE / "static";     STATIC.mkdir(exist_ok=True)
MUSIC  = BASE / "assets/music"; MUSIC.mkdir(parents=True, exist_ok=True)

app.mount("/static",  StaticFiles(directory=str(STATIC)),  name="static")
app.mount("/outputs", StaticFiles(directory=str(OUTPUT)),  name="outputs")
templates = Jinja2Templates(directory=str(BASE / "templates"))

# --- Job store ---
jobs: dict = {}
def upd(jid, **kw):
    if jid in jobs: jobs[jid].update(kw)

# --- HeyGen Avatars & Voices ---
AVATARS = [
    {"id":"Abigail_expressive_2024112501",   "name":"Abigail",  "emoji":"👩",  "style":"Trẻ trung"},
    {"id":"Angela-inblackskirt-20220820",    "name":"Angela",   "emoji":"👩‍💼", "style":"Chuyên nghiệp"},
    {"id":"Anna_public_3_20240108",          "name":"Anna",     "emoji":"🧑‍🦰", "style":"Thân thiện"},
    {"id":"Emily-inpinkskirt-20220820",      "name":"Emily",    "emoji":"💃",  "style":"Năng động"},
    {"id":"Susan-inbluetshirt-20220821",     "name":"Susan",    "emoji":"🙋‍♀️", "style":"Tự nhiên"},
    {"id":"Lily-inpinkskirt-20220822",       "name":"Lily",     "emoji":"🌸",  "style":"Dịu dàng"},
]
VOICES = [
    {"id":"vi-VN-HoaiMyNeural",    "name":"Hoài My - Nữ miền Nam (Khuyến nghị)"},
    {"id":"vi-VN-NamMinhNeural",   "name":"Nam Minh - Nam miền Nam"},
    {"id":"vi-VN-Standard-A",      "name":"Giọng nữ chuẩn Việt"},
]

# --- Product Analyzer ---
PLATFORM_HINTS = {
    "shopee.vn":    "Shopee",
    "lazada.vn":    "Lazada",
    "tiki.vn":      "Tiki",
    "tiktok.com":   "TikTok Shop",
    "sendo.vn":     "Sendo",
    "zalora.vn":    "Zalora",
}

KIDS_KEYWORDS = [
    "bé","trẻ em","trẻ sơ sinh","baby","kids","children","infant",
    "toddler","boy","girl","bé trai","bé gái","đồ trẻ em",
    "áo trẻ em","quần trẻ em","bộ trẻ em","váy bé",
]

AGE_MAP = {
    "sơ sinh":   ("0-12 tháng",  "newborn"),
    "0-1":       ("0-12 tháng",  "newborn"),
    "1-3":       ("1-3 tuổi",    "toddler"),
    "toddler":   ("1-3 tuổi",    "toddler"),
    "4-6":       ("4-6 tuổi",    "preschool"),
    "mầm non":   ("4-6 tuổi",    "preschool"),
    "7-10":      ("7-10 tuổi",   "school"),
    "tiểu học":  ("7-10 tuổi",   "school"),
}

async def analyze_product(url: str) -> dict:
    """Smart scrape + analyze product from URL"""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                          "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        }
        async with httpx.AsyncClient(timeout=18, follow_redirects=True) as c:
            r = await c.get(url, headers=headers)
            html = r.text

        # Platform
        platform = "Website"
        for domain, name in PLATFORM_HINTS.items():
            if domain in url: platform = name; break

        # Title
        t = re.search(r'<title[^>]*>(.*?)</title>', html, re.I|re.S)
        title = re.sub(r'\s+', ' ', t.group(1)).strip()[:100] if t else "Sản phẩm"

        # Description
        dm = re.search(
            r'<meta[^>]+(?:name=["\']description["\']|property=["\']og:description["\'])'
            r'[^>]+content=["\']([^"\']{10,300})', html, re.I)
        desc = dm.group(1).strip() if dm else ""

        # Price
        pm = re.search(r'(\d[\d\.,]+)\s*(?:đ|VNĐ|vnđ|₫)', html)
        price = pm.group(0) if pm else ""
        # Also try structured price
        pm2 = re.search(r'"price"\s*:\s*"?(\d[\d\.,]+)"?', html)
        if not price and pm2: price = pm2.group(1) + "đ"

        # Images
        imgs = re.findall(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', html, re.I)
        if not imgs:
            imgs = re.findall(r'<img[^>]+src=["\']([^"\']{20,}\.(?:jpg|jpeg|png|webp))["\']', html, re.I)
        img_url = imgs[0] if imgs else ""

        # Download image
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

        # Smart category detection
        text_lower = (title + " " + desc).lower()
        is_kids = any(k in text_lower for k in KIDS_KEYWORDS)

        gender = "bé gái" if any(w in text_lower for w in ["gái","girl","váy","đầm","hồng","tím"]) \
            else "bé trai" if any(w in text_lower for w in ["trai","boy","xanh dương","xe","robot"]) \
            else "bé"

        age_label, age_key = "1-3 tuổi", "toddler"
        for kw, (lbl, key) in AGE_MAP.items():
            if kw in text_lower:
                age_label, age_key = lbl, key; break

        style = "cute & colorful"
        if any(w in text_lower for w in ["sang","luxury","cao cấp"]): style = "luxury kids"
        elif any(w in text_lower for w in ["thể thao","sport","active"]): style = "sporty kids"
        elif any(w in text_lower for w in ["dễ thương","cute","kawaii"]): style = "cute kawaii"

        return {
            "title": title,
            "description": desc,
            "price": price,
            "platform": platform,
            "img_url": img_url,
            "local_img": local_img,
            "is_kids": is_kids,
            "gender": gender,
            "age_label": age_label,
            "age_key": age_key,
            "style": style,
            "source_url": url,
        }
    except Exception as e:
        return {
            "title": "Sản phẩm thời trang bé", "description": "", "price": "",
            "platform": "Shopee", "img_url": "", "local_img": None,
            "is_kids": True, "gender": "bé", "age_label": "1-3 tuổi",
            "age_key": "toddler", "style": "cute & colorful", "source_url": url,
        }


def analyze_image_locally(img_path: str) -> dict:
    """Basic image analysis without API"""
    return {
        "title": "Thời trang bé yêu",
        "description": "Sản phẩm thời trang cho bé chất lượng cao",
        "price": "", "platform": "Upload",
        "img_url": "", "local_img": img_path,
        "is_kids": True, "gender": "bé",
        "age_label": "1-3 tuổi", "age_key": "toddler",
        "style": "cute & colorful", "source_url": "",
    }


# --- Script Generator ---
SCRIPT_TEMPLATES = {
    "newborn": [
        "Ơi các mẹ ơi! {title} siêu cute cho bé sơ sinh nhà mình đây! "
        "Chất vải 100% cotton mềm mại, an toàn cho làn da nhạy cảm của bé. "
        "{price_text}Đặt ngay hôm nay, giao hàng toàn quốc nhé các mẹ!",
    ],
    "toddler": [
        "Các mẹ ơi xem {title} này xinh không! "
        "Phù hợp cho {gender} {age_label}, chất vải thoáng mát dễ chịu. "
        "{price_text}Mẹ nào đang tìm đồ cho bé thì đừng bỏ lỡ nhé!",
        "Ồ trời ơi cute quá đi! {title} - hot trend {year} đây các mẹ! "
        "Bé mặc vào là đẹp ngay, chụp ảnh cực kỳ photogenic. "
        "{price_text}Bình luận GIÁ để mình báo ngay!",
    ],
    "preschool": [
        "Mẹ bỉm đang tìm đồ cho bé {age_label}? {title} là lựa chọn hoàn hảo! "
        "Thiết kế {style}, bé mặc vào tự tin hơn hẳn. "
        "{price_text}Giao hàng nhanh, đổi trả dễ dàng!",
    ],
    "school": [
        "Thời trang học đường cực chất! {title} cho {gender} {age_label}. "
        "Vải bền đẹp, co giãn tốt, bé mặc cả ngày vẫn thoải mái. "
        "{price_text}Đặt ngay kẻo hết size nhé!",
    ],
}

def make_script(p: dict) -> str:
    age_key = p.get("age_key", "toddler")
    templates = SCRIPT_TEMPLATES.get(age_key, SCRIPT_TEMPLATES["toddler"])
    import random
    tpl = random.choice(templates)
    pr = p.get("price","")
    price_text = f"Giá chỉ {pr}! " if pr else "Giá cực hấp dẫn! "
    return tpl.format(
        title   = (p.get("title") or "sản phẩm này")[:45],
        gender  = p.get("gender","bé"),
        age_label = p.get("age_label","1-3 tuổi"),
        style   = p.get("style","cute"),
        price_text = price_text,
        year    = "2025",
    )


# --- Caption & Hashtag Generator ---
def make_content(p: dict) -> dict:
    t = (p.get("title") or "Thời trang bé")[:40]
    pr = p.get("price","")
    g = p.get("gender","bé")
    age = p.get("age_label","")
    platform = p.get("platform","")
    pstr = f"\n💰 Chỉ {pr}" if pr else ""

    captions = [
        f"👶 {t}{pstr}\n✨ Chất vải mềm mại, an toàn cho {g}\n📦 Giao toàn quốc - Đổi trả dễ dàng\n👇 Bình luận GIÁ để đặt hàng ngay!",
        f"🔥 HOT TREND - {t}{pstr}\n💕 Phù hợp {g} {age}\n✅ Chính hãng 100% từ {platform}\n🛒 Link mua trong bio - Đặt ngay kẻo hết!",
        f"😍 Cute quá các mẹ ơi!\n{t}{pstr}\n🌸 Thiết kế {p.get('style','dễ thương')}\n💬 Nhắn tin ngay để được tư vấn miễn phí!",
    ]

    hashtags = [
        "#thoitrangtreem #mevabe #beyeu #tiktokshop #sanphamhot #muahang #trending #viral #review #cute",
        f"#thoitrangbe #dotreem #{g.replace(' ','')} #baby #kids #fashion #shopee #lazada #affiliate #mua1tang1",
        "#reviewsanpham #unboxing #haul #recommend #chinhang #giaonhanh #sale #deal #tiktok #fyp",
    ]

    return {"captions": captions, "hashtags": hashtags}


# --- HeyGen Integration ---
async def heygen_upload(path: str, key: str) -> Optional[str]:
    try:
        data = open(path,"rb").read()
        ext  = Path(path).suffix.lower().lstrip(".") or "jpeg"
        mime = f"image/{ext}" if ext in ["jpg","jpeg","png","webp"] else "image/jpeg"
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://upload.heygen.com/v1/asset",
                headers={"x-api-key":key,"Content-Type":mime}, content=data)
            d = r.json()
            return d.get("data",{}).get("id") or d.get("id")
    except Exception as e:
        print(f"[HeyGen Upload] {e}"); return None


async def heygen_create(key:str, script:str, avatar:str, voice:str, bg_id:Optional[str], duration:int) -> Optional[str]:
    bg = {"type":"image","url":f"https://resource.heygen.com/image/{bg_id}"} \
         if bg_id else {"type":"color","value":"#FFF5F9"}
    payload = {
        "video_inputs": [{
            "character": {"type":"avatar","avatar_id":avatar,"avatar_style":"normal"},
            "voice":     {"type":"text","input_text":script,"voice_id":voice,"speed":1.0},
            "background": bg,
        }],
        "dimension": {"width":1080,"height":1920},
        "test": False,
    }
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post("https://api.heygen.com/v2/video/generate",
                headers={"X-Api-Key":key,"Content-Type":"application/json"}, json=payload)
            d = r.json()
            print(f"[HeyGen Create] {d}")
            return d.get("data",{}).get("video_id") or d.get("video_id")
    except Exception as e:
        print(f"[HeyGen Create] {e}"); return None


async def heygen_poll(key:str, vid:str, jid:str) -> Optional[str]:
    for i in range(80):
        await asyncio.sleep(8)
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"https://api.heygen.com/v1/video_status.get?video_id={vid}",
                    headers={"X-Api-Key":key})
                d = r.json()
                st  = d.get("data",{}).get("status","")
                url = d.get("data",{}).get("video_url","")
                pct = min(92, 48 + i)
                upd(jid, step=f"🎬 HeyGen đang render... ({(i+1)*8}s)", progress=pct)
                if st == "completed" and url: return url
                if st == "failed":
                    print(f"[HeyGen Failed] {d}"); return None
        except: pass
    return None


async def heygen_download(url:str, jid:str) -> str:
    try:
        out = OUTPUT / f"video_{jid}.mp4"
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as c:
            r = await c.get(url)
            if r.status_code == 200:
                out.write_bytes(r.content); return str(out)
    except Exception as e:
        print(f"[Download] {e}")
    return ""


# --- FFmpeg Fallback Video ---
async def make_ffmpeg_video(img: Optional[str], p: dict, jid: str) -> str:
    """Create video with FFmpeg - simple and robust"""
    out = OUTPUT / f"video_{jid}.mp4"

    def safe(s, n=28):
        if not s: return "San pham"
        r = s[:n].encode("ascii","ignore").decode()
        for c in [chr(39), ':', chr(92), chr(34), '[', ']']:
            r = r.replace(c, "")
        return r or "San pham"

    title = safe(p.get("title",""))
    price = safe(p.get("price",""), 20)
    price_line = f"Gia {price}" if price else "Gia sieu hot"

    try:
        if img and Path(img).exists():
            vf = (
                "scale=1080:1920:force_original_aspect_ratio=decrease,"
                "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=0x1a0a2e,"
                f"drawtext=text='{title}':fontsize=34:fontcolor=white:"
                "x=(w-text_w)/2:y=140:box=1:boxcolor=black@0.6:boxborderw=10,"
                f"drawtext=text='{price_line}':fontsize=28:fontcolor=yellow:"
                "x=(w-text_w)/2:y=195:box=1:boxcolor=black@0.5:boxborderw=8,"
                "drawtext=text='Dat hang ngay!':fontsize=26:fontcolor=lime:"
                "x=(w-text_w)/2:y=h-100:box=1:boxcolor=black@0.5:boxborderw=8"
            )
            cmd = ["ffmpeg","-y","-loop","1","-i",str(img),"-t","20",
                   "-vf",vf,"-c:v","libx264","-preset","ultrafast",
                   "-pix_fmt","yuv420p","-r","24",str(out)]
            proc = await asyncio.create_subprocess_exec(*cmd,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if out.exists() and out.stat().st_size > 1000:
                return str(out)
            print(f"[FFmpeg img] {stderr.decode()[-200:]}")

        # Plain background fallback
        vf2 = (
            "drawtext=text='AutoVis AI':fontsize=48:fontcolor=white:x=(w-text_w)/2:y=h/2-100,"
            f"drawtext=text='{title}':fontsize=30:fontcolor=yellow:"
            "x=(w-text_w)/2:y=h/2-20:box=1:boxcolor=black@0.4:boxborderw=8,"
            f"drawtext=text='{price_line}':fontsize=26:fontcolor=lime:"
            "x=(w-text_w)/2:y=h/2+50"
        )
        cmd2 = ["ffmpeg","-y","-f","lavfi",
                "-i","color=c=0x1a0a2e:size=1080x1920:rate=24",
                "-t","15","-vf",vf2,
                "-c:v","libx264","-preset","ultrafast","-pix_fmt","yuv420p",str(out)]
        proc2 = await asyncio.create_subprocess_exec(*cmd2,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc2.communicate()
    except Exception as e:
        print(f"[FFmpeg] {e}")

    return str(out) if (out.exists() and out.stat().st_size > 500) else ""


# --- Main Job Processor ---
async def process(jid, product_url, img_path, api_key, avatar_id, voice_id, duration):
    try:
        upd(jid, status="processing", step="🔍 Đang phân tích sản phẩm...", progress=5)

        # 1. Analyze product
        if product_url:
            upd(jid, step="📡 Đang tải thông tin từ link...", progress=12)
            p = await analyze_product(product_url)
            if p.get("local_img") and not img_path:
                img_path = p["local_img"]
        elif img_path:
            upd(jid, step="🖼️ Đang phân tích hình ảnh...", progress=12)
            p = analyze_image_locally(img_path)
        else:
            p = {"title":"Sản phẩm","description":"","price":"","is_kids":True,
                 "gender":"bé","age_label":"1-3 tuổi","age_key":"toddler",
                 "style":"cute","platform":"","local_img":None}

        upd(jid, step="✍️ Đang tạo script quảng cáo...", progress=22,
            product_info={
                "title":    p.get("title",""),
                "price":    p.get("price",""),
                "gender":   p.get("gender",""),
                "age":      p.get("age_label",""),
                "platform": p.get("platform",""),
            })

        # 2. Generate script & content
        await asyncio.sleep(0.4)
        script  = make_script(p)
        content = make_content(p)

        # 3. Create video
        video_path  = ""
        used_heygen = False

        if api_key:
            bg_id = None
            curr_img = img_path or p.get("local_img")
            if curr_img and Path(curr_img).exists():
                upd(jid, step="⬆️ Đang upload ảnh lên HeyGen...", progress=30)
                bg_id = await heygen_upload(curr_img, api_key)

            upd(jid, step="🤖 Đang tạo người mẫu AI...", progress=40)
            vid_id = await heygen_create(api_key, script, avatar_id, voice_id, bg_id, duration)

            if vid_id:
                upd(jid, step="🎬 HeyGen đang render video...", progress=48)
                vid_url = await heygen_poll(api_key, vid_id, jid)
                if vid_url:
                    upd(jid, step="⬇️ Đang tải video về...", progress=94)
                    video_path  = await heygen_download(vid_url, jid)
                    used_heygen = bool(video_path)

        if not video_path:
            upd(jid, step="🎨 Đang render video...", progress=65)
            video_path = await make_ffmpeg_video(img_path or p.get("local_img"), p, jid)

        # 4. Done
        fn = Path(video_path).name if video_path else ""
        upd(jid,
            status="done", step="✅ Hoàn tất!", progress=100,
            result={
                "video_url":      f"/outputs/{fn}" if fn else "",
                "video_filename": fn,
                "product":        p,
                "script":         script,
                "captions":       content["captions"],
                "hashtags":       content["hashtags"],
                "used_heygen":    used_heygen,
            })

    except Exception as e:
        upd(jid, status="error", step=f"❌ Lỗi: {e}", progress=0)


# --- Routes ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/config")
async def config():
    return {"avatars": AVATARS, "voices": VOICES}

@app.post("/api/create")
async def create(
    bg: BackgroundTasks,
    product_url: Optional[str]  = Form(None),
    image:       Optional[UploadFile] = File(None),
    api_key:     Optional[str]  = Form(None),
    avatar_id:   Optional[str]  = Form(None),
    voice_id:    Optional[str]  = Form(None),
    duration:    int             = Form(25),
):
    if not product_url and not image:
        raise HTTPException(400, "Cần link sản phẩm hoặc ảnh")

    jid = uuid.uuid4().hex
    jobs[jid] = {"status":"pending","step":"Đang chuẩn bị...","progress":0,"result":None,"product_info":None}

    img_path = None
    if image and image.filename:
        ext = Path(image.filename).suffix or ".jpg"
        sp  = UPLOAD / f"up_{jid}{ext}"
        sp.write_bytes(await image.read())
        img_path = str(sp)

    bg.add_task(process, jid, product_url, img_path,
        (api_key or "").strip(),
        avatar_id or AVATARS[0]["id"],
        voice_id  or VOICES[0]["id"],
        duration)

    return {"job_id": jid}

@app.get("/api/job/{jid}")
async def get_job(jid: str):
    if jid not in jobs: raise HTTPException(404,"Not found")
    return jobs[jid]

@app.get("/api/download/{fn}")
async def download(fn: str):
    fp = OUTPUT / fn
    if not fp.exists(): raise HTTPException(404,"File not found")
    return FileResponse(str(fp), media_type="video/mp4", filename=fn,
        headers={"Content-Disposition": f"attachment; filename={fn}"})

@app.get("/health")
async def health():
    return {"status":"ok","version":"4.0.0"}
