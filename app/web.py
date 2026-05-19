from __future__ import annotations

from functools import wraps
from flask import Flask, jsonify, redirect, render_template, request, session, url_for

from . import auth
from .config import AppConfig, parse_datetime
from .db import connect, list_distinct_urls, query_logs
from .errors import AppError, AuthError, parse_limit

FILTER_KEYS = ("keyword", "date_from", "date_to", "ip", "url", "group_by")


def _filters_from_request() -> dict[str, str]:
    return {k: request.args.get(k, "") for k in FILTER_KEYS}


def _serialize_row(row: dict) -> dict:
    return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def _rows_to_json(rows: list[dict]) -> list[dict]:
    return [_serialize_row(row) for row in rows]


def _table_columns(group_by: str) -> list[tuple[str, str]]:
    if group_by == "ip":
        return [
            ("remote_ip", "IP"),
            ("hits", "Запросов"),
            ("first_seen", "Первый запрос"),
            ("last_seen", "Последний запрос"),
        ]
    if group_by == "date":
        return [("log_date", "Дата"), ("hits", "Запросов")]
    return [
        ("request_time", "Время"),
        ("remote_ip", "IP"),
        ("method", "Метод"),
        ("url_path", "URL"),
        ("status_code", "Статус"),
        ("response_bytes", "Размер"),
    ]


def _format_cell(row: dict, key: str) -> str:
    value = row.get(key)
    if value is None:
        return "—"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def create_app(cfg: AppConfig) -> Flask:
    app = Flask(__name__, template_folder=str(cfg.templates_dir), static_folder=str(cfg.static_dir))
    app.secret_key = cfg.flask_secret
    app.config["CFG"] = cfg

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        if request.path.startswith("/api/"):
            status = 401 if exc.code == "AUTH_REQUIRED" else 400
            return jsonify(exc.to_dict()), status
        if session.get("user_id"):
            return render_template("error.html", message=exc.message), 400
        return render_template("login.html", error=exc.message), 400

    @app.errorhandler(404)
    def handle_not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": {"code": "NOT_FOUND", "message": "Страница не найдена"}}), 404
        if session.get("user_id"):
            return render_template("error.html", message="Страница не найдена"), 404
        return redirect(url_for("login"))

    @app.errorhandler(500)
    def handle_internal_error(_exc):
        if request.path.startswith("/api/"):
            return jsonify(
                {
                    "ok": False,
                    "error": {"code": "INTERNAL_ERROR", "message": "Внутренняя ошибка сервера"},
                }
            ), 500
        if session.get("user_id"):
            return render_template("error.html", message="Внутренняя ошибка сервера"), 500
        return render_template("login.html", error="Внутренняя ошибка сервера"), 500

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("user_id"):
                return redirect(url_for("index"))
            return render_template("login.html", error=request.args.get("error"))

        with connect(app.config["CFG"]) as conn:
            user = auth.verify_user(
                conn,
                request.form.get("username", ""),
                request.form.get("password", ""),
            )
        if not user:
            return render_template("login.html", error="Неверный логин или пароль")
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        return redirect(url_for("index"))

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        filters = _filters_from_request()
        loaded = request.args.get("load") == "1"
        urls: list[dict] = []
        records: list[dict] = []
        page_error: str | None = None
        group_by = filters.get("group_by", "")

        if loaded:
            cfg = app.config["CFG"]
            try:
                with connect(cfg) as conn:
                    urls = list_distinct_urls(
                        conn,
                        keyword=filters["keyword"] or None,
                        date_from=parse_datetime(filters["date_from"]),
                        date_to=parse_datetime(filters["date_to"], end_of_day=True),
                    )
                    records = query_logs(
                        conn,
                        date_from=parse_datetime(filters["date_from"]),
                        date_to=parse_datetime(filters["date_to"], end_of_day=True),
                        remote_ip=filters["ip"] or None,
                        keyword=filters["keyword"] or None,
                        url_path=filters["url"] or None,
                        group_by=group_by or None,
                        limit=200,
                    )
            except AppError as exc:
                page_error = exc.message
                loaded = False

        columns = _table_columns(group_by)
        table_rows = [
            [_format_cell(row, key) for key, _ in columns]
            for row in records
        ]

        return render_template(
            "dashboard.html",
            username=session.get("username", ""),
            filters=filters,
            loaded=loaded,
            urls=urls,
            columns=columns,
            table_rows=table_rows,
            page_error=page_error,
            load_summary=f"Загружено записей: {len(records)}" if loaded and not page_error else None,
        )

    @app.route("/api/urls")
    def api_urls():
        if not session.get("user_id"):
            raise AuthError()
        cfg = app.config["CFG"]
        with connect(cfg) as conn:
            rows = list_distinct_urls(
                conn,
                keyword=request.args.get("keyword") or None,
                date_from=parse_datetime(request.args.get("date_from")),
                date_to=parse_datetime(request.args.get("date_to"), end_of_day=True),
            )
        return jsonify({"ok": True, "data": _rows_to_json(rows)})

    @app.route("/api/logs")
    def api_logs():
        if not session.get("user_id"):
            raise AuthError()
        cfg = app.config["CFG"]
        with connect(cfg) as conn:
            rows = query_logs(
                conn,
                date_from=parse_datetime(request.args.get("date_from")),
                date_to=parse_datetime(request.args.get("date_to"), end_of_day=True),
                remote_ip=request.args.get("ip") or None,
                keyword=request.args.get("keyword") or None,
                url_path=request.args.get("url") or None,
                group_by=request.args.get("group_by") or None,
                limit=parse_limit(request.args.get("limit")),
            )
        return jsonify({"ok": True, "data": _rows_to_json(rows)})

    return app
