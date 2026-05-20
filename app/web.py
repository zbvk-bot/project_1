from __future__ import annotations

import json
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, session, url_for
from flask_sock import Sock

from . import auth
from .config import AppConfig, parse_datetime
from .db import connect, list_distinct_urls, query_logs
from .errors import AppError, AuthError, ValidationError, parse_limit
from .log_service import run_parse_file
from .upload_service import get_file_detail, list_uploaded_files, matches_log_mask, save_upload

FILTER_KEYS = ("keyword", "date_from", "date_to", "ip", "url", "group_by")
sock = Sock()


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


def _require_api_user() -> None:
    if not session.get("user_id"):
        raise AuthError()


def create_app(cfg: AppConfig) -> Flask:
    app = Flask(__name__, template_folder=str(cfg.templates_dir), static_folder=str(cfg.static_dir))
    app.secret_key = cfg.flask_secret
    app.config["CFG"] = cfg
    app.config["MAX_CONTENT_LENGTH"] = cfg.upload_max_bytes
    sock.init_app(app)

    def login_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("user_id"):
                return redirect(url_for("login"))
            return view(*args, **kwargs)

        return wrapped

    @app.errorhandler(AppError)
    def handle_app_error(exc: AppError):
        if request.path.startswith("/api/") or request.path.startswith("/ws/"):
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

    @app.errorhandler(413)
    def handle_too_large(_exc):
        if request.path.startswith("/api/"):
            return jsonify(
                {
                    "ok": False,
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Файл слишком большой",
                    },
                }
            ), 413
        return render_template("error.html", message="Файл слишком большой"), 413

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
        cfg = app.config["CFG"]
        return render_template(
            "dashboard.html",
            username=session.get("username", ""),
            file_mask=cfg.logs_file_mask,
            max_upload_human=_format_bytes(cfg.upload_max_bytes),
        )

    @app.route("/api/files", methods=["GET"])
    def api_files():
        _require_api_user()
        cfg = app.config["CFG"]
        return jsonify({"ok": True, "data": list_uploaded_files(cfg)})

    @app.route("/api/files/<path:name>", methods=["GET"])
    def api_file_detail(name: str):
        _require_api_user()
        cfg = app.config["CFG"]
        return jsonify({"ok": True, "data": get_file_detail(cfg, name)})

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        _require_api_user()
        cfg = app.config["CFG"]
        uploaded = request.files.get("file")
        meta = save_upload(cfg, uploaded)
        return jsonify({"ok": True, "data": meta})

    @app.route("/api/urls")
    def api_urls():
        _require_api_user()
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
        _require_api_user()
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
        group_by = request.args.get("group_by") or ""
        return jsonify(
            {
                "ok": True,
                "data": _rows_to_json(rows),
                "columns": _table_columns(group_by),
            }
        )

    @sock.route("/ws/parse")
    def ws_parse(ws):
        if not session.get("user_id"):
            ws.send(json.dumps({"type": "error", "message": "Требуется вход"}))
            return
        cfg = app.config["CFG"]
        try:
            raw = ws.receive(timeout=30)
        except TypeError:
            raw = ws.receive()
        if not raw:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            ws.send(json.dumps({"type": "error", "message": "Неверный JSON"}))
            return
        filename = (payload.get("file") or "").strip()
        if not filename:
            ws.send(json.dumps({"type": "error", "message": "Не указан файл"}))
            return
        if not matches_log_mask(cfg, filename):
            ws.send(
                json.dumps(
                    {
                        "type": "error",
                        "message": f"Файл должен соответствовать маске «{cfg.logs_file_mask}»",
                    }
                )
            )
            return
        path = (cfg.logs_directory / Path(filename).name).resolve()
        if path.parent != cfg.logs_directory.resolve() or not path.is_file():
            ws.send(json.dumps({"type": "error", "message": "Файл не найден"}))
            return

        def on_progress(bytes_read: int, total_bytes: int, inserted: int) -> None:
            pct = round(100 * bytes_read / total_bytes) if total_bytes else 100
            ws.send(
                json.dumps(
                    {
                        "type": "progress",
                        "bytes_read": bytes_read,
                        "total_bytes": total_bytes,
                        "percent": pct,
                        "inserted": inserted,
                    }
                )
            )

        try:
            report = run_parse_file(cfg, path, on_progress=on_progress)
        except AppError as exc:
            ws.send(json.dumps({"type": "error", "message": exc.message}))
            return
        ws.send(
            json.dumps(
                {
                    "type": "done",
                    "filename": report.filename,
                    "inserted": report.total_inserted,
                    "errors": report.errors[:20],
                    "error_count": len(report.errors),
                }
            )
        )

    return app


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"
