import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(os.environ.get("AZBAZE_DB", "/var/lib/az-baze/auth.db"))
KNOWLEDGE_ROOT = Path(os.environ.get("AZBAZE_KNOWLEDGE_ROOT", "/mnt/qnap-obuchenie"))
COMPAT_ROOT = Path(os.environ.get("AZBAZE_KNOWLEDGE_COMPAT_ROOT", "/var/lib/az-baze/knowledge-compatible"))
CACHE_LIMIT_BYTES = int(os.environ.get("AZBAZE_KNOWLEDGE_COMPAT_LIMIT_BYTES", str(12 * 1024**3)))
MIN_FREE_BYTES = int(os.environ.get("AZBAZE_KNOWLEDGE_MIN_FREE_BYTES", str(4 * 1024**3)))
FFPROBE = os.environ.get("AZBAZE_FFPROBE", "/usr/bin/ffprobe")
FFMPEG = os.environ.get("AZBAZE_FFMPEG", "/usr/bin/ffmpeg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_video_compat (
    entry_id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL,
    source_size INTEGER NOT NULL,
    source_mtime TEXT,
    status TEXT NOT NULL CHECK(status IN (
        'pending','probing','source_ok','converting','ready','evicted','error'
    )),
    requested_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_accessed_at TEXT,
    codec_name TEXT,
    pix_fmt TEXT,
    output_size INTEGER,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_knowledge_video_compat_queue
    ON knowledge_video_compat(status, requested_at);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def init_schema(conn):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)


def cache_path(entry_id):
    return COMPAT_ROOT / f"{entry_id}.mp4"


def _signature_matches(state, entry):
    return (
        int(state["source_size"]) == int(entry["size_bytes"])
        and str(state["source_mtime"] or "") == str(entry["modified_at"] or "")
        and str(state["relative_path"]) == str(entry["relative_path"])
    )


def request_video(conn, entry):
    """Register a video for compatibility handling and return current state."""
    init_schema(conn)
    now = utc_now()
    state = conn.execute(
        "SELECT * FROM knowledge_video_compat WHERE entry_id=?",
        (entry["id"],),
    ).fetchone()

    needs_reset = False
    if state is None:
        needs_reset = True
    elif not _signature_matches(state, entry):
        needs_reset = True
    elif state["status"] == "ready" and not cache_path(entry["id"]).is_file():
        needs_reset = True
    elif state["status"] == "evicted":
        needs_reset = True

    if needs_reset:
        old = cache_path(entry["id"])
        try:
            old.unlink()
        except FileNotFoundError:
            pass
        conn.execute(
            """
            INSERT INTO knowledge_video_compat(
                entry_id,relative_path,source_size,source_mtime,status,
                requested_at,updated_at,last_accessed_at,codec_name,pix_fmt,
                output_size,error
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(entry_id) DO UPDATE SET
                relative_path=excluded.relative_path,
                source_size=excluded.source_size,
                source_mtime=excluded.source_mtime,
                status='pending',
                requested_at=excluded.requested_at,
                updated_at=excluded.updated_at,
                last_accessed_at=NULL,
                codec_name=NULL,
                pix_fmt=NULL,
                output_size=NULL,
                error=NULL
            """,
            (
                entry["id"], entry["relative_path"], int(entry["size_bytes"]),
                entry["modified_at"], "pending", now, now, None,
                None, None, None, None,
            ),
        )
        conn.commit()
        state = conn.execute(
            "SELECT * FROM knowledge_video_compat WHERE entry_id=?",
            (entry["id"],),
        ).fetchone()

    if state["status"] in ("ready", "source_ok"):
        conn.execute(
            "UPDATE knowledge_video_compat SET last_accessed_at=? WHERE entry_id=?",
            (now, entry["id"]),
        )
        conn.commit()
        state = conn.execute(
            "SELECT * FROM knowledge_video_compat WHERE entry_id=?",
            (entry["id"],),
        ).fetchone()
    return state


