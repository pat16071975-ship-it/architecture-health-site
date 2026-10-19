"""Permanent, prebuilt AZ-BAZE video library (no request-time conversion).

Preparation is an explicit offline operation. Publication is an all-entry gate.
This module never deletes source files, archives or published media, and never
uses the legacy LRU cache. The catalog and financial database are read-only.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import struct
import subprocess
import tempfile
import time
import uuid
import zipfile
from functools import wraps
from typing import Any, Iterable

SCHEMA_VERSION = 1
PROFILE = "h264-high-crf18-medium-source-resolution-v1"
CHUNK = 1024 * 1024
GIB = 1024 ** 3


class NotReady(RuntimeError):
    """A failed gate; never fall back to an on-demand conversion."""


def relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise NotReady("Invalid relative path")
    p = PurePosixPath(value)
    if p.is_absolute() or ".." in p.parts or p.as_posix() != value or value == ".":
        raise NotReady("Unsafe or noncanonical path")
    return p


def safe_path(root: Path, value: str) -> Path:
    p = relative(value)
    if root.is_symlink() or not root.is_dir():
        raise NotReady("Storage root missing or symlinked")
    root = root.resolve(strict=True)
    out = root.joinpath(*p.parts)
    cur = root
    for part in p.parts:
        cur = cur / part
        if cur.is_symlink():
            raise NotReady("Symlink in material path")
    if root not in out.resolve(strict=False).parents:
        raise NotReady("Path outside storage root")
    return out


def snapshot(path: Path) -> dict:
    s = path.stat()
    if not stat.S_ISREG(s.st_mode) or s.st_size <= 0:
        raise NotReady("Missing, empty or nonregular material")
    return {"size": s.st_size, "mtime_ns": s.st_mtime_ns}


def digest_stream(stream) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(CHUNK), b""):
        h.update(block)
    return h.hexdigest()


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return digest_stream(stream)


def json_read(path: Path) -> dict:
    if path.is_symlink():
        raise NotReady("Symlinked manifest")
    with path.open("r", encoding="utf-8") as f:
        result = json.load(f)
    if not isinstance(result, dict):
        raise NotReady("Invalid manifest")
    return result


def json_atomic(path: Path, value: dict) -> None:
    """Atomic manifest update; never exposes a partial JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def catalog_rows(db_path: Path) -> list[dict]:
    """No schema-init, no import of the application's bootstrap, no live writes."""
    with contextlib.closing(sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)) as c:
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA query_only=ON")
        return [dict(r) for r in c.execute(
            "SELECT id,relative_path,name,size_bytes,modified_at "
            "FROM knowledge_entries WHERE hidden=0 AND entry_type='file' "
            "AND media_kind='video' ORDER BY relative_path"
        )]


def validate_rows(rows: list[dict]) -> None:
    if not rows:
        raise NotReady("Empty catalog is not a completed library")
    ids, paths, outputs = set(), set(), {}
    for row in rows:
        p = relative(row["relative_path"])
        expected = hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:32]
        if row["id"] != expected or expected in ids or str(p) in paths:
            raise NotReady("Catalog identity mismatch or duplicate")
        ids.add(expected)
        paths.add(str(p))
        # Case-insensitive collision guard also protects Windows/QNAP clients.
        output = str(p.with_suffix(".mp4")).casefold()
        if output in outputs:
            raise NotReady("Conflicting MP4 names: " + outputs[output] + " / " + str(p))
        outputs[output] = str(p)


