import os
import hmac
import hashlib
import json
import sqlite3
import secrets
from datetime import datetime, timezone
from functools import wraps
from urllib.parse import parse_qsl

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    abort,
    send_from_directory,
    jsonify,
    from werkzeug.exceptions import RequestEntityTooLarge,
)
from werkzeug.utils import secure_filename


# ============================================================
# CONFIGURATION
# ============================================================

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(APP_DIR, "demo.sqlite3"))
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)

APP_SECRET = os.environ.get("APP_SECRET")
if not APP_SECRET:
    raise RuntimeError("Set APP_SECRET in Fade Host environment variables.")

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD")
if not ADMIN_PASSWORD:
    raise RuntimeError("Set ADMIN_PASSWORD in Fade Host environment variables.")

# Add the BotFather token as BOT_TOKEN in Fade Host.
# Never put the real token directly in this file or in public GitHub code.
BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()

app.secret_key = APP_SECRET
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Demo-only settings. Real-money mode intentionally stays disabled.
REAL_MONEY_ENABLED = False
ENTRY_PRICE_ETB = 25
MIN_NUMBER = 1
MAX_NUMBER = 1000
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_TELEGRAM_AUTH_AGE_SECONDS = 3600


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                player_name TEXT NOT NULL,
                telegram_username TEXT DEFAULT '',
                telegram_user_id TEXT DEFAULT '',
                number INTEGER NOT NULL,
                payment_method TEXT NOT NULL,
                transaction_ref TEXT NOT NULL,
                screenshot TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                admin_note TEXT DEFAULT ''
            )
        """)

        # Keep compatibility with an older demo database.
        columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(entries)").fetchall()
        }
        if "telegram_user_id" not in columns:
            conn.execute(
                "ALTER TABLE entries ADD COLUMN telegram_user_id TEXT DEFAULT ''"
            )

        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS unique_transaction_ref
            ON entries(transaction_ref COLLATE NOCASE)
        """)


init_db()


# ============================================================
# SECURITY HELPERS
# ============================================================

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def validate_telegram_init_data(init_data):
    """Validate Telegram Mini App initData before trusting its user fields."""
    if not BOT_TOKEN or not init_data:
        return None

    try:
        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
        )

        # Reject duplicate keys to avoid ambiguous validation.
        if len({key for key, _ in pairs}) != len(pairs):
            return None

        parsed = dict(pairs)
        received_hash = parsed.pop("hash", None)

        if not received_hash or len(received_hash) != 64:
            return None

        data_check_string = "\n".join(
            f"{key}={value}" for key, value in sorted(parsed.items())
        )

        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode("utf-8"),
            hashlib.sha256,
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(calculated_hash, received_hash):
            return None

        auth_date = int(parsed.get("auth_date", "0"))
        now = int(datetime.now(timezone.utc).timestamp())

        if auth_date > now + 30:
            return None
        if now - auth_date > MAX_TELEGRAM_AUTH_AGE_SECONDS:
            return None

        user_data = parsed.get("user")
        if not user_data:
            return None

        user = json.loads(user_data)
        if not isinstance(user, dict) or not user.get("id"):
            return None

        return {
            "id": str(user["id"]),
            "first_name": str(user.get("first_name", ""))[:80],
            "username": str(user.get("username", ""))[:80],
        }

    except (ValueError, TypeError, json.JSONDecodeError):
        return None


# ============================================================
# HOME PAGE
# ============================================================

@app.route("/")
def home():
    with db() as conn:
        recent = conn.execute("""
            SELECT code, player_name, number, status, created_at
            FROM entries
            ORDER BY id DESC
            LIMIT 10
        """).fetchall()

        approved = conn.execute("""
            SELECT COUNT(*) AS n FROM entries WHERE status = 'approved'
        """).fetchone()["n"]

        pending = conn.execute("""
            SELECT COUNT(*) AS n FROM entries WHERE status = 'pending'
        """).fetchone()["n"]

    return render_template(
        "index.html",
        recent=recent,
        approved=approved,
        pending=pending,
        price=ENTRY_PRICE_ETB,
        real_money_enabled=REAL_MONEY_ENABLED,
    )


# ============================================================
# TELEGRAM MINI APP AUTHENTICATION
# ============================================================

@app.route("/api/telegram-auth", methods=["POST"])
def telegram_auth():
    if not BOT_TOKEN:
        return jsonify({
            "ok": False,
            "error": "Telegram authentication is not configured.",
        }), 503

    body = request.get_json(silent=True) or {}
    init_data = body.get("initData", "")

    if not isinstance(init_data, str) or len(init_data) > 10000:
        return jsonify({
            "ok": False,
            "error": "Invalid Telegram authentication data.",
        }), 400

    user = validate_telegram_init_data(init_data)
    if not user:
        return jsonify({
            "ok": False,
            "error": "Telegram authentication failed. Reopen the Mini App.",
        }), 401

    session.clear()
    session["telegram_user_id"] = user["id"]
    session["telegram_username"] = user["username"]
    session["telegram_first_name"] = user["first_name"]

    return jsonify({
        "ok": True,
        "user": user,
    })


@app.route("/api/telegram-status")
def telegram_status():
    user_id = session.get("telegram_user_id")
    return jsonify({
        "authenticated": bool(user_id),
        "user": {
            "id": user_id,
            "username": session.get("telegram_username", ""),
            "first_name": session.get("telegram_first_name", ""),
        } if user_id else None,
    })


