#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Домашнее облако — локальный файловый сервер на Flask.
Запуск: python app.py
Открыть в браузере: http://127.0.0.1:8000
"""

import os
import sys
import json
import hashlib
import secrets
import socket
import shutil
import qrcode
import io
import base64
from datetime import datetime
from pathlib import Path
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    send_from_directory, flash, session, jsonify, abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

# =======================================================================
# КОНФИГУРАЦИЯ
# =======================================================================
BASE_DIR = Path(__file__).resolve().parent
STORAGE_DIR = BASE_DIR / "storage"
CONFIG_FILE = BASE_DIR / "config.json"
SHARED_LINKS_FILE = BASE_DIR / "shared_links.json"

HOST = "127.0.0.1"
PORT = 8000
MAX_FILE_SIZE = 100 * 1024 * 1024 * 1024  # 100 ГБ на файл
ALLOWED_ALL = True  # разрешить любые расширения

# =======================================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# =======================================================================
def ensure_dirs():
    """Создаём папки хранилища при первом запуске."""
    STORAGE_DIR.mkdir(exist_ok=True)


def load_config():
    """Загружаем конфигурацию (логин/пароль) из config.json.
    При первом запуске — создаём дефолтный admin/admin и просим сменить."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    # Дефолтная конфигурация
    config = {
        "username": "admin",
        "password_hash": generate_password_hash("admin"),
        "must_change_password": True,
        "session_secret": secrets.token_hex(32),
    }
    save_config(config)
    return config


