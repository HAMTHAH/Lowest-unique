# Lowest Unique Number — Telegram Mini App Demo

A mobile-first Flask demo for a lowest-unique-number game. It supports demo entry submission, screenshot upload, pending status, an admin approve/reject panel, duplicate transaction-reference blocking, and demo results.

## Safety / activation status

**Real-money game activation is disabled in `app.py` (`REAL_MONEY_ENABLED = False`).** Payment details are placeholders. Screenshot uploads are unverified evidence, and an admin approval only marks a demo submission as reviewed; it does not verify a real payment. Do not use this demo to accept paid entries or distribute cash prizes until applicable legal requirements and provider/account terms have been checked.

## Run locally

1. Install Python 3.10+.
2. In this folder run:
   ```bash
   python -m venv .venv
   # Windows: .venv\\Scripts\\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Set an admin password before running:
   ```bash
   # macOS/Linux
   export ADMIN_PASSWORD='your-strong-password'
   export APP_SECRET='a-long-random-secret'
   # Windows PowerShell
   $env:ADMIN_PASSWORD='your-strong-password'
   $env:APP_SECRET='a-long-random-secret'
   ```
4. Start:
   ```bash
   python app.py
   ```
5. Open `http://127.0.0.1:5000`.

Default admin password is `change-me` if you do not set `ADMIN_PASSWORD`; set a strong password before any shared deployment. For a shared demo, also set a strong `APP_SECRET`.

## Telegram Mini App connection

This package is currently a responsive web demo. To launch it inside Telegram:
1. Deploy it to an HTTPS host.
2. Create/configure a Telegram bot with BotFather and set the HTTPS URL as its Web App URL.
3. Add Telegram's Web App SDK and validate `Telegram.WebApp.initData` on the backend before trusting Telegram user identity. Never trust `initDataUnsafe` alone.
4. Add a bot start/menu button pointing at the app URL.

Official documentation: https://core.telegram.org/bots/webapps

## Current demo behavior

- Entry amount shown: 25 ETB, labelled as planned/demo only.
- Number range: 1–1000.
- Methods: Telebirr and CBE transfer are selectable labels only; no real payment is taken.
- Screenshot formats: PNG, JPG, JPEG, WEBP.
- Transaction reference uniqueness: duplicate references are blocked by a SQLite unique index.
- Admin panel: review uploaded screenshot, approve/reject, optionally add a note.
- Results: lowest number chosen by exactly one **admin-approved demo entry** wins.
- If no number is unique, the demo displays no winner.

## Before a real-money launch

Confirm the game's legal status and any licensing obligations, and confirm that the account/payment provider permits the proposed activity. Then implement server-side payment verification, secure file storage, user identity validation, audit logs, rate limiting, round locking, backups, privacy disclosures, and tested refund/no-winner rules. Do not rely on screenshots alone as proof of payment.
