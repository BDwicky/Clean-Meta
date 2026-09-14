import os
import uuid
import json
import shutil
import random
import datetime
import subprocess
import threading
import hashlib
import hmac

from flask import Flask, request, jsonify, send_from_directory, make_response

app = Flask(__name__)
UPLOAD_DIR = "/var/www/clean-ai/uploads"
STATIC_DIR = "/var/www/clean-ai"
os.makedirs(UPLOAD_DIR, exist_ok=True)

PIN_CODE = "123123"
SECRET_KEY = "clean-ai-secret-pin-salt-" + hashlib.sha256(PIN_CODE.encode()).hexdigest()[:16]

JOBS = {}  # job_id -> {status, progress, filename, report, error, meta}

def create_auth_token():
    sig = hmac.new(SECRET_KEY.encode(), b"authenticated_clean_ai", hashlib.sha256).hexdigest()
    return f"auth_{sig}"

def verify_auth_token(token):
    if not token or not token.startswith("auth_"):
        return False
    expected = create_auth_token()
    return hmac.compare_digest(token, expected)

def is_request_authenticated():
    cookie_token = request.cookies.get("clean_auth")
    if verify_auth_token(cookie_token):
        return True
    header_pin = request.headers.get("X-PIN-Code")
    if header_pin == PIN_CODE:
        return True
    return False

@app.before_request
def check_auth():
    # Mengizinkan akses login verification endpoint
    if request.path == "/api/verify-pin":
        return None
    # Jika root atau static upload atau api dicek
    if not is_request_authenticated():
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized: Masukkan PIN terlebih dahulu", "locked": True}), 401
        # Jika membuka halaman web atau download file saat belum login, tetap layani index.html (tapi konten aplikasi tidak bisa di-load/di-execute)
        # index.html akan menampilkan popup PIN dan tidak memiliki token valid untuk mengakses API


def read_metadata_summary(file_path):
    """Membaca seluruh metadata asli file menggunakan exiftool sebelum dibersihkan."""
    removed_items = []
    try:
        cmd = ["exiftool", "-json", "-G", file_path]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            if data and isinstance(data, list):
                raw = data[0]
                skip_groups = {"File", "SourceFile", "ExifTool", "Composite"}
                for k, v in raw.items():
                    grp = k.split(":", 1)[0] if ":" in k else ""
                    if grp not in skip_groups:
                        val_str = str(v)[:60]
                        removed_items.append(f"{k} = {val_str}")
    except Exception as e:
        removed_items.append(f"Gagal membaca metadata: {e}")

    if not removed_items:
        removed_items.append("Tidak ada metadata terdeteksi (file raw tanpa tag sebelumnya).")
    return removed_items