def retry_video(conn, entry_id):
    init_schema(conn)
    now = utc_now()
    conn.execute(
        """
        UPDATE knowledge_video_compat
        SET status='pending',requested_at=?,updated_at=?,error=NULL
        WHERE entry_id=?
        """,
        (now, now, entry_id),
    )
    conn.commit()


def mark_accessed(conn, entry_id):
    init_schema(conn)
    conn.execute(
        "UPDATE knowledge_video_compat SET last_accessed_at=? WHERE entry_id=?",
        (utc_now(), entry_id),
    )
    conn.commit()


def _run_json(args, timeout=30):
    proc = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=True,
    )
    return json.loads(proc.stdout)


def probe(path):
    data = _run_json([
        FFPROBE, "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    streams = data.get("streams") or []
    videos = [
        s for s in streams
        if s.get("codec_type") == "video"
        and int((s.get("disposition") or {}).get("attached_pic") or 0) != 1
    ]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    video = videos[0] if videos else None
    audio = audios[0] if audios else None
    fmt = data.get("format") or {}
    try:
        duration = float(fmt.get("duration") or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return {
        "video": video,
        "audio": audio,
        "duration": duration,
        "format_name": str(fmt.get("format_name") or ""),
    }


def source_is_browser_compatible(path, meta):
    video = meta.get("video") or {}
    audio = meta.get("audio") or {}
    suffix = path.suffix.lower()
    video_ok = (
        suffix in {".mp4", ".m4v"}
        and video.get("codec_name") == "h264"
        and video.get("pix_fmt") in {"yuv420p", "yuvj420p"}
    )
    audio_codec = audio.get("codec_name")
    audio_ok = not audio or audio_codec in {"aac", "mp3"}
    return bool(video_ok and audio_ok)


def _cache_bytes():
    total = 0
    if not COMPAT_ROOT.is_dir():
        return 0
    for path in COMPAT_ROOT.glob("*.mp4"):
        try:
            total += path.stat().st_size
        except OSError:
            pass
    return total


def _evict_one(conn, exclude_entry_id):
    row = conn.execute(
        """
        SELECT entry_id
        FROM knowledge_video_compat
        WHERE status='ready' AND entry_id<>?
        ORDER BY
            CASE WHEN last_accessed_at IS NULL THEN 0 ELSE 1 END,
            last_accessed_at ASC,
            updated_at ASC
        LIMIT 1
        """,
        (exclude_entry_id,),
    ).fetchone()
    if not row:
        return False
    path = cache_path(row["entry_id"])
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    conn.execute(
        """
        UPDATE knowledge_video_compat
        SET status='evicted',output_size=NULL,error=NULL,updated_at=?
        WHERE entry_id=?
        """,
        (utc_now(), row["entry_id"]),
    )
    conn.commit()
    return True


def ensure_capacity(conn, entry_id, estimated_output):
    COMPAT_ROOT.mkdir(parents=True, exist_ok=True)
    while True:
        cache_used = _cache_bytes()
        free = shutil.disk_usage(COMPAT_ROOT).free
        if (
            cache_used + estimated_output <= CACHE_LIMIT_BYTES
            and free - estimated_output >= MIN_FREE_BYTES
        ):
            return
        if not _evict_one(conn, entry_id):
            raise RuntimeError(
                "Недостаточно места для совместимой копии видео "
                f"(cache={cache_used}, free={free}, need={estimated_output})"
            )


def _mark(conn, entry_id, status, **fields):
    fields = dict(fields)
    fields["status"] = status
    fields["updated_at"] = utc_now()
    assignments = ",".join(f"{key}=?" for key in fields)
    values = list(fields.values()) + [entry_id]
    conn.execute(
        f"UPDATE knowledge_video_compat SET {assignments} WHERE entry_id=?",
        values,
    )
    conn.commit()


def _pick_pending(conn):
    init_schema(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            """
            SELECT *
            FROM knowledge_video_compat
            WHERE status='pending'
            ORDER BY requested_at ASC
            LIMIT 1
            """
        ).fetchone()
        if row:
            conn.execute(
                """
                UPDATE knowledge_video_compat
                SET status='probing',updated_at=?
                WHERE entry_id=? AND status='pending'
                """,
                (utc_now(), row["entry_id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return row


def reset_interrupted(conn):
    init_schema(conn)
    conn.execute(
        """
        UPDATE knowledge_video_compat
        SET status='pending',updated_at=?,error=NULL
        WHERE status IN ('probing','converting')
        """,
        (utc_now(),),
    )
    conn.commit()


def process_one(conn):
    row = _pick_pending(conn)
    if not row:
        return False

    entry_id = row["entry_id"]
    source = (KNOWLEDGE_ROOT / row["relative_path"]).resolve()
    root = KNOWLEDGE_ROOT.resolve()
    if source != root and root not in source.parents:
        _mark(conn, entry_id, "error", error="Некорректный путь источника")
        return True
    if not source.is_file():
        _mark(conn, entry_id, "error", error="Исходный файл недоступен")
        return True

    try:
        meta = probe(source)
        video = meta.get("video") or {}
        codec = video.get("codec_name")
        pix_fmt = video.get("pix_fmt")
        if source_is_browser_compatible(source, meta):
            _mark(
                conn, entry_id, "source_ok",
                codec_name=codec, pix_fmt=pix_fmt, error=None,
            )
            return True

        duration = max(float(meta.get("duration") or 0), 60.0)
        # 2.0 Mbit/s + 25% headroom is conservative for the 720p ultrafast cache.
        estimated_output = int(duration * 2_000_000 / 8 * 1.25)
        ensure_capacity(conn, entry_id, estimated_output)
        _mark(
            conn, entry_id, "converting",
            codec_name=codec, pix_fmt=pix_fmt, error=None,
        )

        COMPAT_ROOT.mkdir(parents=True, exist_ok=True)
        final_path = cache_path(entry_id)
        temp_path = COMPAT_ROOT / f".{entry_id}.tmp.mp4"
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass

        cmd = [
            FFMPEG, "-y", "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-i", str(source),
            "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn",
            "-vf", "scale='min(1280,iw)':-2",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-threads", "2",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            str(temp_path),
        ]
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=8 * 60 * 60,
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "ffmpeg error")[-3000:])
        if not temp_path.is_file() or temp_path.stat().st_size <= 0:
            raise RuntimeError("ffmpeg не создал совместимый файл")

        verify = probe(temp_path)
        if not source_is_browser_compatible(temp_path, verify):
            raise RuntimeError("Проверка H.264-копии не пройдена")

        os.replace(temp_path, final_path)
        size = final_path.stat().st_size
        _mark(
            conn, entry_id, "ready",
            codec_name=codec, pix_fmt=pix_fmt,
            output_size=size, error=None,
            last_accessed_at=utc_now(),
        )
    except Exception as exc:
        try:
            temp = COMPAT_ROOT / f".{entry_id}.tmp.mp4"
            temp.unlink()
        except (FileNotFoundError, UnboundLocalError):
            pass
        _mark(conn, entry_id, "error", error=str(exc)[:3000])
    return True


def worker_loop():
    COMPAT_ROOT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        reset_interrupted(conn)
        while True:
            worked = process_one(conn)
            if not worked:
                time.sleep(5)
    finally:
        conn.close()


def print_status():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        rows = conn.execute(
            """
            SELECT status,COUNT(*) AS n
            FROM knowledge_video_compat
            GROUP BY status
            ORDER BY status
            """
        ).fetchall()
        for row in rows:
            print(f"{row['status']}={row['n']}")
        print(f"cache_bytes={_cache_bytes()}")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    if args.worker:
        worker_loop()
        return
    if args.status:
        print_status()
        return

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        init_schema(conn)
        reset_interrupted(conn)
        if args.once:
            process_one(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
