from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig
from .db import connect, insert_access_logs
from .errors import AppError
from .parser import parse_file_incremental


@dataclass
class ParseRunReport:
    total_inserted: int = 0
    errors: list[str] = field(default_factory=list)


def discover_log_files(cfg: AppConfig) -> list[Path]:
    if not cfg.logs_directory.is_dir():
        raise AppError(f"Каталог логов не найден: {cfg.logs_directory}")
    return [
        p
        for p in sorted(cfg.logs_directory.iterdir())
        if p.is_file() and fnmatch.fnmatch(p.name, cfg.logs_file_mask)
    ]


def run_parse(cfg: AppConfig) -> ParseRunReport:
    paths = discover_log_files(cfg)
    if not paths:
        raise AppError(f"Нет файлов «{cfg.logs_file_mask}» в {cfg.logs_directory}")
    report = ParseRunReport()
    with connect(cfg) as conn:
        for path in paths:
            for batch, result in parse_file_incremental(path, chunk_size=cfg.logs_read_chunk):
                report.errors.extend(result.errors)
                if batch:
                    report.total_inserted += insert_access_logs(conn, batch)
    return report