def process_image_metadata_only(input_path, output_path, ext):
    """
    Membersihkan 100% metadata tanpa mengubah/mengompresi data piksel asli.
    Piksel gambar 100% bit-exact dari aslinya, lalu disuntikkan EXIF kamera asli.
    """
    removed_items = read_metadata_summary(input_path)
    shutil.copy2(input_path, output_path)

    # 1. Hapus seluruh metadata (C2PA, IPTC, XMP, EXIF lama, chunk text AI)
    strip_cmd = ["exiftool", "-overwrite_original", "-all=", output_path]
    subprocess.run(strip_cmd, check=True, timeout=60)

    now = datetime.datetime.now()
    now_str = now.strftime("%Y:%m:%d %H:%M:%S")
    exp_time = random.choice(["1/60", "1/100", "1/120", "1/250", "1/500"])
    iso = random.choice(["50", "64", "80", "100", "125"])

    ext_clean = ext.lower().replace(".", "")
    injected_exif = []

    if ext_clean in ("jpg", "jpeg"):
        # Format JPEG mendukung penuh standarisasi EXIF kamera
        inject_cmd = [
            "exiftool", "-overwrite_original",
            "-Make=Apple",
            "-Model=iPhone 15 Pro Max",
            "-Software=18.1.1",
            f"-Exif:DateTimeOriginal={now_str}",
            f"-Exif:CreateDate={now_str}",
            f"-Exif:ModifyDate={now_str}",
            f"-Exif:ExposureTime={exp_time}",
            "-Exif:FNumber=1.78",
            f"-Exif:ISO={iso}",
            "-Exif:FocalLength=6.765",
            "-Exif:LensModel=iPhone 15 Pro Max back triple camera 6.765mm f/1.78",
            output_path
        ]
        subprocess.run(inject_cmd, check=True, timeout=60)
        injected_exif = [
            ("Make", "Apple"),
            ("Model", "iPhone 15 Pro Max"),
            ("Software", "iOS 18.1.1"),
            ("DateTimeOriginal", now_str),
            ("ExposureTime", exp_time),
            ("FNumber", "f/1.78"),
            ("ISO", iso),
            ("FocalLength", "24mm (6.765mm optik)"),
            ("LensModel", "iPhone 15 Pro Max back triple camera 6.765mm f/1.78"),
        ]
    else:
        # PNG/WEBP dll: Tag umum device capture
        inject_cmd = [
            "exiftool", "-overwrite_original",
            "-Make=Apple",
            "-Model=iPhone 15 Pro Max",
            "-Software=18.1.1",
            output_path
        ]
        subprocess.run(inject_cmd, check=True, timeout=60)
        injected_exif = [
            ("Make", "Apple"),
            ("Model", "iPhone 15 Pro Max"),
            ("Software", "iOS 18.1.1"),
        ]

    modifications = [
        {
            "stage": "Bit-Exact Original Pixels (Kualitas 100% Utuh)",
            "action": "Tidak ada penambahan efek visual, tanpa kompresi ulang, tanpa re-encoding piksel. Kualitas 100% identik dengan file asli."
        },
        {
            "stage": "Pembersihan Metadata Total (exiftool -all=)",
            "action": "Seluruh signature C2PA Content Credentials, XMP AI generation, prompt AI, IPTC tag, dan metadata container dibuang tuntas."
        },
        {
            "stage": "Injeksi Metadata Kamera Asli",
            "action": "Disuntikkan EXIF resmi kamera Apple iPhone 15 Pro Max (ISO, Shutter, Lensa, Tanggal) seolah difoto langsung."
        }
    ]

    return {
        "removed_items": removed_items,
        "modifications": modifications,
        "injected_exif": injected_exif
    }


def probe_video(path):
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    data = json.loads(out.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
    astream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    fps = 30.0
    if vstream:
        rate = vstream.get("avg_frame_rate") or vstream.get("r_frame_rate") or "30/1"
        try:
            num, den = rate.split("/")
            fps = float(num) / float(den) if float(den) != 0 else 30.0
        except Exception:
            pass

    return {
        "duration": float(fmt.get("duration", 0) or 0),
        "width": int(vstream.get("width", 0)) if vstream else 0,
        "height": int(vstream.get("height", 0)) if vstream else 0,
        "fps": round(fps, 2),
        "has_audio": astream is not None,
        "format_name": fmt.get("format_long_name", fmt.get("format_name", "")),
        "tags": fmt.get("tags", {}),
        "stream_tags": {s.get("codec_type", "?"): s.get("tags", {}) for s in streams},
    }


def run_video_job_metadata_only(job_id, in_path, out_path, probe):
    """
    Video diproses secara STREAM COPY (-c copy).
    100% tanpa kompresi ulang, tanpa penurunan kualitas (lossless copy),
    tanpa filter atau effect visual apapun.
    Metadata container/stream AI dibuang lalu diinjeksi metadata Apple QuickTime.
    """
    try:
        JOBS[job_id]["status"] = "processing"
        JOBS[job_id]["progress"] = 15

        removed = read_metadata_summary(in_path)

        # 1. Stream copy tanpa re-encode (-c copy), strip internal container metadata (-map_metadata -1)
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", in_path,
            "-c", "copy",
            "-map_metadata", "-1",
            "-fflags", "+bitexact",
            "-movflags", "+faststart",
            out_path
        ]
        subprocess.run(cmd, check=True, timeout=180)
        JOBS[job_id]["progress"] = 70

        # 2. Hapus sisa metadata dan suntikkan tag kamera Apple QuickTime
        now = datetime.datetime.now()
        create_str = now.strftime("%Y-%m-%d %H:%M:%S")

        exif_cmd = [
            "exiftool", "-overwrite_original",
            "-all=",
            "-Make=Apple",
            "-Model=iPhone 15 Pro Max",
            "-Software=18.1.1",
            f"-CreateDate={now.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-ModifyDate={now.strftime('%Y:%m:%d %H:%M:%S')}",
            out_path,
        ]
        subprocess.run(exif_cmd, check=True, timeout=120)
        JOBS[job_id]["progress"] = 95

        if os.path.exists(in_path):
            os.remove(in_path)

        injected = [
            ("Make", "Apple"),
            ("Model", "iPhone 15 Pro Max"),
            ("Software", "iOS 18.1.1"),
            ("CreateDate", create_str),
        ]

        modifications = [
            {
                "stage": "Stream Copy Murni (-c copy, 100% Lossless)",
                "action": "Video dan audio disalin langsung bit-for-bit tanpa rendering ulang atau filter apapun. Kualitas video 100% asli."
            },
            {
                "stage": "Strip Metadata Container & Stream (-map_metadata -1)",
                "action": "Seluruh metadata encoder, AI generator identifier, C2PA manifest, dan handler tag dibersihkan tuntas."
            },
            {
                "stage": "Injeksi Metadata QuickTime Kamera Asli",
                "action": "Disuntikkan tag kamera Apple iPhone 15 Pro Max sebagai metadata rekaman asli."
            }
        ]

        out_name = os.path.basename(out_path)
        JOBS[job_id]["report"] = {
            "removed_items": removed,
            "modifications": modifications,
            "injected_exif": injected
        }
        JOBS[job_id]["filename"] = out_name
        JOBS[job_id]["progress"] = 100
        JOBS[job_id]["status"] = "done"

    except Exception as e:
        JOBS[job_id]["status"] = "error"
        JOBS[job_id]["error"] = str(e)
        if os.path.exists(in_path):
            try:
                os.remove(in_path)
            except Exception:
                pass


