import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from .config import ROOT, Settings

FORMATS = {
    ".pdf": ("pdf", "application/pdf"),
    ".docx": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".png": ("image", "image/png"),
    ".jpg": ("image", "image/jpeg"),
    ".jpeg": ("image", "image/jpeg"),
    ".webp": ("image", "image/webp"),
}


def safe_filename(filename: str):
    name = filename.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(c for c in name if c >= " " and c not in '\x7f"')
    return name[:255] or "file"


def save_upload(data: bytes, filename: str, settings: Settings):
    name = safe_filename(filename)
    suffix = Path(name).suffix.lower()
    if suffix not in FORMATS:
        raise ValueError("Поддерживаются PDF, DOCX, PNG, JPG и WEBP.")
    if not data:
        raise ValueError("Файл пустой.")
    key = f"{uuid4()}{suffix}"
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    path = settings.storage_dir / key
    path.write_bytes(data)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "kotolancer.extract", str(path)],
            cwd=ROOT,
            capture_output=True,
            timeout=settings.extraction_timeout,
        )
        extraction = json.loads(result.stdout.decode("utf-8"))
    except (subprocess.TimeoutExpired, ValueError, UnicodeError, OSError):
        extraction = {
            "status": "failed",
            "blocks": [],
            "warnings": ["Разбор остановлен по лимиту или ошибке. Оригинал сохранён."],
        }
    kind, mime = FORMATS[suffix]
    return {
        "title": name,
        "kind": kind,
        "mime": mime,
        "file_key": key,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "extraction_status": extraction["status"],
    }, extraction
