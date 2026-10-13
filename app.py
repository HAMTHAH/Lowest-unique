
import os
import sqlite3
import secrets
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort, jsonify
)
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash

app = Flask(__name__)

# Configuration
app.secret_key = os.environ.get("APP_SECRET") or secrets.token_hex(32)
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TELEBIRR_NUMBER = os.environ.get("TELEBIRR_NUMBER", "").strip()

ENTRY_PRICE_ETB = 25
REAL_MONEY_ENABLED = False

BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("DATABASE_PATH", str(BASE_DIR / "demo.sqlite3")))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_FOLDER", str(BASE_DIR / "uploads")))
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024


def get_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DATABASE))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                player_name TEXT NOT NULL,
                telegram_username TEXT DEFAULT '',
                number INTEGER NOT NULL,
                payment_method TEXT DEFAULT 'Telebirr',
                transaction_ref TEXT DEFAULT '',
                screenshot TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
        """)


init_db()


def admin_required(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("admin_login"))
        return func(*args, **kwargs)
    return wrapper


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


@app.get("/")
def index():
    return render_template(
        "index.html",
        price=ENTRY_PRICE_ETB,
        real_money_enabled=REAL_MONEY_ENABLED
    )


@app.get("/enter-game")
def enter_game():
    return render_template(
        "enter_game.html",
        price=ENTRY_PRICE_ETB,
        telebirr_number=TELEBIRR_NUMBER,
        real_money_enabled=REAL_MONEY_ENABLED
    )


@app.post("/submit")
def submit():
    # This version accepts demo entries only.
    if REAL_MONEY_ENABLED:
        abort(503, description="Real-money entries are not enabled.")

    player_name = request.form.get("player_name", "").strip()
    telegram_username = request.form.get("telegram_username", "").strip()
    number_text = request.form.get("number", "").strip()
    payment_method = request.form.get("payment_method", "").strip()
    transaction_ref = request.form.get("transaction_ref", "").strip()
    screenshot = request.files.get("screenshot")

    if not player_name or len(player_name) > 100:
        flash("Enter a valid player name.")
        return redirect(url_for("enter_game"))

    try:
        number = int(number_text)
        if not 1 <= number <= 1000:
            raise ValueError
    except (ValueError, TypeError):
        flash("Choose a number from 1 to 1000.")
        return redirect(url_for("enter_game"))

    if payment_method != "Telebirr":
        flash("Select Telebirr.")
        return redirect(url_for("enter_game"))

    saved_filename = ""

    if screenshot and screenshot.filename:
        if not allowed_file(screenshot.filename):
            flash("Screenshot must be PNG, JPG, JPEG, or WEBP.")
            return redirect(url_for("enter_game"))

        saved_filename = (
            secrets.token_hex(16) + "_" +
            secure_filename(screenshot.filename)
        )
        screenshot.save(str(UPLOAD_DIR / saved_filename))

    with get_db() as db:
        db.execute("""
            INSERT INTO entries (
                player_name, telegram_username, number,
                payment_method, transaction_ref, screenshot,
                status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'demo', ?)
        """, (
            player_name,
            telegram_username[:100],
            number,
            "Telebirr",
            transaction_ref[:150],
            saved_filename,
            datetime.now(timezone.utc).isoformat()
        ))

    flash("Demo entry submitted. No payment was processed.")
    return redirect(url_for("results"))


@app.get("/results")
def results():
    with get_db() as db:
        entries = db.execute("""
            SELECT id, player_name, number, status, created_at
            FROM entries
            WHERE status IN ('approved', 'demo')
            ORDER BY id DESC
        """).fetchall()

    # Lowest number selected by exactly one player wins.
    counts = {}
    for entry in entries:
        counts[entry["number"]] = counts.get(entry["number"], 0) + 1

    unique_numbers = [
        entry for entry in entries
        if counts[entry["number"]] == 1
    ]
    winner = min(unique_numbers, key=lambda e: e["number"]) if unique_numbers else None

    return render_template(
        "results.html",
        entries=entries,
        winner=winner,
        real_money_enabled=REAL_MONEY_ENABLED
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        supplied = request.form.get("password", "")
        if not ADMIN_PASSWORD:
            flash("Set ADMIN_PASSWORD in your hosting environment first.")
        elif secrets.compare_digest(supplied, ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))
        else:
            flash("Incorrect password.")

    return render_template("admin_login.html")


@app.get("/admin")
@admin_required
def admin():
    with get_db() as db:
        entries = db.execute("""
            SELECT * FROM entries ORDER BY id DESC
        """).fetchall()

    return render_template(
        "admin.html",
        entries=entries,
        real_money_enabled=REAL_MONEY_ENABLED
    )


@app.post("/admin/entry/<int:entry_id>/<action>")
@admin_required
def update_entry(entry_id, action):
    # Approval means demo review only; it does not confirm payment.
    if action not in {"approve", "reject"}:
        abort(400)

    new_status = "approved" if action == "approve" else "rejected"

    with get_db() as db:
        cursor = db.execute(
            "UPDATE entries SET status = ? WHERE id = ?",
            (new_status, entry_id)
        )
        if cursor.rowcount == 0:
            abort(404)

    flash("Demo entry updated.")
    return redirect(url_for("admin"))


@app.get("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "real_money_enabled": REAL_MONEY_ENABLED,
        "telebirr_configured": bool(TELEBIRR_NUMBER)
    })


@app.errorhandler(413)
def file_too_large(error):
    return "Screenshot is too large. Maximum size is 5 MB.", 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
