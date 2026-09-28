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

from flask import Flask, request, jsonify, send_from_directory, make_response, Response, stream_with_context
import db

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = "/var/www/clean-ai" if os.path.exists("/var/www/clean-ai") else BASE_DIR
UPLOAD_DIR = "/var/www/clean-ai/uploads" if os.path.exists("/var/www/clean-ai/uploads") else os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

def cleanup_old_uploads():
    """
    Hapus file unduhan/unggahan lama (> 12 jam) yang tidak diproteksi oleh Mode Admin.
    Jika Admin mengaktifkan 'Tidak Hangus' (Global Freeze), pembersihan dilewati sepenuhnya.
    """
    try:
        if db.is_global_freeze():
            return  # Mode Tidak Hangus Aktif: Semua file dipertahankan permanen
            
        protected_files = db.get_protected_filenames()
        db.purge_expired_records()
        
        now = datetime.datetime.now().timestamp()
        for fname in os.listdir(UPLOAD_DIR):
            fpath = os.path.join(UPLOAD_DIR, fname)
            if os.path.isfile(fpath):
                if fname in protected_files:
                    continue  # Berkas diproteksi permanen oleh admin
                # Retensi 12 Jam = 43,200 detik
                if now - os.path.getmtime(fpath) > 43200:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass
    except Exception:
        pass

PIN_CODE = os.environ.get("ADMIN_PIN", "123123")
SECRET_KEY = "clean-ai-secret-pin-salt-" + hashlib.sha256(PIN_CODE.encode()).hexdigest()[:16]

JOBS = {}  # job_id -> {status, progress, filename, report, error, meta}

def get_request_client_id():
    """Mengambil Client ID unik per perangkat dari header X-Client-ID atau cookie clean_client_id"""
    cid = request.headers.get("X-Client-ID") or request.cookies.get("clean_client_id")
    if not cid or not isinstance(cid, str) or len(cid) > 64:
        cid = f"c_{int(datetime.datetime.now().timestamp())}_{uuid.uuid4().hex[:6]}"
    return cid

def create_admin_token():
    sig = hmac.new(SECRET_KEY.encode(), b"admin_clean_ai_master", hashlib.sha256).hexdigest()
    return f"admin_{sig}"

def verify_admin_token(token):
    if not token or not token.startswith("admin_"):
        return False
    expected = create_admin_token()
    return hmac.compare_digest(token, expected)

# Alias untuk backward compatibility
def create_auth_token():
    return create_admin_token()

def verify_auth_token(token):
    return verify_admin_token(token)

def is_admin_authenticated():
    """Cek apakah request terautentikasi sebagai Admin"""
    cookie_token = request.cookies.get("clean_admin_auth") or request.cookies.get("clean_auth")
    if verify_admin_token(cookie_token):
        return True
    auth_header = request.headers.get("X-Admin-Token") or request.headers.get("X-Auth-Token")
    if verify_admin_token(auth_header):
        return True
    header_pin = request.headers.get("X-PIN-Code")
    if header_pin == PIN_CODE:
        return True
    qs_token = request.args.get("admin_token") or request.args.get("token") or ""
    if verify_admin_token(qs_token):
        return True
    return False

def is_request_authenticated():
    return is_admin_authenticated()

@app.before_request
def check_auth():
    """
    Public Mode: Semua fitur sanitasi, youtube, ekstrak audio, dan unduhan terbuka bebas bagi publik.
    Hanya endpoint admin yang diproteksi.
    """
    if request.path.startswith("/api/admin/"):
        if request.path in ("/api/admin/login", "/api/admin/status"):
            return None
        if not is_admin_authenticated():
            return jsonify({"error": "Unauthorized: Mode Admin diperlukan", "admin_required": True}), 401


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


