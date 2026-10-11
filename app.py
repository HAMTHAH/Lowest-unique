import os, sqlite3, secrets
from datetime import datetime, timezone
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, abort, send_from_directory
from werkzeug.utils import secure_filename

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "demo.sqlite3")
UPLOAD_DIR = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET", "change-this-demo-secret")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")
# Intentionally disabled: this demo must not accept or represent real-money entries.
REAL_MONEY_ENABLED = False
ENTRY_PRICE_ETB = 25
MIN_NUMBER, MAX_NUMBER = 1, 1000
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE NOT NULL,
            player_name TEXT NOT NULL,
            telegram_username TEXT DEFAULT '',
            number INTEGER NOT NULL,
            payment_method TEXT NOT NULL,
            transaction_ref TEXT NOT NULL,
            screenshot TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            admin_note TEXT DEFAULT ''
        )""")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_transaction_ref ON entries(transaction_ref COLLATE NOCASE)")

init_db()

def admin_required(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return fn(*args, **kwargs)
    return wrapped

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route("/")
def home():
    with db() as c:
        recent = c.execute("SELECT code, player_name, number, status, created_at FROM entries ORDER BY id DESC LIMIT 10").fetchall()
        approved = c.execute("SELECT COUNT(*) AS n FROM entries WHERE status='approved'").fetchone()["n"]
        pending = c.execute("SELECT COUNT(*) AS n FROM entries WHERE status='pending'").fetchone()["n"]
    return render_template("index.html", recent=recent, approved=approved, pending=pending,
                           price=ENTRY_PRICE_ETB, real_money_enabled=REAL_MONEY_ENABLED)

@app.route("/submit", methods=["POST"])
def submit():
    # Demonstration-only: never confirms or collects a real payment.
    if REAL_MONEY_ENABLED:
        abort(503)
    player_name = request.form.get("player_name", "").strip()[:80]
    username = request.form.get("telegram_username", "").strip().lstrip("@")[:80]
    method = request.form.get("payment_method", "")
    ref = request.form.get("transaction_ref", "").strip()[:100]
    number_raw = request.form.get("number", "")
    proof = request.files.get("screenshot")
    if not player_name or method not in {"Telebirr", "CBE transfer"} or not ref:
        flash("Please complete all required fields.", "error")
        return redirect(url_for("home"))
    try:
        number = int(number_raw)
        if not MIN_NUMBER <= number <= MAX_NUMBER:
            raise ValueError()
    except ValueError:
        flash("Choose a whole number from 1 to 1,000.", "error")
        return redirect(url_for("home"))
    if not proof or not proof.filename or not allowed_file(proof.filename):
        flash("Upload a PNG, JPG, JPEG, or WEBP screenshot.", "error")
        return redirect(url_for("home"))
    # Screenshots are treated as unverified evidence in this demo.
    safe_name = secure_filename(proof.filename)
    stored_name = secrets.token_hex(12) + "_" + safe_name
    proof.save(os.path.join(UPLOAD_DIR, stored_name))
    code = "DEMO-" + secrets.token_hex(4).upper()
    try:
        with db() as c:
            c.execute("""INSERT INTO entries
                (code, player_name, telegram_username, number, payment_method, transaction_ref, screenshot, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
                (code, player_name, username, number, method, ref, stored_name, datetime.now(timezone.utc).isoformat()))
    except sqlite3.IntegrityError:
        try:
            os.remove(os.path.join(UPLOAD_DIR, stored_name))
        except OSError:
            pass
        flash("That transaction reference has already been submitted. Duplicate references are blocked.", "error")
        return redirect(url_for("home"))
    flash(f"Demo submission received: {code}. Status: pending admin review. No real payment was processed.", "success")
    return redirect(url_for("home"))

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["admin"] = True
            return redirect(url_for("admin"))
        flash("Incorrect admin password.", "error")
    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("home"))

@app.route("/admin")
@admin_required
def admin():
    with db() as c:
        entries = c.execute("SELECT * FROM entries ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, id DESC").fetchall()
    return render_template("admin.html", entries=entries, real_money_enabled=REAL_MONEY_ENABLED)

@app.route("/admin/review/<int:entry_id>", methods=["POST"])
@admin_required
def review(entry_id):
    action = request.form.get("action")
    note = request.form.get("note", "").strip()[:250]
    if action not in {"approve", "reject"}:
        abort(400)
    with db() as c:
        row = c.execute("SELECT status FROM entries WHERE id=?", (entry_id,)).fetchone()
        if not row:
            abort(404)
        # In demo, approve means "admin reviewed demo evidence", not payment confirmation.
        new_status = "approved" if action == "approve" else "rejected"
        c.execute("UPDATE entries SET status=?, admin_note=? WHERE id=?", (new_status, note, entry_id))
    flash("Demo entry review updated. This does not confirm a real payment.", "success")
    return redirect(url_for("admin"))

@app.route("/admin/proof/<path:filename>")
@admin_required
def proof(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/results")
def results():
    with db() as c:
        rows = c.execute("SELECT number, COUNT(*) AS n FROM entries WHERE status='approved' GROUP BY number ORDER BY number").fetchall()
        total = c.execute("SELECT COUNT(*) AS n FROM entries WHERE status='approved'").fetchone()["n"]
    unique_numbers = [r["number"] for r in rows if r["n"] == 1]
    winner = min(unique_numbers) if unique_numbers else None
    return render_template("results.html", winner=winner, total=total, rows=rows)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