# ============================================================
# DEMO ENTRY SUBMISSION
# ============================================================

@app.route("/submit", methods=["POST"])
def submit():
    if REAL_MONEY_ENABLED:
        abort(503)

    player_name = request.form.get("player_name", "").strip()[:80]
    username = (
        request.form.get("telegram_username", "")
        .strip()
        .lstrip("@")[:80]
    )
    method = request.form.get("payment_method", "")
    ref = request.form.get("transaction_ref", "").strip()[:100]
    number_raw = request.form.get("number", "")
    proof = request.files.get("screenshot")

    # The ID is accepted only from the verified Telegram session.
    telegram_user_id = session.get("telegram_user_id", "")

    if not player_name or method not in {"Telebirr", "CBE transfer"} or not ref:
        flash("Please complete all required fields.", "error")
        return redirect(url_for("home"))

    try:
        number = int(number_raw)
        if not MIN_NUMBER <= number <= MAX_NUMBER:
            raise ValueError
    except (ValueError, TypeError):
        flash("Choose a whole number from 1 to 1,000.", "error")
        return redirect(url_for("home"))

    if not proof or not proof.filename or not allowed_file(proof.filename):
        flash("Upload a PNG, JPG, JPEG, or WEBP screenshot.", "error")
        return redirect(url_for("home"))

    safe_name = secure_filename(proof.filename)
    if not safe_name:
        flash("Invalid screenshot filename.", "error")
        return redirect(url_for("home"))

    stored_name = secrets.token_hex(12) + "_" + safe_name
    proof_path = os.path.join(UPLOAD_DIR, stored_name)
    proof.save(proof_path)

    code = "DEMO-" + secrets.token_hex(4).upper()

    try:
        with db() as conn:
            conn.execute("""
                INSERT INTO entries (
                    code, player_name, telegram_username, telegram_user_id,
                    number, payment_method, transaction_ref, screenshot,
                    status, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                code,
                player_name,
                username,
                telegram_user_id,
                number,
                method,
                ref,
                stored_name,
                datetime.now(timezone.utc).isoformat(),
            ))
    except sqlite3.IntegrityError:
        try:
            os.remove(proof_path)
        except OSError:
            pass
        flash(
            "That transaction reference has already been submitted. "
            "Duplicate references are blocked.",
            "error",
        )
        return redirect(url_for("home"))

    flash(
        f"Demo submission received: {code}. Status: pending admin review. "
        "No real payment was processed.",
        "success",
    )
    return redirect(url_for("home"))


# ============================================================
# ADMIN LOGIN / LOGOUT
# ============================================================

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, ADMIN_PASSWORD):
            session.clear()
            session["admin"] = True
            return redirect(url_for("admin"))

        flash("Incorrect admin password.", "error")

    return render_template("admin_login.html")


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))


# ============================================================
# ADMIN REVIEW
# ============================================================

@app.route("/admin")
@admin_required
def admin():
    with db() as conn:
        entries = conn.execute("""
            SELECT * FROM entries
            ORDER BY
                CASE status
                    WHEN 'pending' THEN 0
                    ELSE 1
                END,
                id DESC
        """).fetchall()

    return render_template(
        "admin.html",
        entries=entries,
        real_money_enabled=REAL_MONEY_ENABLED,
    )


@app.route("/admin/review/<int:entry_id>", methods=["POST"])
@admin_required
def review(entry_id):
    action = request.form.get("action")
    note = request.form.get("note", "").strip()[:250]

    if action not in {"approve", "reject"}:
        abort(400)

    new_status = "approved" if action == "approve" else "rejected"

    with db() as conn:
        row = conn.execute(
            "SELECT id FROM entries WHERE id = ?",
            (entry_id,),
        ).fetchone()

        if not row:
            abort(404)

        conn.execute(
            "UPDATE entries SET status = ?, admin_note = ? WHERE id = ?",
            (new_status, note, entry_id),
        )

    flash(
        "Demo entry review updated. This does not confirm a real payment.",
        "success",
    )
    return redirect(url_for("admin"))


@app.route("/admin/proof/<path:filename>")
@admin_required
def proof(filename):
    return send_from_directory(UPLOAD_DIR, filename, as_attachment=False)


# ============================================================
# DEMO RESULTS: LOWEST UNIQUE APPROVED NUMBER WINS
# ============================================================

@app.route("/results")
def results():
    with db() as conn:
        rows = conn.execute("""
            SELECT number, COUNT(*) AS n
            FROM entries
            WHERE status = 'approved'
            GROUP BY number
            ORDER BY number
        """).fetchall()

        total = conn.execute("""
            SELECT COUNT(*) AS n
            FROM entries
            WHERE status = 'approved'
        """).fetchone()["n"]

    unique_numbers = [row["number"] for row in rows if row["n"] == 1]
    winner = min(unique_numbers) if unique_numbers else None

    return render_template(
        "results.html",
        winner=winner,
        total=total,
        rows=rows,
    )


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/health")
def health():
    return jsonify({
        "ok": True,
        "real_money_enabled": REAL_MONEY_ENABLED,
    })


@app.errorhandler(RequestEntityTooLarge)
def file_too_large(_error):
    flash("Screenshot is too large. Maximum size is 5 MB.", "error")
    return redirect(url_for("home"))


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "5000")),
        debug=False,
    )