CAMERA_PRESETS = {
    "iphone_15_pro_max": {
        "label": "Apple iPhone 15 Pro Max",
        "make": "Apple",
        "model": "iPhone 15 Pro Max",
        "software": "18.1.1",
        "software_display": "iOS 18.1.1",
        "lens_model": "iPhone 15 Pro Max back triple camera 6.765mm f/1.78",
        "fnumber": "1.78",
        "focal_length": "6.765",
        "focal_length_display": "24mm (6.765mm optik)",
        "iso_choices": ["50", "64", "80", "100", "125"],
        "exp_choices": ["1/60", "1/100", "1/120", "1/250", "1/500"],
    },
    "samsung_s24_ultra": {
        "label": "Samsung Galaxy S24 Ultra",
        "make": "Samsung",
        "model": "SM-S928B",
        "software": "One UI 6.1",
        "software_display": "One UI 6.1 (Android 14)",
        "lens_model": "Samsung Galaxy S24 Ultra back camera 6.3mm f/1.7",
        "fnumber": "1.7",
        "focal_length": "6.3",
        "focal_length_display": "23mm (6.3mm optik)",
        "iso_choices": ["50", "64", "80", "100", "125"],
        "exp_choices": ["1/60", "1/100", "1/125", "1/250", "1/500"],
    },
    "google_pixel_8_pro": {
        "label": "Google Pixel 8 Pro",
        "make": "Google",
        "model": "Pixel 8 Pro",
        "software": "Android 14",
        "software_display": "Android 14 (Build UD1A)",
        "lens_model": "Google Pixel 8 Pro back camera 6.9mm f/1.68",
        "fnumber": "1.68",
        "focal_length": "6.9",
        "focal_length_display": "25mm (6.9mm optik)",
        "iso_choices": ["40", "50", "64", "100", "125"],
        "exp_choices": ["1/60", "1/100", "1/120", "1/250", "1/500"],
    },
    "sony_a7_iv": {
        "label": "Sony Alpha 7 IV (ILCE-7M4)",
        "make": "Sony",
        "model": "ILCE-7M4",
        "software": "ILCE-7M4 v2.00",
        "software_display": "ILCE-7M4 Firmware v2.00",
        "lens_model": "FE 24-70mm F2.8 GM II",
        "fnumber": "2.8",
        "focal_length": "35.0",
        "focal_length_display": "35mm (FE 24-70mm F2.8 GM II)",
        "iso_choices": ["100", "160", "200", "320", "400"],
        "exp_choices": ["1/125", "1/160", "1/200", "1/250", "1/500"],
    },
    "canon_eos_r5": {
        "label": "Canon EOS R5",
        "make": "Canon",
        "model": "Canon EOS R5",
        "software": "Firmware Version 1.9.0",
        "software_display": "Canon EOS R5 Firmware 1.9.0",
        "lens_model": "RF24-70mm F2.8 L IS USM",
        "fnumber": "2.8",
        "focal_length": "50.0",
        "focal_length_display": "50mm (RF24-70mm F2.8 L IS USM)",
        "iso_choices": ["100", "160", "200", "250", "400"],
        "exp_choices": ["1/125", "1/160", "1/200", "1/250", "1/500"],
    },
    "fujifilm_xt5": {
        "label": "Fujifilm X-T5",
        "make": "FUJIFILM",
        "model": "X-T5",
        "software": "Digital Camera X-T5 Ver.2.01",
        "software_display": "Fujifilm Firmware Ver.2.01",
        "lens_model": "XF16-55mmF2.8 R LM WR",
        "fnumber": "2.8",
        "focal_length": "23.0",
        "focal_length_display": "23mm (35mm ekivalen)",
        "iso_choices": ["125", "160", "200", "250", "400"],
        "exp_choices": ["1/125", "1/160", "1/250", "1/500", "1/1000"],
    }
}


