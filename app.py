import os
import sqlite3
import secrets
from pathlib import Path
from functools import wraps
from datetime import datetime, timezone

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, abort, jsonify, send_from_directory
)
from werkzeug.utils import secure_filename

app = Flask(__name__)
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
    db = sqlite3.connect(str(DATABASE))
    db.row_factory = sqlite3.Row
    return db


def init_db():
    with get_db() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT DEFAULT '',
                player_name TEXT NOT NULL,
                telegram_username TEXT DEFAULT '',
                number INTEGER NOT NULL,
                payment_method TEXT DEFAULT 'Telebirr',
                transaction_ref TEXT DEFAULT '',
                screenshot TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                admin_note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(entries)").fetchall()
        }
        additions = {
            "code": "TEXT DEFAULT ''",
            "admin_note": "TEXT DEFAULT ''",
            "transaction_ref": "TEXT DEFAULT ''",
            "screenshot": "TEXT DEFAULT ''",
            "telegram_username": "TEXT DEFAULT ''",
            "payment_method": "TEXT DEFAULT 'Telebirr'",
            "status": "TEXT DEFAULT 'pending'",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")


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
def home():
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
    player_name = request.form.get("player_name", "").strip()
    telegram_username = request.form.get("telegram_username", "").strip().lstrip("@")
    transaction_ref = request.form.get("transaction_ref", "").strip()
    payment_method = request.form.get("payment_method", "").strip()
    number_text = request.form.get("number", "").strip()
    screenshot = request.files.get("screenshot")

    if not REAL_MONEY_ENABLED:
        flash("Demo mode is active. No payment was processed.")
        return redirect(url_for("enter_game"))

    if not player_name or len(player_name) > 100:
        flash("Enter a valid name.")
        return redirect(url_for("enter_game"))

    try:
        number = int(number_text)
        if not 1 <= number <= 1000:
            raise ValueError
    except ValueError:
        flash("Choose a number from 1 to 1000.")
        return redirect(url_for("enter_game"))

    if payment_method != "Telebirr":
        flash("Select Telebirr.")
        return redirect(url_for("enter_game"))

    if not transaction_ref:
        flash("Enter your transaction reference.")
        return redirect(url_for("enter_game"))

    if not screenshot or not screenshot.filename or not allowed_file(screenshot.filename):
        flash("Upload a valid payment screenshot.")
        return redirect(url_for("enter_game"))

    filename = secrets.token_hex(16) + "_" + secure_filename(screenshot.filename)

    with get_db() as db:
        duplicate = db.execute(
            "SELECT id FROM entries WHERE transaction_ref = ?",
            (transaction_ref,)
        ).fetchone()
        if duplicate:
            flash("That transaction reference has already been submitted.")
            return redirect(url_for("enter_game"))

        screenshot.save(str(UPLOAD_DIR / filename))
        code = "LW-" + secrets.token_hex(4).upper()
        db.execute("""
            INSERT INTO entries (
                code, player_name, telegram_username, number,
                payment_method, transaction_ref, screenshot,
                status, admin_note, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', '', ?)
        """, (
            code, player_name, telegram_username[:100], number,
            payment_method, transaction_ref[:150], filename,
            datetime.now(timezone.utc).isoformat()
        ))

    flash("Entry submitted for manual payment review.")
    return redirect(url_for("results"))


@app.get("/proof/<path:filename>")
@admin_required
def proof(filename):
    # Only an authenticated administrator can view uploaded evidence.
    return send_from_directory(UPLOAD_DIR, filename)


@app.get("/results")
def results():
    with get_db() as db:
        entries = db.execute("""
            SELECT id, code, player_name, number, status, created_at
            FROM entries
            WHERE status = 'approved'
            ORDER BY id DESC
        """).fetchall()

    counts = {}
    for entry in entries:
        counts[entry["number"]] = counts.get(entry["number"], 0) + 1

    unique_entries = [
        entry for entry in entries
        if counts[entry["number"]] == 1
    ]
    winner = min(unique_entries, key=lambda entry: entry["number"]) if unique_entries else None

    return render_template(
        "results.html",
        entries=entries,
        winner=winner,
        real_money_enabled=REAL_MONEY_ENABLED
    )


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and secrets.compare_digest(password, ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            return redirect(url_for("admin"))
        flash("Incorrect password or ADMIN_PASSWORD is not configured.")
    return render_template("admin_login.html")


@app.get("/admin")
@admin_required
def admin():
    with get_db() as db:
        entries = db.execute("SELECT * FROM entries ORDER BY id DESC").fetchall()
    return render_template(
        "admin.html",
        entries=entries,
        real_money_enabled=REAL_MONEY_ENABLED
    )


@app.post("/admin/review/<int:entry_id>")
@admin_required
def review(entry_id):
    action = request.form.get("action", "")
    note = request.form.get("note", "").strip()[:250]

    if action not in {"approve", "reject"}:
        abort(400)

    with get_db() as db:
        entry = db.execute(
            "SELECT * FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if entry is None:
            abort(404)

        # Approval is allowed only after the admin manually verifies
        # the transaction in the official Telebirr account.
        if action == "approve" and not REAL_MONEY_ENABLED:
            flash("Demo mode is active. Entries cannot be approved as paid.")
            return redirect(url_for("admin"))

        if action == "approve" and not entry["transaction_ref"]:
            flash("Cannot approve without a transaction reference.")
            return redirect(url_for("admin"))

        new_status = "approved" if action == "approve" else "rejected"
        db.execute(
            "UPDATE entries SET status = ?, admin_note = ? WHERE id = ?",
            (new_status, note, entry_id)
        )

    flash("Entry review updated.")
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
    return "Screenshot too large. Maximum allowed size is 5 MB.", 413


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")))
