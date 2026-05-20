from __future__ import annotations

import fnmatch
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from .config import AppConfig
from .errors import ValidationError


def safe_log_basename(name: str) -> str:
    base = secure_filename(name)
    if not base or base in (".", ".."):
        raise ValidationError("Недопустимое имя файла")
    return base


def _resolve_log_path(cfg: AppConfig, name: str) -> Path:
    base = safe_log_basename(name)
    path = (cfg.logs_directory / base).resolve()
    if path.parent != cfg.logs_directory.resolve():
        raise ValidationError("Недопустимый путь к файлу")
    return path


def matches_log_mask(cfg: AppConfig, name: str) -> bool:
    return fnmatch.fnmatch(safe_log_basename(name), cfg.logs_file_mask)


def file_to_meta(path: Path) -> dict:
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    return {
        "name": path.name,
        "size": stat.st_size,
        "size_human": _format_bytes(stat.st_size),
        "modified": modified.isoformat(),
    }


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def list_uploaded_files(cfg: AppConfig) -> list[dict]:
    if not cfg.logs_directory.is_dir():
        return []
    paths = [
        p
        for p in sorted(cfg.logs_directory.iterdir())
        if p.is_file() and fnmatch.fnmatch(p.name, cfg.logs_file_mask)
    ]
    return [file_to_meta(p) for p in paths]


def save_upload(cfg: AppConfig, file_storage: FileStorage) -> dict:
    if not file_storage or not file_storage.filename:
        raise ValidationError("Файл не выбран")
    name = safe_log_basename(file_storage.filename)
    if not matches_log_mask(cfg, name):
        raise ValidationError(f"Имя файла должно соответствовать маске «{cfg.logs_file_mask}»")
    path = _resolve_log_path(cfg, name)
    data = file_storage.read()
    if not data:
        raise ValidationError("Пустой файл")
    if len(data) > cfg.upload_max_bytes:
        raise ValidationError(f"Размер файла превышает {_format_bytes(cfg.upload_max_bytes)}")
    path.write_bytes(data)
    return file_to_meta(path)


def get_file_detail(cfg: AppConfig, name: str, *, preview_lines: int | None = None) -> dict:
    preview_lines = preview_lines or cfg.upload_preview_lines
    path = _resolve_log_path(cfg, name)
    if not path.is_file():
        raise ValidationError("Файл не найден")
    meta = file_to_meta(path)
    lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for _ in range(preview_lines):
            line = fh.readline()
            if not line:
                break
            lines.append(line.rstrip("\n\r"))
    meta["preview_lines"] = lines
    meta["preview_count"] = len(lines)
    return meta