def process_image_metadata_only(input_path, output_path, ext, camera_preset="iphone_15_pro_max"):
    """
    Membersihkan 100% metadata tanpa mengubah/mengompresi data piksel asli.
    Piksel gambar 100% bit-exact dari aslinya, lalu disuntikkan EXIF kamera asli sesuai preset.
    """
    cam = CAMERA_PRESETS.get(camera_preset, CAMERA_PRESETS["iphone_15_pro_max"])
    removed_items = read_metadata_summary(input_path)
    shutil.copy2(input_path, output_path)

    # 1. Hapus seluruh metadata (C2PA, IPTC, XMP, EXIF lama, chunk text AI)
    strip_cmd = ["exiftool", "-overwrite_original", "-all=", output_path]
    subprocess.run(strip_cmd, check=True, timeout=60)

    now = datetime.datetime.now()
    now_str = now.strftime("%Y:%m:%d %H:%M:%S")
    exp_time = random.choice(cam.get("exp_choices", ["1/60", "1/100", "1/120", "1/250"]))
    iso = random.choice(cam.get("iso_choices", ["50", "64", "80", "100", "125"]))

    ext_clean = ext.lower().replace(".", "")
    injected_exif = []

    if ext_clean in ("jpg", "jpeg"):
        # Format JPEG mendukung penuh standarisasi EXIF kamera
        inject_cmd = [
            "exiftool", "-overwrite_original",
            f"-Make={cam['make']}",
            f"-Model={cam['model']}",
            f"-Software={cam['software']}",
            f"-Exif:DateTimeOriginal={now_str}",
            f"-Exif:CreateDate={now_str}",
            f"-Exif:ModifyDate={now_str}",
            f"-Exif:ExposureTime={exp_time}",
            f"-Exif:FNumber={cam['fnumber']}",
            f"-Exif:ISO={iso}",
            f"-Exif:FocalLength={cam['focal_length']}",
            f"-Exif:LensModel={cam['lens_model']}",
            output_path
        ]
        subprocess.run(inject_cmd, check=True, timeout=60)
        injected_exif = [
            ("Make", cam["make"]),
            ("Model", cam["model"]),
            ("Software", cam.get("software_display", cam["software"])),
            ("DateTimeOriginal", now_str),
            ("ExposureTime", exp_time),
            ("FNumber", f"f/{cam['fnumber']}"),
            ("ISO", iso),
            ("FocalLength", cam.get("focal_length_display", f"{cam['focal_length']}mm")),
            ("LensModel", cam["lens_model"]),
        ]
    else:
        # PNG/WEBP dll: Tag umum device capture
        inject_cmd = [
            "exiftool", "-overwrite_original",
            f"-Make={cam['make']}",
            f"-Model={cam['model']}",
            f"-Software={cam['software']}",
            output_path
        ]
        subprocess.run(inject_cmd, check=True, timeout=60)
        injected_exif = [
            ("Make", cam["make"]),
            ("Model", cam["model"]),
            ("Software", cam.get("software_display", cam["software"])),
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
            "action": f"Disuntikkan EXIF resmi kamera {cam['label']} (ISO, Shutter, Lensa, Tanggal) seolah difoto langsung."
        }
    ]

    return {
        "removed_items": removed_items,
        "modifications": modifications,
        "injected_exif": injected_exif,
        "camera_label": cam["label"]
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


def run_video_job_metadata_only(job_id, in_path, out_path, probe, camera_preset="iphone_15_pro_max"):
    """
    Video diproses secara STREAM COPY (-c copy).
    100% tanpa kompresi ulang, tanpa penurunan kualitas (lossless copy),
    tanpa filter atau effect visual apapun.
    Metadata container/stream AI dibuang lalu diinjeksi metadata kamera asli sesuai preset.
    """
    cam = CAMERA_PRESETS.get(camera_preset, CAMERA_PRESETS["iphone_15_pro_max"])
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

        # 2. Hapus sisa metadata dan suntikkan tag kamera QuickTime
        now = datetime.datetime.now()
        create_str = now.strftime("%Y-%m-%d %H:%M:%S")

        exif_cmd = [
            "exiftool", "-overwrite_original",
            "-all=",
            f"-Make={cam['make']}",
            f"-Model={cam['model']}",
            f"-Software={cam['software']}",
            f"-CreateDate={now.strftime('%Y:%m:%d %H:%M:%S')}",
            f"-ModifyDate={now.strftime('%Y:%m:%d %H:%M:%S')}",
            out_path,
        ]
        subprocess.run(exif_cmd, check=True, timeout=120)
        JOBS[job_id]["progress"] = 95

        if os.path.exists(in_path):
            os.remove(in_path)

        injected = [
            ("Make", cam["make"]),
            ("Model", cam["model"]),
            ("Software", cam.get("software_display", cam["software"])),
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
                "action": f"Disuntikkan tag kamera {cam['label']} sebagai metadata rekaman asli."
            }
        ]

        out_name = os.path.basename(out_path)
        JOBS[job_id]["report"] = {
            "removed_items": removed,
            "modifications": modifications,
            "injected_exif": injected,
            "camera_label": cam["label"]
        }
        JOBS[job_id]["camera_label"] = cam["label"]
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