def probe(path: Path) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=180, check=False,
    )
    if proc.returncode:
        raise NotReady("ffprobe failed: " + proc.stderr[-800:])
    data = json.loads(proc.stdout)
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"
              and not (s.get("disposition") or {}).get("attached_pic")]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if len(videos) != 1:
        raise NotReady("REVIEW: expected exactly one real video stream")
    if len(audios) > 1 or (audios and int(audios[0].get("channels", 0)) > 2):
        raise NotReady("REVIEW: multiple audio tracks or multichannel audio")
    # Do not silently discard potentially meaningful subtitles.
    if any(s.get("codec_type") == "subtitle" for s in streams):
        raise NotReady("REVIEW: subtitle track requires a preservation decision")
    v = videos[0]
    rotation = float((v.get("tags") or {}).get("rotate", 0) or 0)
    for side in v.get("side_data_list", []):
        rotation = float(side.get("rotation", rotation) or 0)
    if rotation % 360:
        raise NotReady("REVIEW: rotation must be verified before copying")
    if v.get("color_transfer") in {"smpte2084", "arib-std-b67"}:
        raise NotReady("REVIEW: HDR is not the tested BT.709 SDR profile")
    duration = float((data.get("format") or {}).get("duration", 0) or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise NotReady("Unknown duration")
    return {"video": v, "audio": audios[0] if audios else None,
            "duration": duration, "format": data.get("format", {})}


def classify(path: Path, meta: dict) -> str:
    v, a = meta["video"], meta["audio"]
    copy_video = v.get("codec_name") == "h264" and v.get("pix_fmt") in {"yuv420p", "yuvj420p"}
    copy_audio = a is None or a.get("codec_name") in {"aac", "mp3"}
    if copy_video and copy_audio and path.suffix.lower() in {".mp4", ".m4v"}:
        return "direct"
    if copy_video:
        return "remux" if copy_audio else "audio_fix"
    if v.get("codec_name") not in {"hevc", "mpeg4", "h264"}:
        raise NotReady("REVIEW: untested video codec")
    # This candidate does not change resolution, cadence, aspect or HDR rendering.
    if v.get("field_order") not in {None, "unknown", "progressive"}:
        raise NotReady("REVIEW: interlaced transcode requires a separate profile")
    return "video_transcode"


def conversion_command(src: Path, dst: Path, meta: dict, mode: str) -> list[str]:
    v, a = meta["video"], meta["audio"]
    cmd = ["ffmpeg", "-n", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
           "-threads", "2", "-noautorotate", "-i", str(src), "-map", "0:" + str(v["index"])]
    if a:
        cmd += ["-map", "0:" + str(a["index"])]
    cmd += ["-sn", "-dn", "-map_metadata", "-1", "-map_chapters", "-1"]
    if mode in {"remux", "audio_fix"}:
        cmd += ["-c:v", "copy"]
    else:
        cmd += ["-c:v", "libx264", "-profile:v", "high", "-preset", "medium", "-crf", "18",
                "-pix_fmt", "yuv420p", "-threads:v", "2", "-filter_threads", "1",
                "-fps_mode", "passthrough"]
        for key, flag in [("color_range", "-color_range"), ("color_space", "-colorspace"),
                          ("color_transfer", "-color_trc"), ("color_primaries", "-color_primaries")]:
            value = v.get(key)
            if value and value not in {"unknown", "unspecified", "reserved"}:
                cmd += [flag, str(value)]
    if a:
        cmd += ["-c:a", "copy"] if a.get("codec_name") in {"aac", "mp3"} else [
            "-c:a", "aac", "-b:a", "192k", "-threads:a", "1"]
    return cmd + ["-movflags", "+faststart", "-f", "mp4", str(dst)]


def run_media(cmd: list[str], guard=None, timeout: float = 24 * 3600) -> str:
    """Bound resource use and stop on disk/mount failure, including during ffmpeg."""
    with tempfile.TemporaryFile() as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        start = time.monotonic()
        try:
            while proc.poll() is None:
                if time.monotonic() - start > timeout:
                    raise NotReady("Media operation timed out")
                if guard:
                    guard()
                time.sleep(0.1)
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 12000))
            output = log.read().decode("utf-8", "replace")
            if proc.returncode:
                raise NotReady("Media operation failed: " + output[-1500:])
            return output
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()


def frame_evidence(path: Path, guard=None) -> dict:
    cmd = ["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-threads", "2", "-noautorotate",
           "-i", str(path), "-map", "0:V:0", "-an", "-sn", "-dn", "-fps_mode", "passthrough",
           "-threads:v", "1", "-filter_threads", "1", "-pix_fmt", "yuv420p",
           "-c:v", "rawvideo", "-f", "hash", "-hash", "sha256", "-"]
    output = run_media(cmd, guard)
    hashes = [s.split("=", 1)[1].strip() for s in output.splitlines() if s.startswith("SHA256=")]
    if len(hashes) != 1 or len(hashes[0]) != 64:
        raise NotReady("Missing decoded-frame digest")
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-threads", "2", "-select_streams", "V:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "json", str(path)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=24 * 3600,
    )
    if proc.returncode:
        raise NotReady("Frame count failed")
    streams = json.loads(proc.stdout).get("streams", [])
    count = int(streams[0].get("nb_read_frames", 0)) if streams else 0
    if count <= 0:
        raise NotReady("Empty decoded video")
    return {"decoded_sha256": hashes[0], "frames": count}


