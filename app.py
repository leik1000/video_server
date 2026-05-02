import json
import os
import re
import secrets
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse


BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"

DEFAULT_CONFIG: dict[str, Any] = {
    "api_token": "change-this-upload-token",
    "public_base_url": "https://video.example.com/videos",
    "storage_dir": str(BASE_DIR / "data" / "videos"),
    "metadata_file": str(BASE_DIR / "data" / "video_index.json"),
    "max_upload_mb": 1024,
    "max_storage_gb": 100,
    "prune_storage_gb": 1,
    "cleanup_interval_seconds": 3600,
    "allowed_extensions": ["mp4", "webm", "mov"],
    "allowed_content_types": [
        "video/mp4",
        "video/webm",
        "video/quicktime",
        "application/octet-stream",
    ],
}


def _load_config() -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                config.update(data)
        except Exception:
            pass

    env_map = {
        "VIDEO_SERVER_API_TOKEN": "api_token",
        "VIDEO_SERVER_PUBLIC_BASE_URL": "public_base_url",
        "VIDEO_SERVER_STORAGE_DIR": "storage_dir",
        "VIDEO_SERVER_METADATA_FILE": "metadata_file",
        "VIDEO_SERVER_MAX_UPLOAD_MB": "max_upload_mb",
        "VIDEO_SERVER_MAX_STORAGE_GB": "max_storage_gb",
        "VIDEO_SERVER_PRUNE_STORAGE_GB": "prune_storage_gb",
        "VIDEO_SERVER_CLEANUP_INTERVAL_SECONDS": "cleanup_interval_seconds",
    }
    for env_key, config_key in env_map.items():
        value = os.getenv(env_key)
        if value is not None:
            config[config_key] = value

    for int_key in [
        "max_upload_mb",
        "max_storage_gb",
        "prune_storage_gb",
        "cleanup_interval_seconds",
    ]:
        try:
            config[int_key] = int(config.get(int_key) or DEFAULT_CONFIG[int_key])
        except Exception:
            config[int_key] = DEFAULT_CONFIG[int_key]

    config["allowed_extensions"] = [
        str(item or "").strip().lower().lstrip(".")
        for item in config.get("allowed_extensions", [])
        if str(item or "").strip()
    ] or list(DEFAULT_CONFIG["allowed_extensions"])
    config["allowed_content_types"] = [
        str(item or "").strip().lower()
        for item in config.get("allowed_content_types", [])
        if str(item or "").strip()
    ] or list(DEFAULT_CONFIG["allowed_content_types"])
    return config


def _resolve_path(value: str) -> Path:
    path = Path(str(value or "").strip())
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


CONFIG = _load_config()
STORAGE_DIR = _resolve_path(str(CONFIG["storage_dir"]))
METADATA_FILE = _resolve_path(str(CONFIG["metadata_file"]))
MAX_UPLOAD_BYTES = max(1, int(CONFIG["max_upload_mb"])) * 1024 * 1024
MAX_STORAGE_BYTES = max(1, int(CONFIG["max_storage_gb"])) * 1024 * 1024 * 1024
PRUNE_STORAGE_BYTES = max(1, int(CONFIG["prune_storage_gb"])) * 1024 * 1024 * 1024

STORAGE_DIR.mkdir(parents=True, exist_ok=True)
METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)

metadata_lock = threading.Lock()
cleanup_stop = threading.Event()

app = FastAPI(title="Video Storage Server", version="1.0.0")


def _now() -> int:
    return int(time.time())


def _public_url(filename: str) -> str:
    return f"{str(CONFIG['public_base_url']).rstrip('/')}/{filename}"


def _safe_id(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^a-zA-Z0-9_.-]+", "_", text)
    text = text.strip("._-")
    return text[:120]


def _safe_extension(filename: str, content_type: str | None) -> str:
    ext = Path(str(filename or "")).suffix.lower().lstrip(".")
    if not ext and str(content_type or "").lower() == "video/webm":
        ext = "webm"
    if not ext and str(content_type or "").lower() == "video/quicktime":
        ext = "mov"
    if not ext:
        ext = "mp4"
    if ext not in set(CONFIG["allowed_extensions"]):
        raise HTTPException(status_code=400, detail=f"unsupported video extension: {ext}")
    return ext


def _read_index_locked() -> dict[str, Any]:
    if not METADATA_FILE.exists():
        return {}
    try:
        data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_index_locked(index: dict[str, Any]) -> None:
    tmp_path = METADATA_FILE.with_suffix(f"{METADATA_FILE.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(tmp_path, METADATA_FILE)


def _save_record(record: dict[str, Any]) -> None:
    with metadata_lock:
        index = _read_index_locked()
        index[str(record["id"])] = record
        _write_index_locked(index)


def _get_record(video_id: str) -> dict[str, Any] | None:
    with metadata_lock:
        return _read_index_locked().get(video_id)


def _delete_record(video_id: str) -> dict[str, Any] | None:
    with metadata_lock:
        index = _read_index_locked()
        record = index.pop(video_id, None)
        if record is not None:
            _write_index_locked(index)
        return record


def require_upload_auth(authorization: str | None = Header(default=None)) -> None:
    expected = str(CONFIG.get("api_token") or "").strip()
    if not expected or expected == DEFAULT_CONFIG["api_token"]:
        raise HTTPException(status_code=500, detail="api_token is not configured")

    prefix = "Bearer "
    received = str(authorization or "")
    if not received.startswith(prefix):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = received[len(prefix) :].strip()
    if not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=403, detail="invalid bearer token")