@app.route("/favicon.svg")
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(STATIC_DIR, "favicon.svg", mimetype="image/svg+xml")


@app.route("/api/admin/login", methods=["POST"])
@app.route("/api/verify-pin", methods=["POST"])  # Alias untuk kompatibilitas
def admin_login():
    data = request.get_json(silent=True) or {}
    pin = (data.get("pin") or "").strip()
    if pin == PIN_CODE:
        token = create_admin_token()
        resp = make_response(jsonify({
            "success": True, 
            "token": token, 
            "isAdmin": True,
            "globalFreeze": db.is_global_freeze()
        }))
        is_https = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        resp.set_cookie(
            "clean_admin_auth",
            token,
            max_age=30*86400,
            httponly=True,
            samesite="Lax",
            secure=is_https
        )
        resp.set_cookie(
            "clean_auth",
            token,
            max_age=30*86400,
            httponly=True,
            samesite="Lax",
            secure=is_https
        )
        return resp
    return jsonify({"success": False, "error": "PIN Admin salah"}), 403


@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    resp = make_response(jsonify({"success": True}))
    resp.set_cookie("clean_admin_auth", "", expires=0)
    resp.set_cookie("clean_auth", "", expires=0)
    return resp


@app.route("/api/admin/status", methods=["GET"])
@app.route("/api/auth-status", methods=["GET"])  # Alias
def admin_status():
    is_adm = is_admin_authenticated()
    return jsonify({
        "authenticated": is_adm,
        "isAdmin": is_adm,
        "token": create_admin_token() if is_adm else None,
        "globalFreeze": db.is_global_freeze()
    })


@app.route("/api/admin/toggle-freeze", methods=["POST"])
def admin_toggle_freeze():
    if not is_admin_authenticated():
        return jsonify({"error": "Unauthorized: Mode Admin diperlukan"}), 401
    data = request.get_json(silent=True) or {}
    freeze = bool(data.get("freeze"))
    db.set_global_freeze(freeze)
    return jsonify({"success": True, "globalFreeze": db.is_global_freeze()})


@app.route("/api/admin/toggle-item-retention", methods=["POST"])
def admin_toggle_item():
    if not is_admin_authenticated():
        return jsonify({"error": "Unauthorized: Mode Admin diperlukan"}), 401
    data = request.get_json(silent=True) or {}
    item_id = data.get("id")
    is_perm = bool(data.get("isPermanent"))
    ok = db.set_item_permanent(item_id, is_perm)
    return jsonify({"success": ok, "isPermanent": is_perm})