def audio_hash(path: Path, guard=None) -> str:
    output = run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                        "-map", "0:a:0", "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-"], guard)
    vals = [s.split("=", 1)[1].strip() for s in output.splitlines() if s.startswith("SHA256=")]
    if len(vals) != 1:
        raise NotReady("Missing audio digest")
    return vals[0]


def faststart(path: Path) -> bool:
    """Inspect top-level ISO BMFF boxes without reading the whole mdat payload."""
    size, position, moov = path.stat().st_size, 0, None
    with path.open("rb") as f:
        while position + 8 <= size:
            f.seek(position)
            length, kind = struct.unpack(">I4s", f.read(8))
            header = 8
            if length == 1:
                if position + 16 > size:
                    return False
                length = struct.unpack(">Q", f.read(8))[0]
                header = 16
            elif length == 0:
                length = size - position
            if length < header or position + length > size:
                return False
            if kind == b"moov":
                moov = position
            if kind == b"mdat":
                return moov is not None and moov < position
            position += length
    return False


class Store:
    def __init__(self, source: Path, root: Path, *, reserve: int = 20 * GIB,
                 enforce_external: bool = True):
        self.source = source.resolve(strict=True)
        self.root = root.resolve(strict=True)
        self.reserve = reserve
        self.enforce_external = enforce_external
        if (self.root == self.source or self.source in self.root.parents or self.root in self.source.parents):
            raise NotReady("Source and destination must not overlap")
        if root.is_symlink() or source.is_symlink() or reserve < 0:
            raise NotReady("Invalid storage configuration")
        self.device = self.root.stat().st_dev
        self.marker = self.root / ".az-knowledge-store.json"
        self.check_volume()

    def check_volume(self) -> None:
        if not self.root.is_dir() or self.root.stat().st_dev != self.device:
            raise NotReady("Storage mount changed or unavailable")
        if self.enforce_external and self.device == Path("/").stat().st_dev:
            raise NotReady("Permanent media must not use the VPS system volume")
        if self.root.is_symlink():
            raise NotReady("Storage symlink rejected")

    def initialize(self) -> None:
        self.check_volume()
        if self.marker.exists():
            self.marker_data()
            return
        if any(self.root.iterdir()):
            raise NotReady("New store directory must be empty")
        json_atomic(self.marker, {"schema": SCHEMA_VERSION, "uuid": uuid.uuid4().hex,
                                 "source": str(self.source), "profile": PROFILE})

    def marker_data(self) -> dict:
        self.check_volume()
        marker = json_read(self.marker)
        if marker.get("schema") != SCHEMA_VERSION or marker.get("source") != str(self.source):
            raise NotReady("Incorrect storage identity")
        if marker.get("profile") != PROFILE:
            raise NotReady("Profile changed; use a new generation, do not overwrite")
        return marker

    def space(self, additional: int = 0) -> None:
        self.marker_data()
        if shutil.disk_usage(self.root).free < self.reserve + additional:
            raise NotReady("Insufficient space; existing originals and outputs are retained")

    @contextlib.contextmanager
    def exclusive(self):
        import fcntl
        self.marker_data()
        lock = safe_path(self.root, ".prepare.lock")
        with lock.open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise NotReady("Another preparation process holds the lock") from e
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def receipt_path(self, row: dict) -> Path:
        return safe_path(self.root, "state/items/" + row["id"] + ".json")

    def valid_receipt(self, row: dict, *, full_hash: bool = True) -> dict | None:
        path = self.receipt_path(row)
        if not path.exists():
            return None
        r = json_read(path)
        src = safe_path(self.source, row["relative_path"])
        if (r.get("id") != row["id"] or r.get("relative_path") != row["relative_path"]
                or r.get("source_snapshot") != snapshot(src)):
            raise NotReady("Source changed after preparation")
        out = src if r.get("mode") == "direct" else safe_path(self.root, r["output_path"])
        if r.get("output_snapshot") != snapshot(out):
            raise NotReady("Prepared media changed or missing")
        if full_hash and (digest(src) != r["source_sha256"] or digest(out) != r["output_sha256"]):
            raise NotReady("Prepared media checksum mismatch")
        if r.get("schema") != SCHEMA_VERSION or r.get("profile") != PROFILE or not r.get("verified"):
            raise NotReady("Receipt not verified for the configured profile")
        return r

    def archive(self, rows: list[dict], *, target_bytes: int = 20 * GIB) -> list[dict]:
        """One original folder per group. Independent ZIP64 parts, not split ZIPs."""
        if not rows:
            return []
        parents = {str(relative(r["relative_path"]).parent) for r in rows}
        if len(parents) != 1:
            raise NotReady("Archive cannot mix source folders")
        parent = parents.pop()
        folder = "root" if parent == "." else relative(parent).name
        prefix = "archives" + ("/" + parent if parent != "." else "")
        parts, batch, nbytes = [], [], 0
        for row in sorted(rows, key=lambda r: r["relative_path"]):
            s = snapshot(safe_path(self.source, row["relative_path"]))
            row = {**row, "archive_source_snapshot": s}
            if batch and nbytes + s["size"] > target_bytes:
                parts.append(batch)
                batch, nbytes = [], 0
            batch.append(row)
            nbytes += s["size"]
        if batch:
            parts.append(batch)
        result = []
        for number, part in enumerate(parts, 1):
            membership = hashlib.sha256(json.dumps(
                [(r["relative_path"], r["archive_source_snapshot"]) for r in part],
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            relzip = f"{prefix}/{folder}.originals.part{number:03d}-{membership}.zip"
            dest = safe_path(self.root, relzip)
            receipt = safe_path(self.root, relzip + ".json")
            if dest.exists() or receipt.exists():
                if not (dest.exists() and receipt.exists()):
                    raise NotReady("Unverified archive exists; do not overwrite")
                rec = json_read(receipt)
                self.verify_archive(rec)
                if [x["relative_path"] for x in rec["members"]] != [r["relative_path"] for r in part]:
                    raise NotReady("Archive membership mismatch")
                for m in rec["members"]:
                    if snapshot(safe_path(self.source, m["relative_path"])) != m["snapshot"]:
                        raise NotReady("Archived source changed")
                result.append(rec)
                continue
            self.space(sum(r["archive_source_snapshot"]["size"] for r in part) + 16 * CHUNK)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.parent / ("." + dest.name + "." + uuid.uuid4().hex + ".partial")
            members = []
            try:
                with zipfile.ZipFile(tmp, "x", compression=zipfile.ZIP_STORED, allowZip64=True) as z:
                    for row in part:
                        src = safe_path(self.source, row["relative_path"])
                        before = snapshot(src)
                        if before != row["archive_source_snapshot"]:
                            raise NotReady("Source changed before archiving")
                        info = zipfile.ZipInfo.from_file(src, arcname=row["relative_path"], strict_timestamps=False)
                        h = hashlib.sha256()
                        with src.open("rb") as inp, z.open(info, "w", force_zip64=True) as out:
                            for chunk in iter(lambda: inp.read(CHUNK), b""):
                                self.space()
                                out.write(chunk)
                                h.update(chunk)
                        if snapshot(src) != before:
                            raise NotReady("Source changed during archiving")
                        members.append({"relative_path": row["relative_path"], "snapshot": before,
                                        "sha256": h.hexdigest()})
                rec = {"path": relzip, "members": members, "sha256": digest(tmp)}
                self.verify_archive(rec, override=tmp)
                self.space()
                if dest.exists():
                    raise NotReady("Archive appeared concurrently")
                with tmp.open("rb") as durable:
                    os.fsync(durable.fileno())
                os.rename(tmp, dest)
                json_atomic(receipt, rec)
                result.append(rec)
            finally:
                tmp.unlink(missing_ok=True)
        return result

    def verify_archive(self, rec: dict, *, override: Path | None = None) -> None:
        self.marker_data()
        path = override or safe_path(self.root, rec["path"])
        if digest(path) != rec["sha256"]:
            raise NotReady("Archive checksum mismatch")
        with zipfile.ZipFile(path, "r") as z:
            names = z.namelist()
            expected = [m["relative_path"] for m in rec["members"]]
            if names != expected or len(names) != len(set(names)):
                raise NotReady("Archive contents mismatch")
            for m in rec["members"]:
                relative(m["relative_path"])
                info = z.getinfo(m["relative_path"])
                if info.file_size != m["snapshot"]["size"]:
                    raise NotReady("Archive member size mismatch")
                with z.open(info) as f:
                    if digest_stream(f) != m["sha256"]:
                        raise NotReady("Archive member SHA256 mismatch")

    def prepare_one(self, row: dict, meta: dict, mode: str, archives: list[dict]) -> dict:
        old = self.valid_receipt(row)
        if old:
            return old
        src = safe_path(self.source, row["relative_path"])
        before = snapshot(src)
        if (before["size"] != int(row["size_bytes"])
                or before["mtime_ns"] // 1_000_000_000 != int(row["modified_at"])):
            raise NotReady("Catalog is stale; rescan before preparation")
        source_hash = digest(src)
        output_rel = "media/" + str(relative(row["relative_path"]).with_suffix(".mp4"))
        dst = safe_path(self.root, output_rel)
        source_frames = frame_evidence(src, self.space)
        if mode == "direct":
            output_frames, output_meta, output_hash, out_snapshot = source_frames, meta, source_hash, before
        else:
            members = [m for a in archives for m in a["members"] if m["relative_path"] == row["relative_path"]]
            if len(members) != 1 or members[0]["sha256"] != source_hash or members[0]["snapshot"] != before:
                raise NotReady("Verified archive of this exact source is required")
            if dst.exists():
                raise NotReady("Unverified output exists; never overwrite")
            # Stream-copy size is nearly the source size. For encoding this is an
            # admission reserve, not a promise of output size; space is watched.
            self.space(before["size"] + 16 * CHUNK)
            dst.parent.mkdir(parents=True, exist_ok=True)
            tmp = dst.parent / ("." + dst.stem + "." + uuid.uuid4().hex + ".partial.mp4")
            try:
                run_media(conversion_command(src, tmp, meta, mode), self.space)
                output_meta = probe(tmp)
                if output_meta["video"].get("codec_name") != "h264":
                    raise NotReady("Output not H.264")
                if output_meta["video"].get("pix_fmt") not in {"yuv420p", "yuvj420p"}:
                    raise NotReady("Output not compatible 8-bit 4:2:0")
                if not faststart(tmp):
                    raise NotReady("MP4 is not faststart")
                for key in ("width", "height", "sample_aspect_ratio"):
                    if meta["video"].get(key) != output_meta["video"].get(key):
                        raise NotReady("Geometry changed: " + key)
                if abs(meta["duration"] - output_meta["duration"]) > 0.15:
                    raise NotReady("Duration changed; timing review required")
                if bool(meta["audio"]) != bool(output_meta["audio"]):
                    raise NotReady("Audio track missing or unexpectedly added")
                if output_meta["audio"]:
                    if output_meta["audio"].get("codec_name") not in {"aac", "mp3"}:
                        raise NotReady("Output audio is not compatible")
                    if output_meta["audio"].get("channels") != meta["audio"].get("channels"):
                        raise NotReady("Audio channel count changed")
                    if meta["audio"].get("codec_name") in {"aac", "mp3"}:
                        if audio_hash(src, self.space) != audio_hash(tmp, self.space):
                            raise NotReady("Copied audio payload changed")
                    # Decode all output audio, including a newly encoded AAC track.
                    run_media(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-threads", "2",
                               "-i", str(tmp), "-map", "0:a:0", "-vn", "-sn", "-dn", "-f", "null", "-"], self.space)
                output_frames = frame_evidence(tmp, self.space)
                if source_frames["frames"] != output_frames["frames"]:
                    raise NotReady("Frame count changed")
                if mode != "video_transcode" and source_frames["decoded_sha256"] != output_frames["decoded_sha256"]:
                    raise NotReady("Stream-copy decoded frames changed")
                if snapshot(src) != before or digest(src) != source_hash:
                    raise NotReady("Source changed during preparation")
                output_hash = digest(tmp)
                self.space()
                with tmp.open("rb") as durable:
                    os.fsync(durable.fileno())
                os.rename(tmp, dst)
                out_snapshot = snapshot(dst)
            finally:
                tmp.unlink(missing_ok=True)
        if snapshot(src) != before:
            raise NotReady("Source changed during verification")
        receipt = {"schema": SCHEMA_VERSION, "profile": PROFILE, "verified": True,
                   "id": row["id"], "relative_path": row["relative_path"], "mode": mode,
                   "source_snapshot": before, "source_sha256": source_hash,
                   "output_path": output_rel if mode != "direct" else None,
                   "output_snapshot": out_snapshot, "output_sha256": output_hash,
                   "source_frames": source_frames, "output_frames": output_frames,
                   "output_video": output_meta["video"], "output_audio": output_meta["audio"],
                   "visual_review_required": mode == "video_transcode",
                   "archives": [a["path"] for a in archives if any(
                       m["relative_path"] == row["relative_path"] for m in a["members"])]}
        json_atomic(self.receipt_path(row), receipt)
        return receipt

    def prepare(self, rows: list[dict]) -> dict:
        """Resumable complete-folder jobs; any failed item stays a blocker."""
        validate_rows(rows)
        result = {"prepared": [], "errors": []}
        groups = {}
        for row in rows:
            groups.setdefault(str(relative(row["relative_path"]).parent), []).append(row)
        with self.exclusive():
            for folder, entries in groups.items():
                try:
                    self.space()
                    info = []
                    for row in entries:
                        src = safe_path(self.source, row["relative_path"])
                        meta = probe(src)
                        info.append((row, meta, classify(src, meta)))
                    archives = self.archive([r for r, m, k in info if k != "direct"])
                    for row, meta, mode in info:
                        self.prepare_one(row, meta, mode, archives)
                        result["prepared"].append(row["id"])
                        print("PREPARED", row["id"], mode, flush=True)
                except (NotReady, OSError, ValueError, subprocess.SubprocessError, zipfile.BadZipFile) as e:
                    result["errors"].append({"folder": folder, "reason": str(e)})
                    print("BLOCKED", folder, str(e), flush=True)
        return result

    def publish(self, rows: list[dict], *, visual_approved: bool = False) -> dict:
        validate_rows(rows)
        with self.exclusive():
            entries, archives = {}, set()
            for row in rows:
                r = self.valid_receipt(row)
                if not r:
                    raise NotReady("Not every catalog video has a verified permanent source")
                if r["visual_review_required"] and not visual_approved:
                    raise NotReady("Exact permanent transcode profile still needs visual approval")
                entries[row["id"]] = r
                archives.update(r["archives"])
            for archive in sorted(archives):
                self.verify_archive(json_read(safe_path(self.root, archive + ".json")))
            active = {"schema": SCHEMA_VERSION, "store_uuid": self.marker_data()["uuid"],
                      "profile": PROFILE, "source": str(self.source), "count": len(entries),
                      "visual_approved": visual_approved, "entries": entries}
            # Only this command publishes. A successful partial prepare does not.
            json_atomic(safe_path(self.root, "active.json"), active)
            return {"published": len(entries), "archives": len(archives)}


class Reader:
    """Read-only request-side lookup. No subprocess, queue, LRU or source writes."""
    def __init__(self, root: Path):
        self.root = root.resolve(strict=True)
        if root.is_symlink():
            raise NotReady("Invalid permanent root")

    def resolve(self, row: dict) -> tuple[Path, dict]:
        marker = json_read(safe_path(self.root, ".az-knowledge-store.json"))
        manifest = json_read(safe_path(self.root, "active.json"))
        if (manifest.get("schema") != SCHEMA_VERSION or marker.get("uuid") != manifest.get("store_uuid")
                or marker.get("source") != manifest.get("source")
                or manifest.get("profile") != PROFILE or marker.get("profile") != PROFILE):
            raise NotReady("Permanent manifest is missing or mismatched")
        rows = manifest.get("entries", {})
        if manifest.get("count") != len(rows):
            raise NotReady("Incomplete publication manifest")
        r = rows.get(row["id"])
        if not r or r.get("relative_path") != row["relative_path"] or not r.get("verified"):
            raise NotReady("Material has no verified prepared version")
        if r.get("visual_review_required") and not manifest.get("visual_approved"):
            raise NotReady("Unapproved video profile")
        source = safe_path(Path(manifest["source"]), row["relative_path"])
        if snapshot(source) != r["source_snapshot"]:
            raise NotReady("Source changed; prepare a new version offline")
        output = source if r["mode"] == "direct" else safe_path(self.root, r["output_path"])
        if snapshot(output) != r["output_snapshot"]:
            raise NotReady("Permanent media missing or changed")
        return output, r


def install(app) -> None:
    """Wrap existing knowledge endpoints, preserving their auth and templates.

    Set AZBAZE_KNOWLEDGE_PERMANENT_ROOT only after publish. Unset means exact
    legacy behavior; configured-but-broken means 503, never silently reconvert.
    """
    root_text = os.environ.get("AZBAZE_KNOWLEDGE_PERMANENT_ROOT", "").strip()
    if not root_text:
        return
    if not Path(root_text).is_absolute():
        raise NotReady("Permanent root must be absolute")
    from flask import abort, jsonify, render_template, send_file
    from app import db, permission_required

    names = {"knowledge_view", "knowledge_media", "knowledge_compatible_media",
             "knowledge_video_status", "knowledge_video_retry"}
    if not names.issubset(app.view_functions):
        raise NotReady("Knowledge endpoint contract changed; refuse installation")

    def make(endpoint, legacy):
        @wraps(legacy)
        @permission_required("knowledge")
        def prepared(entry_id):
            conn = db()
            # Deliberately no init_schema or legacy request_video invocation.
            row = conn.execute("SELECT * FROM knowledge_entries WHERE id=? AND hidden=0", (entry_id,)).fetchone()
            if row is None or row["entry_type"] != "file":
                abort(404)
            if row["media_kind"] != "video":
                return legacy(entry_id)
            if endpoint == "knowledge_video_retry":
                return jsonify(status="error", message="Видео готовятся централизованно; повторная конвертация при просмотре отключена."), 409
            try:
                path, receipt = Reader(Path(root_text)).resolve(dict(row))
            except (OSError, ValueError, KeyError, NotReady):
                # Fail closed. Do not leak QNAP paths or internal manifest data.
                abort(503, description="Подготовленный материал временно недоступен. Сообщите администратору.")
            media_url = "/knowledge-compatible-media/" + entry_id
            if endpoint == "knowledge_video_status":
                return jsonify(status="ready", ready=True, error=None, media_url=media_url)
            if endpoint == "knowledge_view":
                pol = conn.execute("SELECT allow_download FROM knowledge_policies WHERE relative_path=?",
                                   (row["relative_path"],)).fetchone()
                return render_template("knowledge_view.html", entry=row, allow_download=bool(pol and pol[0]),
                                       media_mimetype="video/mp4", media_url=media_url, test_mode=False,
                                       video_status="ready", video_status_url="/api/knowledge-video-status/" + entry_id)
            response = send_file(path, mimetype="video/mp4", as_attachment=False,
                                 download_name=Path(row["name"]).with_suffix(".mp4").name,
                                 conditional=True, etag=receipt["output_sha256"], max_age=0)
            response.headers["Cache-Control"] = "private, no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Accept-Ranges"] = "bytes"
            response.headers["X-Accel-Buffering"] = "no"
            return response
        return prepared
    for name in names:
        app.view_functions[name] = make(name, app.view_functions[name])


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["init", "prepare", "publish"])
    p.add_argument("--source", required=True, type=Path)
    p.add_argument("--store", required=True, type=Path)
    p.add_argument("--db", type=Path)
    p.add_argument("--approve-visual-h264-crf18", action="store_true")
    args = p.parse_args()
    try:
        store = Store(args.source, args.store)
        if args.command == "init":
            store.initialize()
            print("STORE_INITIALIZED=PASS")
        else:
            if args.db is None:
                raise NotReady("--db required")
            rows = catalog_rows(args.db)
            if args.command == "prepare":
                result = store.prepare(rows)
                print(json.dumps(result, ensure_ascii=False))
                return 1 if result["errors"] else 0
            print(json.dumps(store.publish(rows, visual_approved=args.approve_visual_h264_crf18)))
        return 0
    except (NotReady, OSError, ValueError, sqlite3.Error) as e:
        print("STOP:", e)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
