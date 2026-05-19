from __future__ import annotations

import configparser
import fnmatch
import os
import secrets
from datetime import datetime, time, timezone
from pathlib import Path

from . import PROJECT_ROOT
from .errors import AppError, ConfigError


def default_config_text() -> str:
    secret = secrets.token_hex(32)
    return f"""[database]
host = localhost
port = 5432
dbname = apache_logs
user = postgres
password = postgres
maintenance_dbname = postgres

[server]
host = 0.0.0.0
port = 8080
secret_key = {secret}

[logs]
directory = data/sample_logs
file_mask = access*.log
read_chunk = 65536
"""


def ensure_config_file() -> Path:
    ini = PROJECT_ROOT / "config.ini"
    if not ini.is_file():
        ini.write_text(default_config_text(), encoding="utf-8")
    return ini


def ensure_sample_logs(cfg: "AppConfig") -> None:
    has_logs = (
        any(
            p.is_file() and fnmatch.fnmatch(p.name, cfg.logs_file_mask)
            for p in cfg.logs_directory.iterdir()
        )
        if cfg.logs_directory.is_dir()
        else False
    )
    if has_logs:
        return
    sample = cfg.logs_directory / "access.log"
    sample.write_text(
        "\n".join(
            [
                '127.0.0.1 - - [10/Oct/2024:13:55:36 -0700] "GET /apache_pb.gif HTTP/1.0" 200 2326',
                '192.168.1.10 - frank [10/Oct/2024:13:55:37 -0700] "GET /index.html HTTP/1.0" 200 5120',
                '192.168.1.10 - frank [10/Oct/2024:13:56:01 -0700] "GET /api/users HTTP/1.1" 200 891',
                '10.0.0.5 - - [11/Oct/2024:09:12:00 -0700] "POST /login HTTP/1.1" 401 128',
                '10.0.0.5 - - [11/Oct/2024:09:12:05 -0700] "POST /login HTTP/1.1" 200 64',
                '203.0.113.7 - - [12/Oct/2024:14:22:11 -0700] "GET /static/app.js HTTP/1.1" 304 -',
                '203.0.113.7 - - [12/Oct/2024:14:22:12 -0700] "GET /reports?q=2024 HTTP/1.1" 200 4096 "http://example.com/" "Mozilla/5.0"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def prepare_project(cfg: "AppConfig") -> None:
    os.makedirs(cfg.logs_directory, exist_ok=True)
    ensure_sample_logs(cfg)


def load_config(config_path: str | None = None) -> "AppConfig":
    if config_path:
        path = Path(config_path).resolve()
        if not path.is_file():
            raise ConfigError(f"Файл настроек не найден: {path}")
    else:
        path = ensure_config_file()
    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")
    return AppConfig(path, parser)


class AppConfig:
    def __init__(self, path: Path, parser: configparser.ConfigParser) -> None:
        base = PROJECT_ROOT
        p = parser

        self.db_host = _get(p, "database", "host", "localhost")
        self.db_port = _getint(p, "database", "port", 5432)
        self.db_name = _get(p, "database", "dbname", "apache_logs")
        self.db_user = _get(p, "database", "user", "postgres")
        self.db_password = _get(p, "database", "password", "postgres")
        self.db_maintenance_name = _get(p, "database", "maintenance_dbname", "postgres")
        self.db_connect_timeout = _getint(p, "database", "connect_timeout", 5)

        self.server_host = _get(p, "server", "host", "0.0.0.0")
        self.server_port = _getint(p, "server", "port", 8080)
        self.flask_secret = _get(p, "server", "secret_key", "change-me-in-config")

        logs_dir = _get(p, "logs", "directory", "data/sample_logs")
        self.logs_directory = (base / logs_dir).resolve()
        self.logs_file_mask = _get(p, "logs", "file_mask", "access*.log")
        self.logs_read_chunk = _getint(p, "logs", "read_chunk", 65536)

        self.templates_dir = (base / "templates").resolve()
        self.static_dir = (base / "static").resolve()

    @property
    def dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_name} "
            f"user={self.db_user} password={self.db_password} "
            f"connect_timeout={self.db_connect_timeout}"
        )

    @property
    def maintenance_dsn(self) -> str:
        return (
            f"host={self.db_host} port={self.db_port} dbname={self.db_maintenance_name} "
            f"user={self.db_user} password={self.db_password} "
            f"connect_timeout={self.db_connect_timeout}"
        )

    def server_urls(self) -> list[str]:
        host = "127.0.0.1" if self.server_host in ("0.0.0.0", "") else self.server_host
        if self.server_port == 80:
            return [f"http://{host}"]
        return [f"http://{host}:{self.server_port}"]


def _get(p: configparser.ConfigParser, section: str, key: str, default: str) -> str:
    return p.get(section, key, fallback=default).strip()


def _getint(p: configparser.ConfigParser, section: str, key: str, default: int) -> int:
    return p.getint(section, key, fallback=default)


def parse_datetime(value: str | None, end_of_day: bool = False):
    if not value:
        return None
    value = value.strip()
    try:
        dt = datetime.strptime(value, "%Y-%m-%d")
        if end_of_day:
            dt = datetime.combine(dt.date(), time(23, 59, 59))
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise AppError(f"Неверный формат даты: {value}")