# ------------------------------------------------------------------
# ROUTES
# ------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/api/verify-pin", methods=["POST"])
def verify_pin():
    data = request.get_json(silent=True) or {}
    pin = data.get("pin", "").strip()
    if pin == PIN_CODE:
        token = create_auth_token()
        resp = make_response(jsonify({"success": True, "token": token}))
        resp.set_cookie("clean_auth", token, max_age=30*86400, httponly=True, samesite="Lax")
        return resp
    return jsonify({"success": False, "error": "PIN salah"}), 403


@app.route("/uploads/<path:filename>")
def get_upload(filename):
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=True)


@app.route("/api/clean", methods=["POST"])
def clean():
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty file"}), 400

    clean_token = uuid.uuid4().hex[:8]
    orig_ext = os.path.splitext(file.filename)[1] or ".jpg"
    if orig_ext.lower() not in [".jpg", ".jpeg", ".png", ".webp"]:
        orig_ext = ".jpg"

    temp_in = os.path.join(UPLOAD_DIR, f"temp_{clean_token}{orig_ext}")
    out_filename = f"cleaned_{clean_token}{orig_ext}"
    out_path = os.path.join(UPLOAD_DIR, out_filename)

    file.save(temp_in)
    try:
        report = process_image_metadata_only(temp_in, out_path, orig_ext)
        if os.path.exists(temp_in):
            os.remove(temp_in)
        return jsonify({
            "success": True,
            "filename": out_filename,
            "download_url": f"/uploads/{out_filename}",
            "report": report
        })
    except Exception as e:
        if os.path.exists(temp_in):
            os.remove(temp_in)
        return jsonify({"error": str(e)}), 500


@app.route("/api/clean-video", methods=["POST"])
def clean_video():
    if "video" not in request.files:
        return jsonify({"error": "No video uploaded"}), 400
    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "Empty file"}), 400

    job_id = uuid.uuid4().hex[:12]
    orig_ext = os.path.splitext(file.filename)[1] or ".mp4"
    if orig_ext.lower() not in [".mp4", ".mov", ".webm", ".mkv"]:
        orig_ext = ".mp4"

    in_path = os.path.join(UPLOAD_DIR, f"video_{job_id}_in{orig_ext}")
    out_filename = f"cleaned_{job_id}{orig_ext}"
    out_path = os.path.join(UPLOAD_DIR, out_filename)
    file.save(in_path)

    try:
        probe = probe_video(in_path)
    except Exception as e:
        if os.path.exists(in_path):
            os.remove(in_path)
        return jsonify({"error": f"Gagal membaca video: {e}"}), 400

    if not probe["width"]:
        if os.path.exists(in_path):
            os.remove(in_path)
        return jsonify({"error": "File bukan video yang valid"}), 400

    JOBS[job_id] = {
        "status": "queued",
        "progress": 0,
        "filename": None,
        "report": None,
        "error": None,
        "meta": {
            "duration": probe["duration"],
            "resolution": f"{probe['width']}x{probe['height']}",
            "fps": probe["fps"],
            "has_audio": probe["has_audio"],
        },
    }

    t = threading.Thread(target=run_video_job_metadata_only, args=(job_id, in_path, out_path, probe), daemon=True)
    t.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/video-status/<job_id>")
