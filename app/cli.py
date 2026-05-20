from __future__ import annotations

import argparse
import getpass
import sys

from app import auth, log_service
from app.config import AppConfig, load_config, parse_datetime, prepare_project
from app.db import bootstrap, connect, query_logs
from app.errors import AppError, ValidationError
from app.log_service import ParseRunReport
from app.web import create_app

_PARSE_ERROR_PREVIEW = 20


def _print_parse_report(report: ParseRunReport) -> None:
    print(f"Добавлено записей: {report.total_inserted}")
    if not report.errors:
        return
    print(f"Ошибки при разборе ({len(report.errors)}):", file=sys.stderr)
    for err in report.errors[:_PARSE_ERROR_PREVIEW]:
        print(f"  • {err}", file=sys.stderr)
    rest = len(report.errors) - _PARSE_ERROR_PREVIEW
    if rest > 0:
        print(f"  … и ещё {rest}", file=sys.stderr)


def cmd_register(cfg: AppConfig, username: str | None) -> None:
    if not username:
        username = input("Имя пользователя: ").strip()
        password = getpass.getpass("Пароль: ")
    else:
        password = username
    with connect(cfg) as conn:
        try:
            auth.create_user(conn, username, password)
        except ValidationError as exc:
            print(f"Ошибка: {exc.message}", file=sys.stderr)
            return
    print(f"Пользователь «{username}» создан (пароль: {password}).")


def cmd_parse(cfg: AppConfig) -> None:
    report = log_service.run_parse(cfg)
    _print_parse_report(report)


def cmd_view(cfg: AppConfig, args) -> None:
    with connect(cfg) as conn:
        rows = query_logs(
            conn,
            date_from=parse_datetime(args.date_from) if args.date_from else None,
            date_to=parse_datetime(args.date_to, end_of_day=True) if args.date_to else None,
            remote_ip=args.ip,
            keyword=args.keyword,
            url_path=args.url,
            group_by=args.group_by,
            limit=args.limit,
        )
    if not rows:
        print("Нет данных")
        return
    if args.group_by == "ip":
        cols = ["remote_ip", "hits", "first_seen", "last_seen"]
    elif args.group_by == "date":
        cols = ["log_date", "hits"]
    else:
        cols = ["request_time", "remote_ip", "method", "url_path", "status_code"]
    print(" | ".join(cols))
    print("-" * 60)
    for row in rows:
        print(" | ".join(str(row.get(c, "")) for c in cols))


def cmd_serve(cfg: AppConfig) -> None:
    flask_app = create_app(cfg)
    for url in cfg.server_urls():
        print(f"Откройте в браузере: {url}/")
    flask_app.run(host=cfg.server_host, port=cfg.server_port, debug=False, threaded=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Агрегатор access-логов Apache")
    parser.add_argument("-c", "--config", help="Путь к config.ini")
    sub = parser.add_subparsers(dest="command")

    reg = sub.add_parser("register", help="Создать пользователя")
    reg.add_argument("username", nargs="?")
    sub.add_parser("parse", help="Разбор логов в БД")
    v = sub.add_parser("view", help="Просмотр данных в консоли")
    v.add_argument("--date-from", dest="date_from", help="Дата начала (ГГГГ-ММ-ДД)")
    v.add_argument("--date-to", dest="date_to", help="Дата окончания (ГГГГ-ММ-ДД)")
    v.add_argument("--ip", help="Фильтр по IP")
    v.add_argument("--url", help="Фильтр по URL")
    v.add_argument("--keyword", help="Ключевое слово")
    v.add_argument("--group-by", choices=("ip", "date"), help="Группировка")
    v.add_argument("--limit", type=int, default=50)
    sub.add_parser("serve", help="Веб-интерфейс")
    sub.add_parser("cron", help="Разбор логов (для cron)")

    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 1

    try:
        cfg = load_config(args.config)
        prepare_project(cfg)
        bootstrap(cfg)
    except AppError as exc:
        print(f"Ошибка: {exc.message}", file=sys.stderr)
        return 1

    try:
        if args.command == "register":
            cmd_register(cfg, args.username)
        elif args.command == "parse":
            cmd_parse(cfg)
        elif args.command == "view":
            cmd_view(cfg, args)
        elif args.command == "cron":
            cmd_parse(cfg)
        elif args.command == "serve":
            cmd_serve(cfg)
    except AppError as exc:
        print(f"Ошибка: {exc.message}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nСтоп.")
        return 130
    return 0