@app.route("/api/history", methods=["GET", "POST", "DELETE"])
def handle_history():
    client_id = get_request_client_id()
    is_adm = is_admin_authenticated()

    if request.method == "GET":
        scope = request.args.get("scope", "client")
        if scope == "all" and is_adm:
            items = db.get_history_list(client_id=None, is_admin=True)
        else:
            items = db.get_history_list(client_id=client_id, is_admin=False)
        resp = make_response(jsonify({
            "success": True,
            "clientId": client_id,
            "isAdmin": is_adm,
            "globalFreeze": db.is_global_freeze(),
            "items": items
        }))
        if not request.cookies.get("clean_client_id"):
            resp.set_cookie("clean_client_id", client_id, max_age=365*86400, samesite="Lax")
        return resp

    elif request.method == "POST":
        data = request.get_json(silent=True) or {}
        title = data.get("title") or "Berkas Bersih"
        mtype = data.get("type") or "photo"
        tlabel = data.get("typeLabel") or "Berkas"
        sizestr = data.get("sizeStr") or ""
        metainfo = data.get("metaInfo") or ""
        dl_url = data.get("downloadUrl") or ""
        fname = data.get("filename") or ""
        item_id = data.get("id")

        record = db.add_history_item(
            client_id=client_id,
            title=title,
            media_type=mtype,
            type_label=tlabel,
            size_str=sizestr,
            meta_info=metainfo,
            download_url=dl_url,
            filename=fname,
            item_id=item_id
        )
        resp = make_response(jsonify({"success": True, "item": record}))
        if not request.cookies.get("clean_client_id"):
            resp.set_cookie("clean_client_id", client_id, max_age=365*86400, samesite="Lax")
        return resp

    elif request.method == "DELETE":
        scope = request.args.get("scope", "client")
        if scope == "all" and is_adm:
            db.clear_history_list(is_admin=True)
        else:
            db.clear_history_list(client_id=client_id, is_admin=False)
        return jsonify({"success": True})


@app.route("/api/history/<item_id>", methods=["DELETE"])
def delete_history_single(item_id):
    client_id = get_request_client_id()
    is_adm = is_admin_authenticated()
    ok = db.delete_history_item(item_id, client_id=client_id, is_admin=is_adm)
    return jsonify({"success": ok})


@app.route("/uploads/<path:filename>")
def get_upload(filename):
    # Jika parameter ?dl=1 disertakan, unduh sebagai attachment; jika tidak, izinkan inline preview/stream browser
    as_attachment = request.args.get("dl") == "1" or request.args.get("download") == "1"
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=as_attachment)


@app.route("/api/clean", methods=["POST"])
def clean():
    if "image" not in request.files:
        return jsonify({"error": "No image uploaded"}), 400
    file = request.files["image"]
    if file.filename == "":
        return jsonify({"error": "Empty file"}), 400

    camera_preset = request.form.get("camera_preset", "iphone_15_pro_max")
    if camera_preset not in CAMERA_PRESETS:
        camera_preset = "iphone_15_pro_max"

    clean_token = uuid.uuid4().hex[:8]
    cleanup_old_uploads()
    orig_ext = os.path.splitext(file.filename)[1] or ".jpg"
    if orig_ext.lower() not in [".jpg", ".jpeg", ".png", ".webp"]:
        orig_ext = ".jpg"

    temp_in = os.path.join(UPLOAD_DIR, f"temp_{clean_token}{orig_ext}")
    out_filename = f"cleaned_{clean_token}{orig_ext}"
    out_path = os.path.join(UPLOAD_DIR, out_filename)

    file.save(temp_in)
    try:
        report = process_image_metadata_only(temp_in, out_path, orig_ext, camera_preset)
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

    camera_preset = request.form.get("camera_preset", "iphone_15_pro_max")
    if camera_preset not in CAMERA_PRESETS:
        camera_preset = "iphone_15_pro_max"

    job_id = uuid.uuid4().hex[:12]
    cleanup_old_uploads()
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

    t = threading.Thread(target=run_video_job_metadata_only, args=(job_id, in_path, out_path, probe, camera_preset), daemon=True)
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

YT_YTDLP = "/root/img-env/bin/yt-dlp" if os.path.exists("/root/img-env/bin/yt-dlp") else (shutil.which("yt-dlp") or "yt-dlp")

def get_ytdlp_bin():
    if os.path.exists(YT_YTDLP):
        return YT_YTDLP
    found = shutil.which(YT_YTDLP) or shutil.which("yt-dlp")
    return found or "yt-dlp"

def clean_youtube_url(raw):
    """Normalize input URL: handles raw strings, embedded links from mobile share sheets, and missing protocols."""
    if not raw:
        return ""
    raw = str(raw).strip()
    # Match URL embedded in text (e.g. from Android/iOS share sheet: "Tonton video ini https://youtu.be/...")
    m = _re.search(r"(https?://\S+)", raw)
    if m:
        return m.group(1).rstrip(",;)>]")
    # If pasted without protocol (e.g. youtube.com/watch?v=... or youtu.be/...)
    if _re.match(r"^(www\.|m\.|music\.)?(youtube\.com|youtu\.be)/", raw, _re.IGNORECASE):
        return "https://" + raw
    return raw

