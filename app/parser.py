from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Generator

from .errors import AppError

COMBINED_RE = re.compile(
    r'^(\S+)\s+(\S+)\s+(\S+)\s+\[([^\]]+)\]\s+"([^"]*)"\s+(\d+)\s+(\S+)'
    r'(?:\s+"([^"]*)"\s+"([^"]*)")?\s*$'
)

TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"


@dataclass
class ParsedLine:
    remote_ip: str
    ident: str
    auth_user: str
    request_time: datetime
    request_line: str
    method: str | None
    url_path: str | None
    query_string: str | None
    protocol: str | None
    status_code: int
    response_bytes: int | None
    referer: str | None
    user_agent: str | None
    source_file: str
    line_hash: str


def _parse_request_line(request: str) -> tuple[str | None, str | None, str | None, str | None]:
    parts = request.split()
    if len(parts) < 2:
        return None, None, None, request
    method = parts[0]
    target = parts[1]
    protocol = parts[2] if len(parts) > 2 else None
    url_path = target
    query_string = None
    if "?" in target:
        url_path, query_string = target.split("?", 1)
    return method, url_path, query_string, protocol


def _parse_bytes(value: str) -> int | None:
    if value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_time(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, TIME_FORMAT)
    except ValueError as exc:
        raise AppError(f"Неверный формат времени в логе: [{raw}]", "PARSE_ERROR") from exc


def parse_line(line: str, source_file: str) -> ParsedLine | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    match = COMBINED_RE.match(line)
    if not match:
        raise AppError(f"Строка не соответствует формату Apache combined: {line[:120]}...", "PARSE_ERROR")
    groups = match.groups()
    remote_ip, ident, auth_user, time_raw, request, status_s, bytes_s = groups[:7]
    referer = groups[7] if len(groups) > 7 else None
    user_agent = groups[8] if len(groups) > 8 else None

    method, url_path, query_string, protocol = _parse_request_line(request)
    line_hash = hashlib.sha256(f"{source_file}|{line}".encode()).hexdigest()

    return ParsedLine(
        remote_ip=remote_ip,
        ident=ident or "-",
        auth_user=auth_user or "-",
        request_time=_parse_time(time_raw),
        request_line=request,
        method=method,
        url_path=url_path,
        query_string=query_string,
        protocol=protocol,
        status_code=int(status_s),
        response_bytes=_parse_bytes(bytes_s),
        referer=None if referer in (None, "-") else referer,
        user_agent=None if user_agent in (None, "-") else user_agent,
        source_file=source_file,
        line_hash=line_hash,
    )


def parsed_to_row(p: ParsedLine) -> dict:
    return {
        "remote_ip": p.remote_ip,
        "ident": p.ident,
        "auth_user": p.auth_user,
        "request_time": p.request_time,
        "request_line": p.request_line,
        "method": p.method,
        "url_path": p.url_path,
        "query_string": p.query_string,
        "protocol": p.protocol,
        "status_code": p.status_code,
        "response_bytes": p.response_bytes,
        "referer": p.referer,
        "user_agent": p.user_agent,
        "source_file": p.source_file,
        "line_hash": p.line_hash,
    }


@dataclass
class ParseResult:
    errors: list[str]


def parse_file_incremental(
    path: Path,
    chunk_size: int = 65536,
    batch_size: int = 500,
) -> Generator[tuple[list[dict], ParseResult, int, int], None, None]:
    if not path.is_file():
        raise AppError(f"Файл не найден: {path}", "PARSE_ERROR")

    total_bytes = path.stat().st_size
    lines_read = 0
    errors: list[str] = []
    batch: list[dict] = []

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        partial = ""
        while True:
            data = fh.read(chunk_size)
            if not data:
                break
            partial += data
            bytes_read = fh.tell()
            while "\n" in partial:
                line, partial = partial.split("\n", 1)
                lines_read += 1
                try:
                    parsed = parse_line(line, str(path))
                except AppError as exc:
                    errors.append(f"Строка {lines_read}: {exc.message}")
                    continue
                if parsed:
                    batch.append(parsed_to_row(parsed))
                if len(batch) >= batch_size:
                    yield batch, ParseResult(errors.copy()), bytes_read, total_bytes
                    batch = []
        if partial.strip():
            lines_read += 1
            try:
                parsed = parse_line(partial, str(path))
                if parsed:
                    batch.append(parsed_to_row(parsed))
            except AppError as exc:
                errors.append(f"Строка {lines_read}: {exc.message}")
        bytes_read = fh.tell()

    if batch:
        yield batch, ParseResult(errors), bytes_read, total_bytes
    elif lines_read == 0:
        yield [], ParseResult(errors), 0, total_bytes