def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def load_shared_links():
    """Загружаем словарь публичных ссылок."""
    if SHARED_LINKS_FILE.exists():
        try:
            with open(SHARED_LINKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_shared_links(links):
    with open(SHARED_LINKS_FILE, "w", encoding="utf-8") as f:
        json.dump(links, f, ensure_ascii=False, indent=2)


def human_size(num_bytes):
    """Человеко-читаемый размер файла."""
    for unit in ["Б", "КБ", "МБ", "ГБ", "ТБ"]:
        if abs(num_bytes) < 1024.0:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024.0
    return f"{num_bytes:.1f} ПБ"


def get_disk_usage():
    """Возвращает информацию о месте на диске, где лежит хранилище."""
    try:
        total, used, free = shutil.disk_usage(STORAGE_DIR)
        # Считаем, что занято облаком = сумма размеров файлов в storage
        cloud_used = sum(f.stat().st_size for f in STORAGE_DIR.rglob("*") if f.is_file())
        return {
            "total": total,
            "used": used,
            "free": free,
            "cloud_used": cloud_used,
            "total_h": human_size(total),
            "used_h": human_size(used),
            "free_h": human_size(free),
            "cloud_used_h": human_size(cloud_used),
            "used_percent": round((used / total) * 100, 1) if total else 0,
            "cloud_percent": round((cloud_used / total) * 100, 1) if total else 0,
        }
    except Exception as e:
        return {
            "total": 0, "used": 0, "free": 0, "cloud_used": 0,
            "total_h": "—", "used_h": "—", "free_h": "—", "cloud_used_h": "—",
            "used_percent": 0, "cloud_percent": 0,
            "error": str(e),
        }


def list_files(rel_path=""):
    """Возвращает список файлов и папок по относительному пути."""
    target = STORAGE_DIR / rel_path
    if not target.exists() or not target.is_dir():
        return [], []

    dirs, files = [], []
    try:
        for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if item.name.startswith("."):
                continue
            stat = item.stat()
            info = {
                "name": item.name,
                "path": str(item.relative_to(STORAGE_DIR)),
                "is_dir": item.is_dir(),
                "size": human_size(stat.st_size) if item.is_file() else "—",
                "size_bytes": stat.st_size if item.is_file() else 0,
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
            }
            (dirs if item.is_dir() else files).append(info)
    except PermissionError:
        pass
    return dirs, files


def get_local_ip():
    """Пытаемся узнать локальный IP-адрес для доступа с других устройств."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def make_qr_code(data: str) -> str:
    """Генерирует QR-код как base64 PNG."""
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def search_files(query):
    """Рекурсивный поиск файлов по имени в хранилище."""
    results = []
    if not query:
        return results
    query_lower = query.lower()
    try:
        for path in STORAGE_DIR.rglob("*"):
            if path.name.startswith("."):
                continue
            if query_lower in path.name.lower():
                stat = path.stat()
                results.append({
                    "name": path.name,
                    "path": str(path.relative_to(STORAGE_DIR)),
                    "is_dir": path.is_dir(),
                    "size": human_size(stat.st_size) if path.is_file() else "—",
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m.%Y %H:%M"),
                })
                if len(results) >= 200:
                    break
    except Exception:
        pass
    return results


def safe_join_storage(rel_path):
    """Безопасно соединяет путь с хранилищем, защищая от выхода за пределы."""
    rel_path = rel_path.lstrip("/\\")
    full = (STORAGE_DIR / rel_path).resolve()
    try:
        full.relative_to(STORAGE_DIR.resolve())
    except ValueError:
        return None
    return full


# =======================================================================
# ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ
# =======================================================================
ensure_dirs()
config = load_config()

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE  # 100 ГБ
app.secret_key = config.get("session_secret", secrets.token_hex(32))


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# =======================================================================
# МАРШРУТЫ
# =======================================================================
@app.route("/")
@login_required
def index():
    return redirect(url_for("browse"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if (username == config.get("username") and
                check_password_hash(config.get("password_hash", ""), password)):
            session["user"] = username
            session.permanent = True
            flash("Вы успешно вошли!", "success")
            return redirect(url_for("browse"))
        flash("Неверный логин или пароль", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Вы вышли из системы", "info")
    return redirect(url_for("login"))


@app.route("/browse/")
@app.route("/browse/<path:rel_path>")
@login_required
def browse(rel_path=""):
    full = safe_join_storage(rel_path)
    if full is None or not full.exists():
        flash("Папка не найдена", "error")
        return redirect(url_for("browse"))

    if full.is_file():
        # Если запросили файл — отдаём его
        return send_from_directory(full.parent, full.name, as_attachment=False)

    dirs, files = list_files(rel_path)
    # Хлебные крошки
    breadcrumbs = []
    parts = rel_path.split("/") if rel_path else []
    cumulative = ""
    for p in parts:
        cumulative = f"{cumulative}/{p}" if cumulative else p
        breadcrumbs.append({"name": p, "path": cumulative})

    # QR-код для текущего URL
    local_ip = get_local_ip()
    lan_url = f"http://{local_ip}:{PORT}/"
    qr_b64 = make_qr_code(lan_url)

    return render_template(
        "dashboard.html",
        dirs=dirs, files=files,
        current_path=rel_path,
        breadcrumbs=breadcrumbs,
        local_url=f"http://127.0.0.1:{PORT}",
        lan_url=lan_url,
        qr_code=qr_b64,
        username=session.get("user"),
        must_change_password=config.get("must_change_password", False),
        total_files=sum(1 for _ in STORAGE_DIR.rglob("*") if _.is_file()),
        total_size=human_size(sum(f.stat().st_size for f in STORAGE_DIR.rglob("*") if f.is_file())),
        disk=get_disk_usage(),
    )


@app.route("/upload", methods=["POST"])
@login_required
def upload():
    rel_path = request.form.get("path", "")
    target_dir = safe_join_storage(rel_path)
    if target_dir is None:
        abort(400)
    target_dir.mkdir(parents=True, exist_ok=True)

    if "files" not in request.files:
        if request.accept_mimetypes.accept_json:
            return jsonify({"success": False, "error": "Не выбраны файлы для загрузки"}), 400
        flash("Не выбраны файлы для загрузки", "error")
        return redirect(url_for("browse", rel_path=rel_path))

    files = request.files.getlist("files")
    success, failed = 0, 0
    uploaded_names = []
    for f in files:
        if f.filename == "":
            continue
        filename = secure_filename(f.filename)
        if not filename:
            filename = "file_" + secrets.token_hex(4)
        # Если файл существует — добавляем суффикс
        dest = target_dir / filename
        if dest.exists():
            stem, ext = os.path.splitext(filename)
            dest = target_dir / f"{stem}_{secrets.token_hex(3)}{ext}"
        try:
            f.save(dest)
            success += 1
            uploaded_names.append(dest.name)
        except Exception as e:
            print(f"Ошибка загрузки {filename}: {e}", file=sys.stderr)
            failed += 1

    # Если запрос через XHR (прогресс-загрузка) — возвращаем JSON
    if request.accept_mimetypes.accept_json:
        if success:
            return jsonify({
                "success": True,
                "uploaded": success,
                "failed": failed,
                "names": uploaded_names,
            })
        else:
            return jsonify({"success": False, "error": "Не удалось загрузить файлы"}), 500

    # Обычная форма — редирект с flash
    if success:
        flash(f"Загружено файлов: {success}" + (f", ошибок: {failed}" if failed else ""), "success")
    else:
        flash("Не удалось загрузить файлы", "error")
    return redirect(url_for("browse", rel_path=rel_path))


@app.route("/create_folder", methods=["POST"])
@login_required
def create_folder():
    rel_path = request.form.get("path", "")
    folder_name = secure_filename(request.form.get("name", ""))
    if not folder_name:
        flash("Введите имя папки", "error")
        return redirect(url_for("browse", rel_path=rel_path))
    target = safe_join_storage(rel_path + "/" + folder_name)
    if target is None:
        abort(400)
    try:
        target.mkdir(parents=True, exist_ok=True)
        flash(f"Папка «{folder_name}» создана", "success")
    except Exception as e:
        flash(f"Ошибка: {e}", "error")
    return redirect(url_for("browse", rel_path=rel_path))


@app.route("/download/<path:rel_path>")
@login_required
def download(rel_path):
    full = safe_join_storage(rel_path)
    if full is None or not full.exists() or not full.is_file():
        abort(404)
    return send_from_directory(full.parent, full.name, as_attachment=True)


@app.route("/delete/<path:rel_path>", methods=["POST"])
@login_required
def delete(rel_path):
    full = safe_join_storage(rel_path)
    if full is None or not full.exists():
        abort(404)
    try:
        if full.is_dir():
            import shutil
            shutil.rmtree(full)
        else:
            full.unlink()
        flash(f"«{full.name}» удалено", "success")
    except Exception as e:
        flash(f"Ошибка удаления: {e}", "error")
    # Редирект в родительскую папку
    parent = str(full.parent.relative_to(STORAGE_DIR)) if full.parent != STORAGE_DIR else ""
    return redirect(url_for("browse", rel_path=parent))


@app.route("/share/<path:rel_path>", methods=["POST"])
@login_required
def share(rel_path):
    full = safe_join_storage(rel_path)
    if full is None or not full.exists() or not full.is_file():
        abort(404)
    links = load_shared_links()
    token = secrets.token_urlsafe(12)
    links[token] = rel_path
    save_shared_links(links)
    return jsonify({"url": url_for("shared", token=token, _external=True), "token": token})


@app.route("/s/<token>")
def shared(token):
    links = load_shared_links()
    rel_path = links.get(token)
    if not rel_path:
        abort(404)
    full = safe_join_storage(rel_path)
    if full is None or not full.exists():
        abort(404)
    return send_from_directory(full.parent, full.name, as_attachment=False)


@app.route("/search")
@login_required
def search():
    q = request.args.get("q", "").strip()
    results = search_files(q) if q else []
    return render_template(
        "dashboard.html",
        dirs=[], files=results,
        current_path="",
        breadcrumbs=[],
        local_url=f"http://127.0.0.1:{PORT}",
        lan_url=f"http://{get_local_ip()}:{PORT}/",
        qr_code=make_qr_code(f"http://{get_local_ip()}:{PORT}/"),
        username=session.get("user"),
        must_change_password=config.get("must_change_password", False),
        total_files=sum(1 for _ in STORAGE_DIR.rglob("*") if _.is_file()),
        total_size=human_size(sum(f.stat().st_size for f in STORAGE_DIR.rglob("*") if f.is_file())),
        disk=get_disk_usage(),
        search_query=q,
        is_search=True,
    )


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    global config
    if request.method == "POST":
        new_username = request.form.get("username", "").strip()
        new_password = request.form.get("new_password", "")
        old_password = request.form.get("old_password", "")
        if not new_username:
            flash("Имя пользователя не может быть пустым", "error")
            return redirect(url_for("settings"))
        if not check_password_hash(config.get("password_hash", ""), old_password):
            flash("Неверный текущий пароль", "error")
            return redirect(url_for("settings"))
        config["username"] = new_username
        if new_password:
            config["password_hash"] = generate_password_hash(new_password)
        config["must_change_password"] = False
        save_config(config)
        session["user"] = new_username
        flash("Настройки сохранены", "success")
        return redirect(url_for("browse"))
    return render_template(
        "settings.html",
        username=config.get("username"),
        must_change_password=config.get("must_change_password", False),
    )


# =======================================================================
# ОБРАБОТКА ОШИБОК
# =======================================================================
@app.errorhandler(413)
def too_large(e):
    if request.accept_mimetypes.accept_json:
        return jsonify({"success": False, "error": "Файл слишком большой (макс. 100 ГБ)"}), 413
    flash("Файл слишком большой (макс. 100 ГБ)", "error")
    return redirect(url_for("browse")), 413


@app.errorhandler(404)
def not_found(e):
    return render_template("error.html", code=404, message="Не найдено"), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500, message="Внутренняя ошибка сервера"), 500


# =======================================================================
# ЗАПУСК
# =======================================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  ДОМАШНЕЕ ОБЛАКО")
    print("=" * 60)
    print(f"  Локальный доступ:  http://127.0.0.1:{PORT}")
    print(f"  Доступ по сети:    http://{get_local_ip()}:{PORT}")
    print(f"  Папка хранилища:   {STORAGE_DIR}")
    print("=" * 60)
    if config.get("must_change_password"):
        print("\n  ВНИМАНИЕ: используется пароль по умолчанию (admin/admin).")
        print("      Смените его в настройках после входа!\n")
    print("  Нажмите Ctrl+C для остановки сервера.\n")

    app.run(
        host=HOST,
        port=PORT,
        debug=False,
        threaded=True,
    )