def is_valid_youtube_url(url):
    """Check if URL matches any valid YouTube format."""
    if not url:
        return False
    pattern = r"^https?://([a-zA-Z0-9_-]+\.)?(youtube\.com|youtu\.be)/"
    return bool(_re.match(pattern, url, _re.IGNORECASE))

YT_JOBS = {}  # job_id -> {status, progress, filename, title, error}

def run_youtube_job(job_id, url, mode, quality):
    """Download YouTube via yt-dlp.
    mode: 'video' -> MP4 up to target quality (VP9/AV1/H264 merged into MP4)
          'audio' -> M4A highest audio quality
    """
    cleanup_old_uploads()
    ytdlp_bin = get_ytdlp_bin()
    out_template = os.path.join(UPLOAD_DIR, f"yt_{job_id}.%(ext)s")

    try:
        YT_JOBS[job_id]["status"] = "downloading"
        YT_JOBS[job_id]["progress"] = 2

        if mode == "audio":
            full_cmd = [
                ytdlp_bin,
                "--no-playlist",
                "--no-warnings",
                "--no-check-certificates",
                "--newline",
                "--progress",
                "--restrict-filenames",
                "-f", "bestaudio[ext=m4a]/bestaudio",
                "-o", out_template,
                url,
            ]
        else:
            fmt = (
                f"bestvideo[ext=mp4][height<={quality}]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={quality}]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={quality}]+bestaudio/"
                f"best[height<={quality}]/best"
            )
            full_cmd = [
                ytdlp_bin,
                "--no-playlist",
                "--no-warnings",
                "--no-check-certificates",
                "--newline",
                "--progress",
                "--restrict-filenames",
                "-f", fmt,
                "--merge-output-format", "mp4",
                "--postprocessor-args", "ffmpeg:-movflags +faststart",
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
            raise RuntimeError("yt-dlp gagal mengunduh (exit %d). Video mungkin dibatasi usia, private, atau memerlukan autentikasi." % proc.returncode)

        # Cari file hasil
        candidates = [f for f in os.listdir(UPLOAD_DIR) if f.startswith(f"yt_{job_id}.") and not f.endswith(".part") and not f.endswith(".ytdl")]
        if not candidates:
            raise RuntimeError("File hasil tidak ditemukan di direktori uploads.")
        out_file = candidates[0]
        out_path = os.path.join(UPLOAD_DIR, out_file)

        YT_JOBS[job_id]["progress"] = 95
        YT_JOBS[job_id]["status"] = "processing"

        # Ambil judul video via yt-dlp --get-title (opsional)
        try:
            tproc = subprocess.run(
                [ytdlp_bin, "--no-playlist", "--get-title", url],
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


@app.route("/api/youtube-info", methods=["POST"])
def youtube_info():
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json(silent=True) or {}
        raw_url = (data.get("url") or "").strip()
        url = clean_youtube_url(raw_url)

        if not url:
            return jsonify({"error": "URL YouTube wajib diisi"}), 400
        if not is_valid_youtube_url(url):
            return jsonify({"error": "URL bukan link YouTube yang valid"}), 400

        ytdlp_bin = get_ytdlp_bin()
        if not (os.path.exists(ytdlp_bin) or shutil.which(ytdlp_bin)):
            return jsonify({"error": "yt-dlp belum terpasang di server"}), 500

        cmd = [
            ytdlp_bin,
            "-j",
            "--skip-download",
            "--no-playlist",
            "--no-warnings",
            "--no-check-certificates",
            url
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "Gagal membaca informasi video dari YouTube.").strip()
            return jsonify({"error": err[:300]}), 400

        raw_stdout = proc.stdout.strip()
        info = {}
        for line in raw_stdout.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    info = json.loads(line)
                    break
                except Exception:
                    pass
        if not info and raw_stdout:
            try:
                info = json.loads(raw_stdout)
            except Exception:
                pass

        formats = info.get("formats", [])

        heights = set()
        widths = set()
        max_fps = 30
        has_1080_enhanced = False

        for f in formats:
            vcodec = f.get("vcodec")
            if vcodec == "none":
                continue

            h = f.get("height")
            w = f.get("width")
            fps = f.get("fps") or 0
            if fps and fps > max_fps:
                try:
                    max_fps = int(fps)
                except Exception:
                    pass

            note = str(f.get("format_note") or "").lower()
            qlabel = str(f.get("quality_label") or "").lower()

            if "premium" in note or "enhanced" in note or "premium" in qlabel:
                has_1080_enhanced = True

            for text in (note, qlabel):
                if not text:
                    continue
                if "2160" in text or "4k" in text:
                    heights.add(2160)
                elif "1440" in text or "2k" in text:
                    heights.add(1440)
                elif "1080" in text:
                    heights.add(1080)
                elif "720" in text:
                    heights.add(720)
                elif "480" in text:
                    heights.add(480)
                elif "360" in text:
                    heights.add(360)
                elif "240" in text:
                    heights.add(240)
                elif "144" in text:
                    heights.add(144)

            if h:
                try:
                    heights.add(int(h))
                except (ValueError, TypeError):
                    pass
            if w:
                try:
                    widths.add(int(w))
                except (ValueError, TypeError):
                    pass

            res_str = str(f.get("resolution") or "")
            if "x" in res_str:
                try:
                    parts = res_str.split("x")
                    widths.add(int(parts[0]))
                    heights.add(int(parts[1]))
                except Exception:
                    pass

        detected_resolutions = []
        if any(h >= 2000 for h in heights) or any(w >= 3800 for w in widths):
            detected_resolutions.append(2160)
        if any(1400 <= h < 2000 for h in heights) or any(2500 <= w < 3800 for w in widths):
            detected_resolutions.append(1440)
        if any(1000 <= h < 1400 for h in heights) or any(1800 <= w < 2500 for w in widths) or has_1080_enhanced:
            detected_resolutions.append(1080)
        if any(700 <= h < 1000 for h in heights) or any(1200 <= w < 1800 for w in widths):
            detected_resolutions.append(720)
        if any(460 <= h < 700 for h in heights) or any(800 <= w < 1200 for w in widths):
            detected_resolutions.append(480)
        if any(300 <= h < 460 for h in heights) or any(500 <= w < 800 for w in widths):
            detected_resolutions.append(360)
        if any(h < 300 for h in heights if h > 0):
            detected_resolutions.append(240)

        detected_resolutions = sorted(list(set(detected_resolutions)))
        if not detected_resolutions:
            detected_resolutions = [360, 480, 720, 1080]

        max_h = max(detected_resolutions)
        duration_sec = info.get("duration") or 0
        minutes = int(duration_sec // 60)
        seconds = int(duration_sec % 60)
        duration_str = f"{minutes:02d}:{seconds:02d}" if duration_sec else "N/A"

        thumbnails = info.get("thumbnails", [])
        thumb_url = info.get("thumbnail") or (thumbnails[-1]["url"] if thumbnails else "")

        return jsonify({
            "success": True,
            "title": info.get("title", "Video YouTube"),
            "uploader": info.get("uploader") or info.get("channel", "YouTube Channel"),
            "thumbnail": thumb_url,
            "duration": duration_sec,
            "duration_str": duration_str,
            "max_resolution": max_h,
            "max_fps": max_fps,
            "has_1080_enhanced": has_1080_enhanced,
            "resolutions": detected_resolutions
        })
    except subprocess.TimeoutExpired:
        return jsonify({"error": "Waktu periksa video habis (timeout). Server tetap bisa mengunduh 1080p langsung."}), 504
    except Exception as e:
        return jsonify({"error": f"Gagal mengecek video: {str(e)}"}), 500


@app.route("/api/youtube-download", methods=["POST"])
def youtube_download():
    if not is_request_authenticated():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        data = request.get_json(silent=True) or {}
        raw_url = (data.get("url") or "").strip()
        url = clean_youtube_url(raw_url)
        mode = str(data.get("mode") or "video").strip().lower()

        # Safely parse quality
        raw_q = data.get("quality")
        try:
            quality = int(raw_q or 720)
        except (ValueError, TypeError):
            digits = "".join(c for c in str(raw_q) if c.isdigit())
            quality = int(digits) if digits else 720

        if not url:
            return jsonify({"error": "URL YouTube wajib diisi"}), 400
        if not is_valid_youtube_url(url):
            return jsonify({"error": "URL bukan link YouTube yang valid"}), 400
        if mode not in ("video", "audio"):
            mode = "video"
        if quality not in (144, 240, 360, 480, 720, 1080, 1440, 2160):
            quality = 720

        ytdlp_bin = get_ytdlp_bin()
        if not (os.path.exists(ytdlp_bin) or shutil.which(ytdlp_bin)):
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
    except Exception as e:
        return jsonify({"error": f"Gagal memulai unduhan: {str(e)}"}), 500


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


@app.route("/api/youtube-stream")
def youtube_stream():
    """Direct streaming download route — yt-dlp stdout di-pipe langsung ke browser.
    Tidak ada file yang disimpan di disk. Browser / IDM menerima byte secara real-time.
    Dipanggil via GET dengan query params: url, mode, quality, token.
    """
    # Streaming terbuka untuk publik
    raw_url = (request.args.get("url") or "").strip()
    url = clean_youtube_url(raw_url)
    mode = (request.args.get("mode") or "video").strip().lower()
    raw_q = request.args.get("quality") or "720"
    try:
        quality = int(raw_q)
    except (ValueError, TypeError):
        quality = 720

    if not url or not is_valid_youtube_url(url):
        return Response("URL YouTube tidak valid", status=400)
    if mode not in ("video", "audio"):
        mode = "video"
    if quality not in (144, 240, 360, 480, 720, 1080, 1440, 2160):
        quality = 720

    ytdlp_bin = get_ytdlp_bin()
    if not (os.path.exists(ytdlp_bin) or shutil.which(ytdlp_bin)):
        return Response("yt-dlp tidak tersedia di server", status=500)

    # Tentukan format dan nama file output
    if mode == "audio":
        fmt = "bestaudio[ext=m4a]/bestaudio"
        mime = "audio/mp4"
        dl_filename = "audio_youtube.m4a"
    else:
        fmt = f"bestvideo[height<={quality}]+bestaudio[ext=m4a]/bestvideo[height<={quality}]+bestaudio/best[height<={quality}]/best"
        mime = "video/mp4"
        dl_filename = f"video_{quality}p_youtube.mp4"

    cmd = [
        ytdlp_bin,
        "--no-playlist",
        "--no-warnings",
        "--no-check-certificates",
        "-f", fmt,
        "-o", "-",   # Output ke stdout — tidak ada file di disk
        url,
    ]

    def generate():
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=65536,
            )
            while True:
                chunk = proc.stdout.read(65536)  # Baca 64KB per chunk
                if not chunk:
                    break
                yield chunk
            proc.wait()
        except GeneratorExit:
            # Pengguna menutup koneksi / membatalkan unduhan
            try:
                proc.kill()
            except Exception:
                pass
        except Exception:
            pass

    headers = {
        "Content-Disposition": f'attachment; filename="{dl_filename}"',
        "Content-Type": mime,
        "X-Accel-Buffering": "no",      # Nonaktifkan buffering Nginx
        "Cache-Control": "no-cache",
    }
    return Response(stream_with_context(generate()), headers=headers, mimetype=mime)


# ------------------------------------------------------------------
# VIDEO -> MP3 EXTRACTOR
# ------------------------------------------------------------------

AUDIO_JOBS = {}  # job_id -> {status, progress, filename, error, meta}

def run_audio_extract_job(job_id, in_path, bitrate):
    cleanup_old_uploads()
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
