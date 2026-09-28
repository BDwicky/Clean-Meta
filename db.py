import sqlite3
import os
import time
import uuid

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "clean_meta.db")

os.makedirs(DATA_DIR, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Inisialisasi schema database SQLite untuk riwayat per client dan pengaturan admin."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY,
        client_id TEXT NOT NULL,
        title TEXT NOT NULL,
        media_type TEXT NOT NULL,
        type_label TEXT NOT NULL,
        size_str TEXT,
        meta_info TEXT,
        download_url TEXT NOT NULL,
        filename TEXT,
        created_at INTEGER NOT NULL,
        expires_at INTEGER NOT NULL,
        is_permanent INTEGER DEFAULT 0
    )
    """)
    cur.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_client ON history(client_id)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_expires ON history(expires_at)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_hist_created ON history(created_at)")
    conn.commit()
    conn.close()


def is_global_freeze():
    """Cek apakah Mode Admin mengaktifkan 'Tidak Hangus' secara global."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key = 'global_freeze'")
        row = cur.fetchone()
        conn.close()
        return bool(row and row["value"] == "1")
    except Exception:
        return False


def set_global_freeze(freeze: bool):
    """Aktifkan atau nonaktifkan mode 'Tidak Hangus' secara global."""
    conn = get_db()
    cur = conn.cursor()
    val = "1" if freeze else "0"
    cur.execute("""
    INSERT INTO settings (key, value) VALUES ('global_freeze', ?)
    ON CONFLICT(key) DO UPDATE SET value = excluded.value
    """, (val,))
    
    # Jika diaktifkan, proteksi semua riwayat yang ada saat ini agar tidak terhapus
    if freeze:
        cur.execute("UPDATE history SET is_permanent = 1")
    conn.commit()
    conn.close()
    return freeze


def add_history_item(client_id, title, media_type, type_label, size_str="", meta_info="", download_url="", filename="", item_id=None):
    """Menambahkan entri riwayat baru untuk client tertentu."""
    now = int(time.time() * 1000)
    # Default retensi 12 Jam = 43.200.000 ms
    expires_at = now + (12 * 60 * 60 * 1000)
    permanent = 1 if is_global_freeze() else 0
    
    if not item_id:
        item_id = f"hist_{now}_{uuid.uuid4().hex[:6]}"
        
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO history 
    (id, client_id, title, media_type, type_label, size_str, meta_info, download_url, filename, created_at, expires_at, is_permanent)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        item_id,
        client_id,
        title or "Berkas Bersih",
        media_type or "photo",
        type_label or "Berkas",
        size_str or "",
        meta_info or "",
        download_url or "",
        filename or "",
        now,
        expires_at,
        permanent
    ))
    conn.commit()
    conn.close()

    return {
        "id": item_id,
        "client_id": client_id,
        "title": title,
        "type": media_type,
        "typeLabel": type_label,
        "sizeStr": size_str,
        "metaInfo": meta_info,
        "downloadUrl": download_url,
        "filename": filename,
        "createdAt": now,
        "expiresAt": expires_at,
        "is_permanent": bool(permanent)
    }


def get_history_list(client_id=None, is_admin=False, limit=200):
    """
    Mengambil daftar riwayat.
    - Jika is_admin dan client_id is None: Ambil riwayat seluruh klien (All Clients).
    - Jika client biasa: Hanya ambil riwayat milik client_id tersebut yang belum expired atau permanent.
    """
    conn = get_db()
    cur = conn.cursor()
    now = int(time.time() * 1000)

    if is_admin and (client_id is None or client_id == "all"):
        cur.execute("""
        SELECT * FROM history 
        ORDER BY created_at DESC 
        LIMIT ?
        """, (limit,))
    else:
        cur.execute("""
        SELECT * FROM history 
        WHERE client_id = ? AND (is_permanent = 1 OR expires_at > ?)
        ORDER BY created_at DESC 
        LIMIT ?
        """, (client_id, now, limit))

    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r["id"],
            "clientId": r["client_id"],
            "title": r["title"],
            "type": r["media_type"],
            "typeLabel": r["type_label"],
            "sizeStr": r["size_str"],
            "metaInfo": r["meta_info"],
            "downloadUrl": r["download_url"],
            "filename": r["filename"],
            "createdAt": r["created_at"],
            "expiresAt": r["expires_at"],
            "isPermanent": bool(r["is_permanent"])
        })
    return results


def set_item_permanent(item_id, is_permanent: bool):
    """Mengubah status retensi permanen ('Tidak Hangus') pada satu item spesifik oleh Admin."""
    conn = get_db()
    cur = conn.cursor()
    val = 1 if is_permanent else 0
    cur.execute("UPDATE history SET is_permanent = ? WHERE id = ?", (val, item_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def delete_history_item(item_id, client_id=None, is_admin=False):
    """Hapus entri riwayat tertentu."""
    conn = get_db()
    cur = conn.cursor()
    if is_admin:
        cur.execute("DELETE FROM history WHERE id = ?", (item_id,))
    else:
        cur.execute("DELETE FROM history WHERE id = ? AND client_id = ?", (item_id, client_id))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return affected > 0


def clear_history_list(client_id=None, is_admin=False):
    """Bersihkan riwayat (per client atau semua jika admin)."""
    conn = get_db()
    cur = conn.cursor()
    if is_admin and (client_id is None or client_id == "all"):
        cur.execute("DELETE FROM history")
    else:
        cur.execute("DELETE FROM history WHERE client_id = ?", (client_id,))
    conn.commit()
    conn.close()


def get_protected_filenames():
    """Mengembalikan set nama file yang berstatus permanen agar tidak dihapus garbage collector."""
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT filename FROM history WHERE is_permanent = 1 AND filename IS NOT NULL AND filename != ''")
        rows = cur.fetchall()
        conn.close()
        return {r["filename"] for r in rows if r["filename"]}
    except Exception:
        return set()


def purge_expired_records():
    """Menghapus record riwayat non-permanen yang sudah melewati 12 jam."""
    try:
        now = int(time.time() * 1000)
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM history WHERE is_permanent = 0 AND expires_at < ?", (now,))
        conn.commit()
        conn.close()
    except Exception:
        pass


# Auto-init DB saat modul di-import
init_db()