def video_status(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    resp = {
        "status": job["status"],
        "progress": job["progress"],
        "meta": job.get("meta"),
    }
    if job["status"] == "done":
        resp.update({
            "success": True,
            "filename": job["filename"],
            "download_url": f"/uploads/{job['filename']}",
            "report": job["report"],
        })
    elif job["status"] == "error":
        resp["error"] = job["error"]
    return jsonify(resp)


# ------------------------------------------------------------------
# YOUTUBE DOWNLOADER
# ------------------------------------------------------------------

import re as _re

YT_YTDLP = "/root/img-env/bin/yt-dlp"

YT_JOBS = {}  # job_id -> {status, progress, filename, title, error}

def yt_progress_hook(job_id):
    def hook(d):
        try:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done = d.get("downloaded_bytes") or 0
                if total:
                    pct = int(done / total * 90)
                    YT_JOBS[job_id]["progress"] = min(pct, 90)
                YT_JOBS[job_id]["status"] = "downloading"
                speed = d.get("_speed_str", "")
                eta = d.get("_eta_str", "")
                YT_JOBS[job_id]["meta_text"] = f"{speed} — ETA {eta}"
            elif d.get("status") == "finished":
                YT_JOBS[job_id]["status"] = "processing"
                YT_JOBS[job_id]["progress"] = 92
                YT_JOBS[job_id]["meta_text"] = "Menggabungkan stream video + audio..."
        except Exception:
            pass
    return hook

def run_youtube_job(job_id, url, mode, quality):
    """Download YouTube via yt-dlp.
    mode: 'video' -> MP4 720p+ (video+audio merge)
          'audio' -> M4A audio terbaik
    """
    out_template = os.path.join(UPLOAD_DIR, f"yt_{job_id}.%(ext)s")
    base_cmd = [
        YT_YTDLP,
        "--no-playlist",
        "--no-warnings",
        "--no-part",
        "--restrict-filenames",
        "--newline",
        "--progress",
        "--quiet",
        "--progress-with-info",
        "--encoding", "utf-8",
        url,
    ]

    try:
        YT_JOBS[job_id]["status"] = "downloading"
        YT_JOBS[job_id]["progress"] = 2

        if mode == "audio":
            # Audio terbaik (m4a, kualitas paling jernih dari sumber)
            cmd = base_cmd[:1] + [
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "--merge-output-format", "m4a",
                "-o", out_template,
                "--print-json",
            ] + base_cmd[1:]
        else:
            # Video 720p ke atas, format gabungan terbaik di bawah quality cap
            fmt = f"bestvideo[height>={quality}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height>={quality}]+bestaudio/best[height>={quality}]/best"
            cmd = [
                YT_YTDLP,
                "--no-playlist",
                "--no-warnings",
                "--restrict-filenames",
                "-f", fmt,
                "--merge-output-format", "mp4",
                "-o", out_template,
                url,
            ]

        # Tambahkan progress hook via python -u dan yt-dlp CLI (pakai --newline untuk parse)
        full_cmd = [c for c in cmd if c not in ("--quiet", "--progress-with-info", "--print-json", "--encoding", "utf-8")]
        # Ganti -f format audio yang konsisten
        if mode == "audio":
            full_cmd = [
                YT_YTDLP,
                "--no-playlist",
                "--no-warnings",
                "--restrict-filenames",
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-o", out_template,
                url,
            ]

        proc = subprocess.Popen(
            full_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
        )

        import re as _regex
        title = ""
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            # Parse progress: [download]  45.3% of 12.34MiB at 2.5MiB/s ETA 00:05
            m = _regex.search(r"\[download\]\s+([\d.]+)%", line)
            if m:
                YT_JOBS[job_id]["progress"] = min(int(float(m.group(1)) * 0.9), 90)
                YT_JOBS[job_id]["status"] = "downloading"
                YT_JOBS[job_id]["meta_text"] = line[:120]
            mt = _regex.search(r"\[Merger\]|\[ExtractAudio\]", line)
            if mt:
                YT_JOBS[job_id]["status"] = "processing"
                YT_JOBS[job_id]["progress"] = 93
                YT_JOBS[job_id]["meta_text"] = "Menggabungkan / konversi stream..."

        proc.wait()
        if proc.returncode != 0:
            raise RuntimeError("yt-dlp gagal mengunduh (exit %d). Video mungkin private/region-locked." % proc.returncode)

        # Cari file hasil
        candidates = [f for f in os.listdir(UPLOAD_DIR) if f.startswith(f"yt_{job_id}.") and not f.endswith(".part")]
        if not candidates:
            raise RuntimeError("File hasil tidak ditemukan di direktori uploads.")
        out_file = candidates[0]
        out_path = os.path.join(UPLOAD_DIR, out_file)

        YT_JOBS[job_id]["progress"] = 95
        YT_JOBS[job_id]["status"] = "processing"

        # Bersihkan metadata YouTube (artist, comment, encoder tag) & suntik tag kamera
        now = datetime.datetime.now()
        try:
            exif_cmd = [
                "exiftool", "-overwrite_original", "-all=",
                "-Make=Apple",
                "-Model=iPhone 15 Pro Max",
                "-Software=18.1.1",
                f"-CreateDate={now.strftime('%Y:%m:%d %H:%M:%S')}",
                out_path
            ]
            subprocess.run(exif_cmd, check=True, timeout=60, capture_output=True)
        except Exception:
            pass  # metadata cleaning optional; jangan gagalkan download

        # Ambil judul video via yt-dlp --get-title (opsional)
        try:
            tproc = subprocess.run(
                [YT_YTDLP, "--no-playlist", "--get-title", url],
                capture_output=True, text=True, timeout=30
            )
            if tproc.returncode == 0 and tproc.stdout.strip():
                title = tproc.stdout.strip().splitlines()[0][:100]
        except Exception:
            pass

        safe_title = _re.sub(r"[^a-zA-Z0-9 _-]", "", title).strip().replace(" ", "_")[:60] or "youtube"
        final_name = f"yt_{safe_title}_{job_id}{os.path.splitext(out_file)[1]}"
        final_path = os.path.join(UPLOAD_DIR, final_name)
        os.rename(out_path, final_path)

        # Probe hasil
        meta = {}
        try:
            p = probe_video(final_path)
            meta = {
                "duration": p.get("duration"),
                "resolution": f"{p.get('width')}x{p.get('height')}" if p.get("width") else None,
                "has_audio": p.get("has_audio"),
            }
        except Exception:
            pass

        YT_JOBS[job_id].update({
            "status": "done",
            "progress": 100,
            "filename": final_name,
            "title": title or safe_title,
            "meta": meta,
        })

    except Exception as e:
        YT_JOBS[job_id]["status"] = "error"
        YT_JOBS[job_id]["error"] = str(e)


@app.route("/api/youtube-download", methods=["POST"])
def youtube_download():
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    mode = data.get("mode", "video")
    quality = int(data.get("quality", 720))

    if not url:
        return jsonify({"error": "URL YouTube wajib diisi"}), 400
    if not (_re.match(r"^https?://(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", url) or _re.match(r"^https?://youtube\.com/shorts/", url)):
        return jsonify({"error": "URL bukan link YouTube yang valid"}), 400
    if mode not in ("video", "audio"):
        return jsonify({"error": "Mode harus 'video' atau 'audio'"}), 400
    if quality not in (720, 1080, 1440, 2160):
        quality = 720
    if not os.path.exists(YT_YTDLP):
        return jsonify({"error": "yt-dlp belum terpasang di server"}), 500

    job_id = uuid.uuid4().hex[:12]
    YT_JOBS[job_id] = {
        "status": "queued",
        "progress": 0,
        "filename": None,
        "title": None,
        "meta": {},
        "error": None,
    }

    t = threading.Thread(target=run_youtube_job, args=(job_id, url, mode, quality), daemon=True)
    t.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/youtube-status/<job_id>")
def youtube_status(job_id):
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    job = YT_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    resp = {
        "status": job["status"],
        "progress": job["progress"],
        "meta_text": job.get("meta_text"),
        "title": job.get("title"),
        "meta": job.get("meta"),
    }
    if job["status"] == "done":
        resp.update({
            "success": True,
            "filename": job["filename"],
            "download_url": f"/uploads/{job['filename']}",
        })
    elif job["status"] == "error":
        resp["error"] = job.get("error")
    return jsonify(resp)


# ------------------------------------------------------------------
# VIDEO -> MP3 EXTRACTOR
# ------------------------------------------------------------------

AUDIO_JOBS = {}  # job_id -> {status, progress, filename, error, meta}

def run_audio_extract_job(job_id, in_path, bitrate):
    try:
        AUDIO_JOBS[job_id]["status"] = "processing"
        AUDIO_JOBS[job_id]["progress"] = 10

        out_filename = f"audio_{job_id}.mp3"
        out_path = os.path.join(UPLOAD_DIR, out_filename)

        # -vn: buang video. Ekstrak audio terbaik, encode MP3 VBR kualitas tinggi (q:a 0 ~ 245kbps)
        if bitrate == "high":
            quality_args = ["-q:a", "0"]
        elif bitrate == "medium":
            quality_args = ["-q:a", "2"]
        else:
            quality_args = ["-b:a", "192k"]

        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", in_path,
            "-vn",
            *quality_args,
            "-ar", "44100",
            "-ac", "2",
            out_path
        ]
        subprocess.run(cmd, check=True, timeout=600)
        AUDIO_JOBS[job_id]["progress"] = 85

        if not os.path.exists(out_path) or os.path.getsize(out_path) == 0:
            raise RuntimeError("File video tidak memiliki track audio, atau format tidak didukung.")

        # Bersihkan metadata lama & suntik tag kamera konsisten dengan fitur lain
        now = datetime.datetime.now()
        try:
            exif_cmd = [
                "exiftool", "-overwrite_original", "-all=",
                "-Make=Apple",
                "-Model=iPhone 15 Pro Max",
                "-Software=18.1.1",
                f"-CreateDate={now.strftime('%Y:%m:%d %H:%M:%S')}",
                out_path
            ]
            subprocess.run(exif_cmd, check=True, timeout=60, capture_output=True)
        except Exception:
            pass

        # Probe durasi hasil
        meta = {}
        try:
            pcmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", out_path
            ]
            pout = subprocess.run(pcmd, capture_output=True, text=True, timeout=30)
            pdata = json.loads(pout.stdout)
            meta = {
                "duration": round(float(pdata.get("format", {}).get("duration", 0) or 0), 1),
                "bitrate_kbps": round(int(pdata.get("format", {}).get("bit_rate", 0) or 0) / 1000),
            }
        except Exception:
            pass

        if os.path.exists(in_path):
            os.remove(in_path)

        AUDIO_JOBS[job_id].update({
            "status": "done",
            "progress": 100,
            "filename": out_filename,
            "meta": meta,
        })

    except Exception as e:
        AUDIO_JOBS[job_id]["status"] = "error"
        AUDIO_JOBS[job_id]["error"] = str(e)
        if os.path.exists(in_path):
            try:
                os.remove(in_path)
            except Exception:
                pass