async def _write_upload(file: UploadFile, target: Path) -> int:
    tmp_path = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    size = 0
    try:
        with tmp_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"video is larger than {int(CONFIG['max_upload_mb'])} MB",
                    )
                out.write(chunk)
        if size <= 0:
            raise HTTPException(status_code=400, detail="empty video file")
        os.replace(tmp_path, target)
        return size
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _cleanup_storage_once() -> dict[str, int]:
    removed = 0
    removed_bytes = 0
    with metadata_lock:
        index = _read_index_locked()
        filename_to_id = {
            Path(str(record.get("filename") or "")).name: str(video_id)
            for video_id, record in index.items()
            if str(record.get("filename") or "").strip()
        }
        files: list[dict[str, Any]] = []
        total_bytes = 0
        for path in STORAGE_DIR.iterdir():
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except Exception:
                continue
            filename = path.name
            video_id = filename_to_id.get(filename)
            record = index.get(video_id, {}) if video_id else {}
            size = max(0, int(stat.st_size))
            total_bytes += size
            try:
                sort_ts = int(record.get("created_at") or 0) if record else 0
            except Exception:
                sort_ts = 0
            files.append(
                {
                    "path": path,
                    "filename": filename,
                    "video_id": video_id,
                    "size": size,
                    "sort_ts": sort_ts or float(stat.st_mtime),
                }
            )

        if total_bytes <= MAX_STORAGE_BYTES or not files:
            return {
                "removed": 0,
                "removed_bytes": 0,
                "total_bytes": total_bytes,
                "max_storage_bytes": MAX_STORAGE_BYTES,
            }

        current_bytes = total_bytes
        files.sort(key=lambda item: float(item.get("sort_ts") or 0))
        for item in files:
            if current_bytes <= MAX_STORAGE_BYTES and removed_bytes >= PRUNE_STORAGE_BYTES:
                break
            path = item["path"]
            size = int(item.get("size") or 0)
            try:
                path.unlink(missing_ok=True)
            except Exception:
                continue
            video_id = item.get("video_id")
            if video_id:
                index.pop(str(video_id), None)
            current_bytes = max(0, current_bytes - size)
            removed_bytes += size
            removed += 1

        if removed:
            _write_index_locked(index)
    return {
        "removed": removed,
        "removed_bytes": removed_bytes,
        "total_bytes": max(0, total_bytes - removed_bytes),
        "max_storage_bytes": MAX_STORAGE_BYTES,
    }


def _cleanup_loop() -> None:
    interval = max(60, int(CONFIG.get("cleanup_interval_seconds") or 3600))
    while not cleanup_stop.wait(interval):
        _cleanup_storage_once()


@app.on_event("startup")
def startup() -> None:
    threading.Thread(target=_cleanup_loop, daemon=True).start()


@app.on_event("shutdown")
def shutdown() -> None:
    cleanup_stop.set()


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "storage_dir": str(STORAGE_DIR),
        "public_base_url": str(CONFIG["public_base_url"]).rstrip("/"),
        "max_upload_mb": int(CONFIG["max_upload_mb"]),
        "max_storage_gb": int(CONFIG["max_storage_gb"]),
        "prune_storage_gb": int(CONFIG["prune_storage_gb"]),
    }


@app.post("/api/videos/upload")
async def upload_video(
    file: UploadFile = File(...),
    task_id: str | None = Form(default=None),
    source: str | None = Form(default=None),
    _auth: None = Depends(require_upload_auth),
) -> JSONResponse:
    content_type = str(file.content_type or "").lower().strip()
    if content_type and content_type not in set(CONFIG["allowed_content_types"]):
        raise HTTPException(status_code=400, detail=f"unsupported content type: {content_type}")

    ext = _safe_extension(file.filename or "", content_type)
    video_id = _safe_id(task_id) or uuid.uuid4().hex
    filename = f"{video_id}.{ext}"
    target = STORAGE_DIR / filename

    size = await _write_upload(file, target)
    created_at = _now()
    record = {
        "id": video_id,
        "filename": filename,
        "size": size,
        "content_type": content_type or None,
        "source": str(source or "").strip() or None,
        "url": _public_url(filename),
        "created_at": created_at,
    }
    _save_record(record)
    return JSONResponse(record)


@app.get("/api/videos/{video_id}")
def get_video(video_id: str, _auth: None = Depends(require_upload_auth)) -> dict[str, Any]:
    record = _get_record(_safe_id(video_id))
    if record is None:
        raise HTTPException(status_code=404, detail="video not found")
    return record


@app.delete("/api/videos/{video_id}")
def delete_video(video_id: str, _auth: None = Depends(require_upload_auth)) -> dict[str, Any]:
    record = _delete_record(_safe_id(video_id))
    if record is None:
        raise HTTPException(status_code=404, detail="video not found")
    filename = str(record.get("filename") or "")
    if filename:
        (STORAGE_DIR / Path(filename).name).unlink(missing_ok=True)
    return {"deleted": True, "id": str(record.get("id") or video_id)}


@app.post("/api/videos/cleanup")
def cleanup(_auth: None = Depends(require_upload_auth)) -> dict[str, int]:
    return _cleanup_storage_once()