@app.route("/api/extract-audio", methods=["POST"])
def extract_audio():
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    if "video" not in request.files:
        return jsonify({"error": "No video uploaded"}), 400
    file = request.files["video"]
    if file.filename == "":
        return jsonify({"error": "Empty file"}), 400

    bitrate = request.form.get("bitrate", "high")
    if bitrate not in ("high", "medium", "standard"):
        bitrate = "high"

    job_id = uuid.uuid4().hex[:12]
    orig_ext = os.path.splitext(file.filename)[1] or ".mp4"
    in_path = os.path.join(UPLOAD_DIR, f"audext_{job_id}_in{orig_ext}")
    file.save(in_path)

    AUDIO_JOBS[job_id] = {
        "status": "queued",
        "progress": 0,
        "filename": None,
        "meta": {},
        "error": None,
    }

    t = threading.Thread(target=run_audio_extract_job, args=(job_id, in_path, bitrate), daemon=True)
    t.start()

    return jsonify({"success": True, "job_id": job_id})


@app.route("/api/extract-audio-status/<job_id>")
def extract_audio_status(job_id):
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    job = AUDIO_JOBS.get(job_id)
    if not job:
        return jsonify({"error": "Job tidak ditemukan"}), 404

    resp = {
        "status": job["status"],
        "progress": job["progress"],
        "meta": job.get("meta"),
    }
    if job["status"] == "done":
        resp.update({
            "success": True,
            "filename": job["filename"],
            "download_url": f"/uploads/{job['filename']}",
        })
    elif job["status"] == "error":
        resp["error"] = job.get("error")
    return jsonify(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5055, threaded=True)
