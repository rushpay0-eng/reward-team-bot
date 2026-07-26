
import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from functools import wraps
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from waitress import serve

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("reward-bot")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_USERNAME = os.getenv("BOT_USERNAME", "").strip().lstrip("@")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "change-this-password").strip()
SECRET_KEY = os.getenv("SECRET_KEY", secrets.token_hex(32)).strip()
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
GROUP_ID = os.getenv("GROUP_ID", "").strip()
CHANNEL_LINK = os.getenv("CHANNEL_LINK", "").strip()
GROUP_LINK = os.getenv("GROUP_LINK", "").strip()
REGISTRATION_LINK = os.getenv("REGISTRATION_LINK", "https://example.com").strip()
SUPPORT_LINK = os.getenv("SUPPORT_LINK", "").strip()
DB_PATH = os.getenv("DB_PATH", "/data/reward_bot.db").strip()
PORT = int(os.getenv("PORT", "8080"))
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Kolkata").strip()

WAIT_REG_PROOF = "wait_reg_proof"
WAIT_REG_ID = "wait_reg_id"
WAIT_NEWBIE_PROOF = "wait_newbie_proof"
WAIT_NEWBIE_ID = "wait_newbie_id"
WAIT_UPI = "wait_upi"

app = Flask(__name__)
app.secret_key = SECRET_KEY


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utc_now().isoformat()


def local_now() -> datetime:
    try:
        return utc_now().astimezone(ZoneInfo(APP_TIMEZONE))
    except Exception:
        return utc_now()


def local_date_key() -> str:
    return local_now().date().isoformat()


def db() -> sqlite3.Connection:
    parent = os.path.dirname(DB_PATH)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


DEFAULT_SETTINGS = {
    "first_min": "10",
    "first_max": "20",
    "second_min": "10",
    "second_max": "20",
    "wheel_values": "50,100,200,300,400,500",
    "wheel_weights": "85,10,2.5,1.5,0.7,0.3",
    "minimum_withdrawal": "50",
    "daily_enabled": "1",
    "daily_min": "1",
    "daily_max": "5",
    "referral_enabled": "1",
    "referral_level1": "0.3",
    "referral_level2": "0.2",
    "referral_level3": "0.1",
    "rewards_enabled": "1",
    "registration_post_enabled": "1",
    "registration_post_title": "Complete Registration Guide",
    "registration_post_subtitle": "Read the complete information carefully before continuing.",
    "registration_post_content": "Add your complete registration instructions from the Admin Panel.",
    "registration_read_seconds": "90",
    "registration_require_scroll": "1",
    "registration_require_confirm": "1",
    "registration_button_text": "Continue Registration",
    "registration_target_link": "",
}


def init_db() -> None:
    with db() as conn:
        conn.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                state TEXT,

                first_status TEXT NOT NULL DEFAULT 'locked',
                first_reward INTEGER NOT NULL DEFAULT 0,

                registration_status TEXT NOT NULL DEFAULT 'not_submitted',
                registration_id TEXT,
                registration_proof TEXT,

                second_status TEXT NOT NULL DEFAULT 'locked',
                second_reward INTEGER NOT NULL DEFAULT 0,

                newbie_status TEXT NOT NULL DEFAULT 'not_submitted',
                newbie_id TEXT,
                newbie_proof TEXT,

                wheel_status TEXT NOT NULL DEFAULT 'locked',
                wheel_reward INTEGER NOT NULL DEFAULT 0,

                balance INTEGER NOT NULL DEFAULT 0,
                upi_id TEXT,
                last_checkin_date TEXT,
                referrer_id INTEGER,
                referred_at TEXT,
                referral_balance REAL NOT NULL DEFAULT 0,
                referral_earnings REAL NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reason TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS withdrawals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                upi_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reason TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount INTEGER NOT NULL,
                kind TEXT NOT NULL,
                reference_id TEXT,
                created_at TEXT NOT NULL,
                note TEXT,
                FOREIGN KEY(user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS referral_commissions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_user_id INTEGER NOT NULL,
                beneficiary_user_id INTEGER NOT NULL,
                level INTEGER NOT NULL,
                rate REAL NOT NULL,
                base_amount REAL NOT NULL,
                commission_amount REAL NOT NULL,
                source_kind TEXT NOT NULL,
                source_reference TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(beneficiary_user_id, level, source_kind, source_reference)
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT NOT NULL,
                details TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )

        existing_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(users)").fetchall()
        }
        migrations = {
            "referrer_id": "ALTER TABLE users ADD COLUMN referrer_id INTEGER",
            "referred_at": "ALTER TABLE users ADD COLUMN referred_at TEXT",
            "referral_balance": "ALTER TABLE users ADD COLUMN referral_balance REAL NOT NULL DEFAULT 0",
            "referral_earnings": "ALTER TABLE users ADD COLUMN referral_earnings REAL NOT NULL DEFAULT 0",
        }
        for column, statement in migrations.items():
            if column not in existing_columns:
                conn.execute(statement)

        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO NOTHING
                """,
                (key, value, now_iso()),
            )


def get_settings() -> dict:
    with db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    values = DEFAULT_SETTINGS.copy()
    values.update({row["key"]: row["value"] for row in rows})
    return values


def setting_int(name: str, default: int) -> int:
    try:
        return int(float(get_settings().get(name, str(default))))
    except ValueError:
        return default


def ensure_user(tg_user) -> sqlite3.Row:
    with db() as conn:
        conn.execute(
            """
            INSERT INTO users(user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
            """,
            (tg_user.id, tg_user.username, tg_user.first_name, now_iso()),
        )
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?", (tg_user.id,)
        ).fetchone()


def get_user(user_id: int) -> sqlite3.Row | None:
    with db() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()


def update_user(user_id: int, **values) -> None:
    if not values:
        return
    columns = ", ".join(f"{key}=?" for key in values)
    with db() as conn:
        conn.execute(
            f"UPDATE users SET {columns} WHERE user_id=?",
            [*values.values(), user_id],
        )


def log_activity(action: str, user_id: int | None = None, details: str = "") -> None:
    with db() as conn:
        conn.execute(
            "INSERT INTO activity_log(user_id, action, details, created_at) VALUES (?, ?, ?, ?)",
            (user_id, action, details, now_iso()),
        )


def add_transaction(
    conn: sqlite3.Connection,
    user_id: int,
    amount: int,
    kind: str,
    reference_id: str = "",
    note: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO transactions(user_id, amount, kind, reference_id, created_at, note)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (user_id, amount, kind, reference_id, now_iso(), note),
    )



def format_money(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def referral_link(user_id: int) -> str:
    if not BOT_USERNAME:
        return ""
    return f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"


def assign_referrer(user_id: int, referrer_id: int) -> bool:
    if user_id == referrer_id:
        return False

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT referrer_id FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()
        referrer = conn.execute(
            "SELECT user_id, blocked FROM users WHERE user_id=?",
            (referrer_id,),
        ).fetchone()

        if (
            not user
            or user["referrer_id"] is not None
            or not referrer
            or referrer["blocked"]
        ):
            conn.rollback()
            return False

        # Prevent short referral cycles.
        current = referrer_id
        for _ in range(3):
            row = conn.execute(
                "SELECT referrer_id FROM users WHERE user_id=?",
                (current,),
            ).fetchone()
            if not row or row["referrer_id"] is None:
                break
            current = row["referrer_id"]
            if current == user_id:
                conn.rollback()
                return False

        result = conn.execute(
            """
            UPDATE users
            SET referrer_id=?, referred_at=?
            WHERE user_id=? AND referrer_id IS NULL
            """,
            (referrer_id, now_iso(), user_id),
        )
        conn.commit()
        return result.rowcount == 1


def distribute_referral_commission(
    conn: sqlite3.Connection,
    source_user_id: int,
    base_amount: float,
    source_kind: str,
    source_reference: str,
) -> None:
    settings = get_settings()
    if settings.get("referral_enabled", "1") != "1":
        return

    rates = [
        float(settings.get("referral_level1", "0.3")),
        float(settings.get("referral_level2", "0.2")),
        float(settings.get("referral_level3", "0.1")),
    ]

    source = conn.execute(
        "SELECT referrer_id FROM users WHERE user_id=?",
        (source_user_id,),
    ).fetchone()
    beneficiary_id = source["referrer_id"] if source else None

    for level, rate in enumerate(rates, start=1):
        if not beneficiary_id:
            break

        beneficiary = conn.execute(
            "SELECT user_id, referrer_id, blocked FROM users WHERE user_id=?",
            (beneficiary_id,),
        ).fetchone()
        if not beneficiary:
            break

        if not beneficiary["blocked"] and rate > 0:
            commission = round(float(base_amount) * rate / 100, 2)
            if commission > 0:
                result = conn.execute(
                    """
                    INSERT OR IGNORE INTO referral_commissions(
                        source_user_id, beneficiary_user_id, level, rate,
                        base_amount, commission_amount, source_kind,
                        source_reference, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source_user_id,
                        beneficiary_id,
                        level,
                        rate,
                        base_amount,
                        commission,
                        source_kind,
                        source_reference,
                        now_iso(),
                    ),
                )
                if result.rowcount == 1:
                    conn.execute(
                        """
                        UPDATE users
                        SET referral_balance=referral_balance+?,
                            referral_earnings=referral_earnings+?
                        WHERE user_id=?
                        """,
                        (commission, commission, beneficiary_id),
                    )
                    add_transaction(
                        conn,
                        beneficiary_id,
                        commission,
                        f"referral_level_{level}",
                        source_reference,
                        f"{rate}% commission from user {source_user_id}",
                    )

        beneficiary_id = beneficiary["referrer_id"]


def referral_summary(user_id: int) -> dict:
    with db() as conn:
        direct = conn.execute(
            "SELECT COUNT(*) FROM users WHERE referrer_id=?",
            (user_id,),
        ).fetchone()[0]

        level2 = conn.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE referrer_id IN (
                SELECT user_id FROM users WHERE referrer_id=?
            )
            """,
            (user_id,),
        ).fetchone()[0]

        level3 = conn.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE referrer_id IN (
                SELECT user_id FROM users
                WHERE referrer_id IN (
                    SELECT user_id FROM users WHERE referrer_id=?
                )
            )
            """,
            (user_id,),
        ).fetchone()[0]

        earnings = conn.execute(
            """
            SELECT level, COALESCE(SUM(commission_amount), 0) AS amount
            FROM referral_commissions
            WHERE beneficiary_user_id=?
            GROUP BY level
            """,
            (user_id,),
        ).fetchall()

        recent = conn.execute(
            """
            SELECT r.*, u.first_name, u.username
            FROM referral_commissions r
            LEFT JOIN users u ON u.user_id=r.source_user_id
            WHERE r.beneficiary_user_id=?
            ORDER BY r.id DESC LIMIT 10
            """,
            (user_id,),
        ).fetchall()

    level_amounts = {1: 0.0, 2: 0.0, 3: 0.0}
    for row in earnings:
        level_amounts[row["level"]] = float(row["amount"])

    return {
        "level1_count": direct,
        "level2_count": level2,
        "level3_count": level3,
        "level1_amount": level_amounts[1],
        "level2_amount": level_amounts[2],
        "level3_amount": level_amounts[3],
        "recent": recent,
    }


def current_registration_link() -> str:
    saved_link = get_settings().get("registration_target_link", "").strip()
    return saved_link or REGISTRATION_LINK


def signed_token(user_id: int, purpose: str) -> str:
    raw = f"{user_id}:{purpose}"
    signature = hmac.new(
        SECRET_KEY.encode(), raw.encode(), hashlib.sha256
    ).hexdigest()
    return f"{user_id}.{signature}"


def verify_token(value: str, purpose: str) -> int | None:
    try:
        user_part, signature = value.split(".", 1)
        user_id = int(user_part)
        expected = signed_token(user_id, purpose).split(".", 1)[1]
        if hmac.compare_digest(signature, expected):
            return user_id
    except (ValueError, AttributeError):
        pass
    return None


def web_url(path: str, user_id: int, purpose: str) -> str:
    return f"{PUBLIC_URL}{path}?token={quote(signed_token(user_id, purpose))}"



def live_membership_verified(user_id: int) -> bool:
    """Check current Channel and Group membership directly through Telegram API."""
    if not CHANNEL_ID or not GROUP_ID:
        return False

    allowed = {"member", "administrator", "creator", "restricted"}

    for chat_id in (CHANNEL_ID, GROUP_ID):
        try:
            response = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
                params={"chat_id": chat_id, "user_id": user_id},
                timeout=12,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                return False
            status = payload.get("result", {}).get("status")
            if status not in allowed:
                return False
        except Exception as exc:
            logger.warning(
                "Live membership verification failed for user %s in %s: %s",
                user_id,
                chat_id,
                exc,
            )
            return False

    return True


def progress_info(user: sqlite3.Row) -> tuple[int, str]:
    completed = sum(
        [
            user["first_status"] == "used",
            user["second_status"] == "used",
            user["wheel_status"] == "used",
        ]
    )

    if not user["verified"]:
        current = "Join and verify membership"
    elif user["first_status"] != "used":
        current = "Open Welcome Scratch"
    elif user["registration_status"] in {"not_submitted", "rejected"}:
        current = "Complete registration"
    elif user["registration_status"] == "pending":
        current = "Registration under review"
    elif user["second_status"] != "used":
        current = "Open Registration Scratch"
    elif user["newbie_status"] in {"not_submitted", "rejected"}:
        current = "Complete Newbie Order"
    elif user["newbie_status"] == "pending":
        current = "Newbie Order under review"
    elif user["wheel_status"] != "used":
        current = "Open Lucky Wheel"
    else:
        current = "All reward steps completed"

    return completed, current


def dashboard_text(user: sqlite3.Row) -> str:
    settings = get_settings()
    completed, current = progress_info(user)
    daily_status = (
        "Claimed today"
        if user["last_checkin_date"] == local_date_key()
        else "Available"
    )
    task_balance = float(user["balance"] or 0)
    referral_balance = float(user["referral_balance"] or 0)
    referral_earnings = float(user["referral_earnings"] or 0)
    total_available = task_balance + referral_balance

    return (
        "🎁 <b>UPI TASK REWARDS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Hello {user['first_name'] or 'User'} 👋\n\n"
        f"📊 <b>Progress:</b> {completed}/3 completed\n"
        f"🎯 <b>Current Step:</b> {current}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 <b>Task Balance:</b> ₹{format_money(task_balance)}\n"
        f"👥 <b>Referral Balance:</b> ₹{format_money(referral_balance)}\n"
        f"🏆 <b>Total Referral Earnings:</b> ₹{format_money(referral_earnings)}\n"
        f"💵 <b>Total Available:</b> ₹{format_money(total_available)}\n"
        f"💳 <b>Minimum Withdrawal:</b> ₹{settings['minimum_withdrawal']}\n"
        f"📅 <b>Daily Bonus:</b> {daily_status}\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def dashboard_keyboard(user: sqlite3.Row) -> InlineKeyboardMarkup:
    rows = []

    if not user["verified"]:
        rows.extend(
            [
                [
                    InlineKeyboardButton("📢 Official Channel", url=CHANNEL_LINK),
                    InlineKeyboardButton("👥 Join Community", url=GROUP_LINK),
                ],
                [
                    InlineKeyboardButton(
                        "✅ Verify Membership", callback_data="verify"
                    )
                ],
            ]
        )
    elif user["first_status"] != "used":
        rows.append(
            [
                InlineKeyboardButton(
                    "🎁 Open Welcome Scratch",
                    web_app=WebAppInfo(
                        web_url("/scratch/1", user["user_id"], "scratch1")
                    ),
                )
            ]
        )
    elif user["registration_status"] in {"not_submitted", "rejected"}:
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        "📖 Complete Registration",
                        web_app=WebAppInfo(
                            web_url(
                                "/registration-info",
                                user["user_id"],
                                "registration_info",
                            )
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "📤 Submit Registration Proof",
                        callback_data="registration_upload",
                    )
                ],
            ]
        )
    elif user["registration_status"] == "pending":
        rows.append(
            [
                InlineKeyboardButton(
                    "⏳ Registration Under Review", callback_data="review_status"
                )
            ]
        )
    elif user["second_status"] != "used":
        rows.append(
            [
                InlineKeyboardButton(
                    "🎁 Open Registration Scratch",
                    web_app=WebAppInfo(
                        web_url("/scratch/2", user["user_id"], "scratch2")
                    ),
                )
            ]
        )
    elif user["newbie_status"] in {"not_submitted", "rejected"}:
        rows.append(
            [
                InlineKeyboardButton(
                    "📤 Submit Newbie Order Proof",
                    callback_data="newbie_upload",
                )
            ]
        )
    elif user["newbie_status"] == "pending":
        rows.append(
            [
                InlineKeyboardButton(
                    "⏳ Newbie Order Under Review", callback_data="review_status"
                )
            ]
        )
    elif user["wheel_status"] != "used":
        rows.append(
            [
                InlineKeyboardButton(
                    "🎡 Open Lucky Wheel",
                    web_app=WebAppInfo(
                        web_url("/wheel", user["user_id"], "wheel")
                    ),
                )
            ]
        )

    settings = get_settings()
    if settings.get("daily_enabled", "1") == "1":
        daily_label = (
            "✅ Daily Bonus Claimed"
            if user["last_checkin_date"] == local_date_key()
            else "📅 Claim Daily Bonus"
        )
        rows.append(
            [InlineKeyboardButton(daily_label, callback_data="daily_checkin")]
        )

    rows.append(
        [InlineKeyboardButton("👥 Invite & Earn", callback_data="invite")]
    )

    rows.extend(
        [
            [
                InlineKeyboardButton(
                    "🎁 Rewards", callback_data="rewards"
                ),
                InlineKeyboardButton(
                    "💳 Withdrawal", callback_data="withdrawal"
                ),
            ],
            [
                InlineKeyboardButton(
                    "📜 Activity", callback_data="activity"
                ),
                InlineKeyboardButton(
                    "❓ Support", callback_data="support"
                ),
            ],
        ]
    )
    return InlineKeyboardMarkup(rows)


def back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="dashboard")]]
    )


async def edit_or_send(
    query,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: str | None = ParseMode.HTML,
) -> None:
    try:
        await query.edit_message_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )
    except Exception:
        await query.message.reply_text(
            text=text,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
        )


async def member_ok(
    context: ContextTypes.DEFAULT_TYPE, chat_id: str, user_id: int
) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        return member.status in {
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
            ChatMemberStatus.RESTRICTED,
        }
    except Exception as exc:
        logger.warning("Membership check failed for %s: %s", chat_id, exc)
        return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = ensure_user(update.effective_user)

    if context.args:
        payload = context.args[0].strip()
        if payload.startswith("ref_"):
            try:
                referrer_id = int(payload.split("_", 1)[1])
                if assign_referrer(user["user_id"], referrer_id):
                    log_activity(
                        "referral_joined",
                        user["user_id"],
                        f"Referrer: {referrer_id}",
                    )
                    user = get_user(user["user_id"])
            except ValueError:
                pass

    if user["blocked"]:
        await update.message.reply_text(
            "⛔ Your account is currently restricted. Please contact support."
        )
        return

    await update.message.reply_text(
        dashboard_text(user),
        parse_mode=ParseMode.HTML,
        reply_markup=dashboard_keyboard(user),
    )


async def chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    await update.message.reply_text(
        f"📛 Name: {chat.title or 'Private Chat'}\n"
        f"🆔 Chat ID: <code>{chat.id}</code>\n"
        f"📂 Type: {chat.type}",
        parse_mode=ParseMode.HTML,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update.effective_user)
    update_user(update.effective_user.id, state=None)
    user = get_user(update.effective_user.id)
    await update.message.reply_text(
        "✅ Current action cancelled.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="dashboard")]]
        ),
    )


async def callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = ensure_user(query.from_user)

    if user["blocked"]:
        await edit_or_send(
            query,
            "⛔ Your account is currently restricted.",
            back_keyboard(),
        )
        return

    if query.data == "dashboard":
        update_user(user_id, state=None)
        user = get_user(user_id)
        await edit_or_send(
            query,
            dashboard_text(user),
            dashboard_keyboard(user),
        )
        return

    if query.data == "verify":
        channel_ok = await member_ok(context, CHANNEL_ID, user_id)
        group_ok = await member_ok(context, GROUP_ID, user_id)
        if not channel_ok or not group_ok:
            await edit_or_send(
                query,
                "❌ <b>MEMBERSHIP NOT VERIFIED</b>\n\n"
                "Please join both the Official Channel and Join Community group, "
                "then tap Verify Membership again.",
                dashboard_keyboard(user),
            )
            return

        if not user["verified"]:
            update_user(user_id, verified=1, first_status="ready")
            log_activity("membership_verified", user_id)
        user = get_user(user_id)
        await edit_or_send(
            query,
            dashboard_text(user),
            dashboard_keyboard(user),
        )
        return

    if query.data == "registration_upload":
        if user["first_status"] != "used":
            await edit_or_send(
                query,
                "🔒 Complete the Welcome Scratch before submitting registration proof.",
                back_keyboard(),
            )
            return
        update_user(user_id, state=WAIT_REG_PROOF)
        await edit_or_send(
            query,
            "📤 <b>SUBMIT REGISTRATION PROOF</b>\n\n"
            "Send a clear screenshot of your completed registration.\n"
            "After the screenshot, the bot will ask for your Registration ID.",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel & Return", callback_data="dashboard")]]
            ),
        )
        return

    if query.data == "newbie_upload":
        if user["second_status"] != "used":
            await edit_or_send(
                query,
                "🔒 Complete the Registration Scratch first.",
                back_keyboard(),
            )
            return
        update_user(user_id, state=WAIT_NEWBIE_PROOF)
        await edit_or_send(
            query,
            "📤 <b>SUBMIT NEWBIE ORDER PROOF</b>\n\n"
            "Send a clear screenshot of your completed Newbie Order.\n"
            "After the screenshot, the bot will ask for your ID.",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel & Return", callback_data="dashboard")]]
            ),
        )
        return

    if query.data == "review_status":
        await edit_or_send(
            query,
            "⏳ <b>SUBMISSION UNDER REVIEW</b>\n\n"
            "Your proof is being checked by the admin.\n"
            "You will receive an automatic message after approval or rejection.",
            back_keyboard(),
        )
        return

    if query.data == "daily_checkin":
        settings = get_settings()
        if settings.get("daily_enabled", "1") != "1":
            await edit_or_send(
                query,
                "📅 Daily Check-in is currently unavailable.",
                back_keyboard(),
            )
            return

        today = local_date_key()
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            if current["last_checkin_date"] == today:
                conn.rollback()
                await edit_or_send(
                    query,
                    "✅ <b>DAILY BONUS ALREADY CLAIMED</b>\n\n"
                    "You have already claimed today's bonus.\n"
                    "Please return tomorrow for the next reward.",
                    back_keyboard(),
                )
                return

            minimum = int(settings["daily_min"])
            maximum = int(settings["daily_max"])
            reward = random.randint(minimum, maximum)
            result = conn.execute(
                """
                UPDATE users
                SET balance=balance+?, last_checkin_date=?
                WHERE user_id=? AND (last_checkin_date IS NULL OR last_checkin_date<>?)
                """,
                (reward, today, user_id, today),
            )
            if result.rowcount != 1:
                conn.rollback()
                await edit_or_send(
                    query,
                    "✅ Today's Daily Bonus has already been claimed.",
                    back_keyboard(),
                )
                return

            add_transaction(
                conn,
                user_id,
                reward,
                "daily_checkin",
                today,
                "Daily Check-in reward",
            )
            distribute_referral_commission(
                conn,
                user_id,
                reward,
                "daily_checkin",
                f"{user_id}:{today}",
            )
            conn.commit()

        log_activity("daily_checkin", user_id, f"₹{reward}")
        await edit_or_send(
            query,
            "✅ <b>DAILY CHECK-IN COMPLETE</b>\n\n"
            f"Today's bonus: <b>₹{reward}</b>\n"
            "The reward has been added to your balance.",
            back_keyboard(),
        )
        return

    if query.data == "invite":
        summary = referral_summary(user_id)
        settings = get_settings()
        link = referral_link(user_id)

        recent_lines = []
        for item in summary["recent"]:
            name = item["first_name"] or item["username"] or str(item["source_user_id"])
            recent_lines.append(
                f"• L{item['level']} · {name}: ₹{format_money(float(item['commission_amount']))}"
            )
        recent_text = "\n".join(recent_lines) if recent_lines else "No referral commission yet."

        if link:
            link_text = f"<code>{link}</code>"
            share_url = (
                "https://t.me/share/url?"
                f"url={quote(link)}&text={quote('Join using my referral link and unlock rewards!')}"
            )
            buttons = [
                [InlineKeyboardButton("📤 Share Invite Link", url=share_url)],
                [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="dashboard")],
            ]
        else:
            link_text = "BOT_USERNAME is not configured in Railway."
            buttons = [
                [InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="dashboard")]
            ]

        await edit_or_send(
            query,
            "👥 <b>INVITE & EARN</b>\n\n"
            f"<b>Your Referral Link:</b>\n{link_text}\n\n"
            f"<b>Level 1:</b> {summary['level1_count']} members · "
            f"{settings['referral_level1']}% · ₹{format_money(summary['level1_amount'])}\n"
            f"<b>Level 2:</b> {summary['level2_count']} members · "
            f"{settings['referral_level2']}% · ₹{format_money(summary['level2_amount'])}\n"
            f"<b>Level 3:</b> {summary['level3_count']} members · "
            f"{settings['referral_level3']}% · ₹{format_money(summary['level3_amount'])}\n\n"
            f"<b>Total Referral Earnings:</b> "
            f"₹{format_money(float(user['referral_earnings'] or 0))}\n\n"
            f"<b>Recent Commission:</b>\n{recent_text}",
            InlineKeyboardMarkup(buttons),
        )
        return

    if query.data == "rewards":
        user = get_user(user_id)
        first = f"₹{user['first_reward']}" if user["first_status"] == "used" else "Locked"
        second = f"₹{user['second_reward']}" if user["second_status"] == "used" else "Locked"
        wheel = f"₹{user['wheel_reward']}" if user["wheel_status"] == "used" else "Locked"
        await edit_or_send(
            query,
            "🎁 <b>REWARD SUMMARY</b>\n\n"
            f"Welcome Scratch: <b>{first}</b>\n"
            f"Registration Scratch: <b>{second}</b>\n"
            f"Lucky Wheel: <b>{wheel}</b>\n\n"
            "━━━━━━━━━━━━━━\n"
            f"Task Balance: <b>₹{user['balance']}</b>\n"
            f"Referral Balance: <b>₹{format_money(float(user['referral_balance'] or 0))}</b>\n"
            f"Minimum Withdrawal: <b>₹{setting_int('minimum_withdrawal', 50)}</b>\n"
            "━━━━━━━━━━━━━━",
            back_keyboard(),
        )
        return

    if query.data == "activity":
        with db() as conn:
            tx = conn.execute("SELECT * FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
            refs = conn.execute("""
                SELECT rc.*, u.first_name, u.username
                FROM referral_commissions rc
                LEFT JOIN users u ON u.user_id=rc.source_user_id
                WHERE rc.beneficiary_user_id=?
                ORDER BY rc.id DESC LIMIT 12
            """, (user_id,)).fetchall()
            sums = conn.execute("""
                SELECT level, COALESCE(SUM(commission_amount),0) total
                FROM referral_commissions
                WHERE beneficiary_user_id=? GROUP BY level
            """, (user_id,)).fetchall()

        normal=[]
        for item in tx:
            if item["kind"].startswith("referral_level_"):
                continue
            sign = "+" if float(item["amount"]) >= 0 else ""
            normal.append(f"• {item['kind'].replace('_',' ').title()}: {sign}₹{format_money(float(item['amount']))}")
        totals={1:0.0,2:0.0,3:0.0}
        for row in sums: totals[int(row["level"])]=float(row["total"])
        details=[]
        for item in refs:
            name=item["first_name"] or (f"@{item['username']}" if item["username"] else str(item["source_user_id"]))
            details.append(
                f"• Level {item['level']}: +₹{format_money(float(item['commission_amount']))}\n"
                f"  From: {name}\n"
                f"  Base Reward: ₹{format_money(float(item['base_amount']))}\n"
                f"  Rate: {format_money(float(item['rate']))}%\n"
                f"  Source: {item['source_kind'].replace('_',' ').title()}"
            )
        await edit_or_send(
            query,
            "📜 <b>RECENT ACTIVITY</b>\n\n"
            "<b>Rewards & Withdrawals</b>\n" + ("\n".join(normal) if normal else "No task activity yet.") +
            "\n\n━━━━━━━━━━━━━━━━━━━━\n"
            "<b>Referral Commission Summary</b>\n"
            f"Level 1: ₹{format_money(totals[1])}\n"
            f"Level 2: ₹{format_money(totals[2])}\n"
            f"Level 3: ₹{format_money(totals[3])}\n"
            f"Total: ₹{format_money(sum(totals.values()))}\n\n"
            "<b>Recent Referral Details</b>\n" + ("\n\n".join(details) if details else "No referral commission yet."),
            back_keyboard(),
        )
        return

    if query.data == "withdrawal":
        user = get_user(user_id)
        minimum = setting_int("minimum_withdrawal", 50)
        with db() as conn:
            pending = conn.execute(
                """
                SELECT * FROM withdrawals
                WHERE user_id=? AND status='pending'
                ORDER BY id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()

        if pending:
            await edit_or_send(
                query,
                "⏳ <b>WITHDRAWAL STATUS</b>\n\n"
                f"Amount: ₹{pending['amount']}\n"
                f"UPI ID: {pending['upi_id']}\n"
                "Status: Pending Review",
                back_keyboard(),
            )
            return

        total_balance = float(user["balance"]) + float(user["referral_balance"] or 0)
        if total_balance < minimum:
            needed = minimum - total_balance
            await edit_or_send(
                query,
                "💳 <b>WITHDRAWAL</b>\n\n"
                f"Available Balance: ₹{format_money(total_balance)}\n"
                f"Minimum Withdrawal: ₹{minimum}\n\n"
                f"You need ₹{needed} more to request a withdrawal.",
                back_keyboard(),
            )
            return

        update_user(user_id, state=WAIT_UPI)
        await edit_or_send(
            query,
            "💳 <b>REQUEST WITHDRAWAL</b>\n\n"
            f"Available Balance: ₹{format_money(total_balance)}\n\n"
            "Send your UPI ID in this format:\n"
            "<code>name@upi</code>",
            InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel & Return", callback_data="dashboard")]]
            ),
        )
        return

    if query.data == "support":
        buttons = [[InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="dashboard")]]
        if SUPPORT_LINK:
            buttons.insert(0, [InlineKeyboardButton("💬 Contact Support", url=SUPPORT_LINK)])
        await edit_or_send(
            query,
            "❓ <b>HELP & SUPPORT</b>\n\n"
            "• Use only the task buttons shown in your dashboard.\n"
            "• Upload clear and complete screenshots.\n"
            "• Keep your Registration ID and Newbie Order ID ready.\n"
            "• You will receive an automatic update after admin review.",
            InlineKeyboardMarkup(buttons),
        )
        return


async def photo_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = ensure_user(update.effective_user)
    user_id = update.effective_user.id
    file_id = update.message.photo[-1].file_id

    if user["state"] == WAIT_REG_PROOF:
        update_user(
            user_id,
            registration_proof=file_id,
            state=WAIT_REG_ID,
        )
        await update.message.reply_text(
            "🆔 <b>REGISTRATION ID REQUIRED</b>\n\n"
            "Now send your Registration ID.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel & Return", callback_data="dashboard")]]
            ),
        )
        return

    if user["state"] == WAIT_NEWBIE_PROOF:
        update_user(
            user_id,
            newbie_proof=file_id,
            state=WAIT_NEWBIE_ID,
        )
        await update.message.reply_text(
            "🆔 <b>NEWBIE ORDER ID REQUIRED</b>\n\n"
            "Now send your ID.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel & Return", callback_data="dashboard")]]
            ),
        )
        return

    await update.message.reply_text(
        "Please open your dashboard and select the correct Submit Proof button.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Back to Dashboard", callback_data="dashboard")]]
        ),
    )


def valid_upi(value: str) -> bool:
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9._-]{2,256}@[A-Za-z0-9.-]{2,64}",
            value,
        )
    )


async def text_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    user = ensure_user(update.effective_user)
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if user["state"] == WAIT_REG_ID:
        if len(text) < 2 or len(text) > 100:
            await update.message.reply_text("Please send a valid Registration ID.")
            return

        with db() as conn:
            conn.execute(
                """
                UPDATE users
                SET registration_id=?, registration_status='pending', state=NULL
                WHERE user_id=?
                """,
                (text, user_id),
            )
            conn.execute(
                """
                INSERT INTO reviews(user_id, kind, status, created_at)
                VALUES (?, 'registration', 'pending', ?)
                """,
                (user_id, now_iso()),
            )
        log_activity("registration_proof_submitted", user_id, text)
        await update.message.reply_text(
            "✅ <b>REGISTRATION PROOF SUBMITTED</b>\n\n"
            "Your proof is now under admin review.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    if user["state"] == WAIT_NEWBIE_ID:
        if len(text) < 2 or len(text) > 100:
            await update.message.reply_text("Please send a valid ID.")
            return

        with db() as conn:
            conn.execute(
                """
                UPDATE users
                SET newbie_id=?, newbie_status='pending', state=NULL
                WHERE user_id=?
                """,
                (text, user_id),
            )
            conn.execute(
                """
                INSERT INTO reviews(user_id, kind, status, created_at)
                VALUES (?, 'newbie', 'pending', ?)
                """,
                (user_id, now_iso()),
            )
        log_activity("newbie_proof_submitted", user_id, text)
        await update.message.reply_text(
            "✅ <b>NEWBIE ORDER PROOF SUBMITTED</b>\n\n"
            "Your proof is now under admin review.",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    if user["state"] == WAIT_UPI:
        if not valid_upi(text):
            await update.message.reply_text(
                "❌ Invalid UPI ID. Please use a format like <code>name@upi</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        minimum = setting_int("minimum_withdrawal", 50)
        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM users WHERE user_id=?", (user_id,)
            ).fetchone()
            pending = conn.execute(
                "SELECT 1 FROM withdrawals WHERE user_id=? AND status='pending'",
                (user_id,),
            ).fetchone()

            if pending:
                conn.rollback()
                await update.message.reply_text(
                    "A withdrawal request is already pending.",
                    reply_markup=back_keyboard(),
                )
                return

            total_balance = float(current["balance"]) + float(current["referral_balance"] or 0)
            if total_balance < minimum:
                conn.rollback()
                await update.message.reply_text(
                    "Your balance is below the minimum withdrawal amount.",
                    reply_markup=back_keyboard(),
                )
                return

            amount = round(total_balance, 2)
            cursor = conn.execute(
                """
                INSERT INTO withdrawals(user_id, amount, upi_id, status, created_at)
                VALUES (?, ?, ?, 'pending', ?)
                """,
                (user_id, amount, text, now_iso()),
            )
            withdrawal_id = cursor.lastrowid
            conn.execute(
                """
                UPDATE users
                SET balance=0, referral_balance=0, upi_id=?, state=NULL
                WHERE user_id=?
                """,
                (text, user_id),
            )
            add_transaction(
                conn,
                user_id,
                -amount,
                "withdrawal_request",
                str(withdrawal_id),
                "Withdrawal submitted",
            )
            conn.commit()

        log_activity("withdrawal_submitted", user_id, f"₹{amount} | {text}")
        await update.message.reply_text(
            "✅ <b>WITHDRAWAL SUBMITTED</b>\n\n"
            f"Amount: ₹{amount}\n"
            f"UPI ID: {text}\n"
            "Status: Pending Review",
            parse_mode=ParseMode.HTML,
            reply_markup=back_keyboard(),
        )
        return

    await update.message.reply_text(
        "Use the dashboard buttons to continue.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Open Dashboard", callback_data="dashboard")]]
        ),
    )


async def admin_stats(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text(
        f"Admin Panel:\n{PUBLIC_URL}/admin"
    )


def run_bot() -> None:
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("chatid", chat_id))
    application.add_handler(CommandHandler("id", chat_id))
    application.add_handler(CommandHandler("cancel", cancel))
    application.add_handler(CommandHandler("stats", admin_stats))
    application.add_handler(CallbackQueryHandler(callback_handler))
    application.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler)
    )
    logger.info("Telegram polling started")
    application.run_polling(drop_pending_updates=True)


@app.get("/")
def home():
    return "Reward Bot Pro running", 200


@app.get("/health")
def health():
    return jsonify(ok=True)


def assign_scratch_reward(user_id: int, stage: int) -> int:
    key = "first" if stage == 1 else "second"
    settings = get_settings()
    minimum = int(settings[f"{key}_min"])
    maximum = int(settings[f"{key}_max"])

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        reward_field = f"{key}_reward"
        reward = user[reward_field]
        if reward <= 0:
            reward = random.randint(minimum, maximum)
            conn.execute(
                f"UPDATE users SET {reward_field}=? WHERE user_id=?",
                (reward, user_id),
            )
        conn.commit()
    return reward


@app.get("/registration-info")
def registration_info_page():
    user_id = verify_token(
        request.args.get("token", ""),
        "registration_info",
    )
    if not user_id:
        return "Invalid or expired registration page link", 403

    user = get_user(user_id)
    if not user or user["blocked"]:
        return "User account is unavailable", 403

    if not user["verified"] or user["first_status"] != "used":
        return "Complete membership verification and Welcome Scratch first.", 403

    if user["registration_status"] not in {"not_submitted", "rejected"}:
        return "Registration information is not required at this stage.", 403

    settings = get_settings()
    if settings.get("registration_post_enabled", "1") != "1":
        return redirect(current_registration_link())

    try:
        reading_seconds = max(
            0,
            min(3600, int(settings.get("registration_read_seconds", "90"))),
        )
    except ValueError:
        reading_seconds = 90

    return render_template(
        "registration_info.html",
        title=settings.get(
            "registration_post_title",
            "Complete Registration Guide",
        ),
        subtitle=settings.get(
            "registration_post_subtitle",
            "",
        ),
        content=settings.get(
            "registration_post_content",
            "",
        ),
        reading_seconds=reading_seconds,
        require_scroll=settings.get(
            "registration_require_scroll",
            "1",
        ) == "1",
        require_confirm=settings.get(
            "registration_require_confirm",
            "1",
        ) == "1",
        button_text=settings.get(
            "registration_button_text",
            "Continue Registration",
        ),
        registration_link=current_registration_link(),
    )


@app.get("/scratch/<int:stage>")
def scratch_page(stage: int):
    if stage not in (1, 2):
        return "Invalid stage", 404

    purpose = "scratch1" if stage == 1 else "scratch2"
    user_id = verify_token(request.args.get("token", ""), purpose)
    if not user_id:
        return "Invalid link", 403

    user = get_user(user_id)
    if not user:
        return "User not found", 404

    if stage == 1:
        if (
            not user["verified"]
            or user["first_status"] == "locked"
            or not live_membership_verified(user_id)
        ):
            return "Join the Official Channel and Community before opening the Scratch Card.", 403
        status = user["first_status"]
    else:
        if (
            user["registration_status"] != "approved"
            or user["second_status"] == "locked"
        ):
            return "Scratch Card locked", 403
        status = user["second_status"]

    reward = assign_scratch_reward(user_id, stage)
    return render_template(
        "scratch.html",
        stage=stage,
        reward=reward,
        token=request.args["token"],
        already_claimed=status == "used",
    )


@app.post("/api/scratch")
def scratch_claim():
    payload = request.get_json(silent=True) or {}
    stage = int(payload.get("stage", 0))
    if stage not in (1, 2):
        return jsonify(ok=False, error="Invalid stage"), 400

    purpose = "scratch1" if stage == 1 else "scratch2"
    user_id = verify_token(payload.get("token", ""), purpose)
    if not user_id:
        return jsonify(ok=False, error="Invalid link"), 403

    key = "first" if stage == 1 else "second"
    status_field = f"{key}_status"
    reward_field = f"{key}_reward"

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()

        if stage == 1 and (
            not user["verified"] or not live_membership_verified(user_id)
        ):
            conn.rollback()
            return jsonify(
                ok=False,
                error="Join the Official Channel and Community before claiming."
            ), 403
        if stage == 2 and user["registration_status"] != "approved":
            conn.rollback()
            return jsonify(ok=False, error="Registration approval required"), 403

        if user[status_field] == "used":
            conn.rollback()
            return jsonify(
                ok=True,
                reward=user[reward_field],
                already=True,
            )

        if user[status_field] != "ready":
            conn.rollback()
            return jsonify(ok=False, error="Scratch Card locked"), 403

        reward = user[reward_field]
        if reward <= 0:
            conn.rollback()
            return jsonify(ok=False, error="Reward was not assigned"), 500

        result = conn.execute(
            f"""
            UPDATE users
            SET {status_field}='used', balance=balance+?
            WHERE user_id=? AND {status_field}='ready'
            """,
            (reward, user_id),
        )
        if result.rowcount != 1:
            conn.rollback()
            return jsonify(ok=False, error="Reward already claimed"), 409

        add_transaction(
            conn,
            user_id,
            reward,
            f"{key}_scratch",
            str(stage),
            "Scratch Card reward",
        )
        distribute_referral_commission(
            conn,
            user_id,
            reward,
            f"{key}_scratch",
            f"{user_id}:{stage}",
        )
        conn.commit()

    log_activity(f"{key}_scratch_claimed", user_id, f"₹{reward}")
    heading = (
        f"🎉 <b>₹{reward} REWARD ADDED</b>\n"
        + ("Your next challenge is now <b>Complete Registration</b>." if stage == 1
           else "Your next challenge is now <b>Complete Newbie Order</b>.")
    )
    try:
        send_refreshed_dashboard(user_id, heading)
    except Exception as exc:
        logger.warning("Unable to send refreshed scratch dashboard: %s", exc)
    return jsonify(ok=True, reward=reward, already=False)


def parse_wheel_settings() -> tuple[list[int], list[float]]:
    settings = get_settings()
    try:
        values = [
            int(float(item.strip()))
            for item in settings["wheel_values"].split(",")
            if item.strip()
        ]
        weights = [
            float(item.strip())
            for item in settings["wheel_weights"].split(",")
            if item.strip()
        ]
        if len(values) != len(weights) or not values or sum(weights) <= 0:
            raise ValueError
        return values, weights
    except ValueError:
        return [50, 100, 200, 300, 400, 500], [85, 10, 2.5, 1.5, 0.7, 0.3]


@app.get("/wheel")
def wheel_page():
    user_id = verify_token(request.args.get("token", ""), "wheel")
    if not user_id:
        return "Invalid link", 403

    user = get_user(user_id)
    if not user or user["newbie_status"] != "approved":
        return "Lucky Wheel locked", 403

    values, _ = parse_wheel_settings()
    return render_template(
        "wheel.html",
        token=request.args["token"],
        values=values,
        already_used=user["wheel_status"] == "used",
        existing_reward=user["wheel_reward"],
    )


@app.post("/api/wheel")
def wheel_spin():
    payload = request.get_json(silent=True) or {}
    user_id = verify_token(payload.get("token", ""), "wheel")
    if not user_id:
        return jsonify(ok=False, error="Invalid link"), 403

    values, weights = parse_wheel_settings()

    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()

        if user["newbie_status"] != "approved":
            conn.rollback()
            return jsonify(ok=False, error="Lucky Wheel locked"), 403

        if user["wheel_status"] == "used":
            conn.rollback()
            return jsonify(
                ok=True,
                reward=user["wheel_reward"],
                already=True,
            )

        reward = random.choices(values, weights=weights, k=1)[0]
        result = conn.execute(
            """
            UPDATE users
            SET wheel_status='used', wheel_reward=?, balance=balance+?
            WHERE user_id=? AND wheel_status='ready'
            """,
            (reward, reward, user_id),
        )
        if result.rowcount != 1:
            conn.rollback()
            return jsonify(ok=False, error="Lucky Wheel is not ready"), 409

        add_transaction(
            conn,
            user_id,
            reward,
            "lucky_wheel",
            "",
            "Lucky Wheel reward",
        )
        distribute_referral_commission(
            conn,
            user_id,
            reward,
            "lucky_wheel",
            f"{user_id}:wheel",
        )
        conn.commit()

    log_activity("lucky_wheel_claimed", user_id, f"₹{reward}")
    try:
        send_refreshed_dashboard(user_id, f"🎉 <b>₹{reward} LUCKY WHEEL REWARD ADDED</b>\nAll reward challenges are now completed.")
    except Exception as exc:
        logger.warning("Unable to send refreshed wheel dashboard: %s", exc)
    return jsonify(ok=True, reward=reward, already=False)


def send_refreshed_dashboard(user_id: int, heading: str = "✅ Dashboard Updated") -> None:
    user = get_user(user_id)
    if not user:
        return
    send_bot_message(user_id, f"{heading}\n\n{dashboard_text(user)}", dashboard_keyboard(user))


def send_bot_message(
    user_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    async def send() -> None:
        bot = Bot(BOT_TOKEN)
        await bot.initialize()
        try:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
        finally:
            await bot.shutdown()

    asyncio.run(send())


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return function(*args, **kwargs)
    return wrapper


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    error = None
    if request.method == "POST":
        if (
            hmac.compare_digest(
                request.form.get("username", ""), ADMIN_USERNAME
            )
            and hmac.compare_digest(
                request.form.get("password", ""), ADMIN_PASSWORD
            )
        ):
            session["admin"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Invalid username or password"
    return render_template("login.html", error=error)


@app.get("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.get("/admin")
@admin_required
def admin_dashboard():
    since = (utc_now() - timedelta(hours=24)).isoformat()
    with db() as conn:
        stats = {
            "new_users": conn.execute(
                "SELECT COUNT(*) FROM users WHERE joined_at>=?", (since,)
            ).fetchone()[0],
            "verified": conn.execute(
                "SELECT COUNT(*) FROM users WHERE verified=1"
            ).fetchone()[0],
            "proofs_24h": conn.execute(
                "SELECT COUNT(*) FROM reviews WHERE created_at>=?", (since,)
            ).fetchone()[0],
            "pending_proofs": conn.execute(
                "SELECT COUNT(*) FROM reviews WHERE status='pending'"
            ).fetchone()[0],
            "withdrawals_24h": conn.execute(
                "SELECT COUNT(*) FROM withdrawals WHERE created_at>=?", (since,)
            ).fetchone()[0],
            "pending_withdrawals": conn.execute(
                "SELECT COUNT(*) FROM withdrawals WHERE status='pending'"
            ).fetchone()[0],
            "paid_amount_24h": conn.execute(
                """
                SELECT COALESCE(SUM(amount),0) FROM withdrawals
                WHERE status='paid' AND reviewed_at>=?
                """,
                (since,),
            ).fetchone()[0],
            "daily_claims_24h": conn.execute(
                """
                SELECT COUNT(*) FROM transactions
                WHERE kind='daily_checkin' AND created_at>=?
                """,
                (since,),
            ).fetchone()[0],
            "daily_amount_24h": conn.execute(
                """
                SELECT COALESCE(SUM(amount),0) FROM transactions
                WHERE kind='daily_checkin' AND created_at>=?
                """,
                (since,),
            ).fetchone()[0],
            "referrals_24h": conn.execute(
                "SELECT COUNT(*) FROM users WHERE referred_at>=?",
                (since,),
            ).fetchone()[0],
            "referral_amount_24h": conn.execute(
                """
                SELECT COALESCE(SUM(commission_amount),0)
                FROM referral_commissions
                WHERE created_at>=?
                """,
                (since,),
            ).fetchone()[0],
        }

        registration_proofs = conn.execute(
            """SELECT r.*, u.first_name, u.username, u.registration_id, u.registration_proof
            FROM reviews r JOIN users u ON u.user_id=r.user_id
            WHERE r.status='pending' AND r.kind='registration' ORDER BY r.id DESC"""
        ).fetchall()
        newbie_proofs = conn.execute(
            """SELECT r.*, u.first_name, u.username, u.newbie_id, u.newbie_proof
            FROM reviews r JOIN users u ON u.user_id=r.user_id
            WHERE r.status='pending' AND r.kind='newbie' ORDER BY r.id DESC"""
        ).fetchall()

        withdrawals = conn.execute(
            """
            SELECT w.*, u.first_name, u.username
            FROM withdrawals w
            JOIN users u ON u.user_id=w.user_id
            ORDER BY w.id DESC LIMIT 100
            """
        ).fetchall()

        activities = conn.execute(
            """
            SELECT a.*, u.first_name, u.username
            FROM activity_log a
            LEFT JOIN users u ON u.user_id=a.user_id
            WHERE a.created_at>=?
            ORDER BY a.id DESC LIMIT 100
            """,
            (since,),
        ).fetchall()

        users = conn.execute(
            "SELECT * FROM users ORDER BY joined_at DESC LIMIT 100"
        ).fetchall()

    return render_template(
        "admin.html",
        stats=stats,
        registration_proofs=registration_proofs,
        newbie_proofs=newbie_proofs,
        withdrawals=withdrawals,
        activities=activities,
        users=users,
        settings=get_settings(),
    )


@app.get("/admin/proof-image/<kind>/<int:user_id>")
@admin_required
def proof_image(kind: str, user_id: int):
    if kind not in {"registration", "newbie"}:
        return "Invalid proof type", 404

    column = (
        "registration_proof" if kind == "registration" else "newbie_proof"
    )
    with db() as conn:
        row = conn.execute(
            f"SELECT {column} AS file_id FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

    if not row or not row["file_id"]:
        return "Proof image not found", 404

    try:
        metadata = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": row["file_id"]},
            timeout=15,
        )
        metadata.raise_for_status()
        file_path = metadata.json()["result"]["file_path"]
        image = requests.get(
            f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}",
            timeout=20,
        )
        image.raise_for_status()
        content_type = image.headers.get("Content-Type", "image/jpeg")
        return Response(image.content, content_type=content_type)
    except Exception as exc:
        logger.exception("Proof image download failed: %s", exc)
        return "Unable to load proof image", 502


@app.post("/admin/proof/<int:review_id>/<action>")
@admin_required
def review_proof(review_id: int, action: str):
    if action not in {"approve", "reject"}:
        return "Invalid action", 400

    reason = request.form.get("reason", "").strip()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        review = conn.execute(
            "SELECT * FROM reviews WHERE id=?", (review_id,)
        ).fetchone()
        if not review or review["status"] != "pending":
            conn.rollback()
            return redirect(url_for("admin_dashboard"))

        status = "approved" if action == "approve" else "rejected"
        conn.execute(
            """
            UPDATE reviews
            SET status=?, reviewed_at=?, reason=?
            WHERE id=?
            """,
            (status, now_iso(), reason, review_id),
        )

        if review["kind"] == "registration":
            conn.execute(
                """
                UPDATE users
                SET registration_status=?, second_status=?
                WHERE user_id=?
                """,
                (
                    status,
                    "ready" if status == "approved" else "locked",
                    review["user_id"],
                ),
            )
        else:
            conn.execute(
                """
                UPDATE users
                SET newbie_status=?, wheel_status=?
                WHERE user_id=?
                """,
                (
                    status,
                    "ready" if status == "approved" else "locked",
                    review["user_id"],
                ),
            )
        conn.commit()

    log_activity(
        f"{review['kind']}_{status}",
        review["user_id"],
        reason,
    )

    if status == "approved":
        if review["kind"] == "registration":
            text = (
                "✅ <b>REGISTRATION APPROVED</b>\n\n"
                "Your Registration Scratch is now unlocked."
            )
        else:
            text = (
                "✅ <b>NEWBIE ORDER APPROVED</b>\n\n"
                "Your Lucky Wheel is now unlocked."
            )
    else:
        text = (
            "❌ <b>PROOF REJECTED</b>\n\n"
            f"Reason: {reason or 'Invalid or incomplete proof'}"
        )

    send_bot_message(
        review["user_id"],
        text,
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Open Dashboard", callback_data="dashboard")]]
        ),
    )
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/withdrawal/<int:withdrawal_id>/<action>")
@admin_required
def review_withdrawal(withdrawal_id: int, action: str):
    if action not in {"paid", "reject"}:
        return "Invalid action", 400

    reason = request.form.get("reason", "").strip()
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        withdrawal = conn.execute(
            "SELECT * FROM withdrawals WHERE id=?", (withdrawal_id,)
        ).fetchone()
        if not withdrawal or withdrawal["status"] != "pending":
            conn.rollback()
            return redirect(url_for("admin_dashboard"))

        status = "paid" if action == "paid" else "rejected"
        conn.execute(
            """
            UPDATE withdrawals
            SET status=?, reviewed_at=?, reason=?
            WHERE id=?
            """,
            (status, now_iso(), reason, withdrawal_id),
        )

        if status == "rejected":
            conn.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (withdrawal["amount"], withdrawal["user_id"]),
            )
            add_transaction(
                conn,
                withdrawal["user_id"],
                withdrawal["amount"],
                "withdrawal_refund",
                str(withdrawal_id),
                "Rejected withdrawal returned",
            )
        conn.commit()

    log_activity(
        f"withdrawal_{status}",
        withdrawal["user_id"],
        f"₹{withdrawal['amount']} | {reason}",
    )

    if status == "paid":
        text = (
            "✅ <b>WITHDRAWAL PAID</b>\n\n"
            f"Amount: ₹{withdrawal['amount']}\n"
            "Status: Paid"
        )
    else:
        text = (
            "❌ <b>WITHDRAWAL REJECTED</b>\n\n"
            f"Amount: ₹{withdrawal['amount']}\n"
            f"Reason: {reason or 'Payment issue'}\n\n"
            "The amount has been returned to your balance."
        )

    send_bot_message(
        withdrawal["user_id"],
        text,
        InlineKeyboardMarkup(
            [[InlineKeyboardButton("⬅️ Open Dashboard", callback_data="dashboard")]]
        ),
    )
    return redirect(url_for("admin_dashboard"))


@app.post("/admin/registration-post")
@admin_required
def save_registration_post():
    title = request.form.get("registration_post_title", "").strip()
    subtitle = request.form.get("registration_post_subtitle", "").strip()
    content = request.form.get("registration_post_content", "").strip()
    button_text = request.form.get(
        "registration_button_text",
        "",
    ).strip()
    target_link = request.form.get(
        "registration_target_link",
        "",
    ).strip()
    enabled = (
        "1"
        if request.form.get("registration_post_enabled") == "on"
        else "0"
    )
    require_scroll = (
        "1"
        if request.form.get("registration_require_scroll") == "on"
        else "0"
    )
    require_confirm = (
        "1"
        if request.form.get("registration_require_confirm") == "on"
        else "0"
    )

    try:
        reading_seconds = int(
            request.form.get("registration_read_seconds", "90")
        )
        if reading_seconds < 0 or reading_seconds > 3600:
            raise ValueError
    except ValueError:
        return redirect(
            url_for(
                "admin_dashboard",
                post_error="Timer must be between 0 and 3600 seconds.",
            )
        )

    if not title or len(title) > 150:
        return redirect(
            url_for(
                "admin_dashboard",
                post_error="Post title is required and must be under 150 characters.",
            )
        )

    if len(subtitle) > 300:
        return redirect(
            url_for(
                "admin_dashboard",
                post_error="Subtitle must be under 300 characters.",
            )
        )

    if not content or len(content) > 50000:
        return redirect(
            url_for(
                "admin_dashboard",
                post_error="Post content is required and must be under 50,000 characters.",
            )
        )

    if not button_text or len(button_text) > 60:
        return redirect(
            url_for(
                "admin_dashboard",
                post_error="Button text is required and must be under 60 characters.",
            )
        )

    if target_link and not target_link.startswith(("https://", "http://")):
        return redirect(
            url_for(
                "admin_dashboard",
                post_error="Registration link must start with http:// or https://.",
            )
        )

    fields = {
        "registration_post_enabled": enabled,
        "registration_post_title": title,
        "registration_post_subtitle": subtitle,
        "registration_post_content": content,
        "registration_read_seconds": str(reading_seconds),
        "registration_require_scroll": require_scroll,
        "registration_require_confirm": require_confirm,
        "registration_button_text": button_text,
        "registration_target_link": target_link,
    }

    with db() as conn:
        for key, value in fields.items():
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, value, now_iso()),
            )

    log_activity(
        "registration_post_updated",
        None,
        f"Timer: {reading_seconds}s",
    )
    return redirect(
        url_for(
            "admin_dashboard",
            post_saved="1",
        )
    )


@app.post("/admin/settings")
@admin_required
def save_settings():
    fields = {
        "first_min": request.form.get("first_min", "").strip(),
        "first_max": request.form.get("first_max", "").strip(),
        "second_min": request.form.get("second_min", "").strip(),
        "second_max": request.form.get("second_max", "").strip(),
        "wheel_values": request.form.get("wheel_values", "").strip(),
        "wheel_weights": request.form.get("wheel_weights", "").strip(),
        "minimum_withdrawal": request.form.get("minimum_withdrawal", "").strip(),
        "daily_enabled": "1" if request.form.get("daily_enabled") == "on" else "0",
        "daily_min": request.form.get("daily_min", "").strip(),
        "daily_max": request.form.get("daily_max", "").strip(),
        "referral_enabled": "1" if request.form.get("referral_enabled") == "on" else "0",
        "referral_level1": request.form.get("referral_level1", "").strip(),
        "referral_level2": request.form.get("referral_level2", "").strip(),
        "referral_level3": request.form.get("referral_level3", "").strip(),
        "rewards_enabled": "1" if request.form.get("rewards_enabled") == "on" else "0",
    }

    try:
        first_min = int(fields["first_min"])
        first_max = int(fields["first_max"])
        second_min = int(fields["second_min"])
        second_max = int(fields["second_max"])
        minimum_withdrawal = int(fields["minimum_withdrawal"])
        daily_min = int(fields["daily_min"])
        daily_max = int(fields["daily_max"])
        referral_level1 = float(fields["referral_level1"])
        referral_level2 = float(fields["referral_level2"])
        referral_level3 = float(fields["referral_level3"])
        wheel_values = [
            int(float(item.strip()))
            for item in fields["wheel_values"].split(",")
            if item.strip()
        ]
        wheel_weights = [
            float(item.strip())
            for item in fields["wheel_weights"].split(",")
            if item.strip()
        ]

        if min(first_min, second_min, minimum_withdrawal, daily_min) < 0:
            raise ValueError("Negative amounts are not allowed")
        if first_min > first_max or second_min > second_max or daily_min > daily_max:
            raise ValueError("Minimum cannot exceed maximum")
        if not wheel_values or len(wheel_values) != len(wheel_weights):
            raise ValueError("Wheel values and weights must have the same count")
        if any(value < 0 for value in wheel_values) or any(weight < 0 for weight in wheel_weights):
            raise ValueError("Invalid wheel value or weight")
        if sum(wheel_weights) <= 0:
            raise ValueError("Wheel weights total must be greater than zero")
        if any(rate < 0 or rate > 100 for rate in [
            referral_level1, referral_level2, referral_level3
        ]):
            raise ValueError("Referral rates must be between 0 and 100")
    except ValueError as exc:
        return redirect(url_for("admin_dashboard", settings_error=str(exc)))

    with db() as conn:
        for key, value in fields.items():
            conn.execute(
                """
                INSERT INTO settings(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, value, now_iso()),
            )

    log_activity("reward_settings_updated", None)
    return redirect(url_for("admin_dashboard", settings_saved="1"))


@app.post("/admin/user/<int:user_id>/toggle-block")
@admin_required
def toggle_block(user_id: int):
    user = get_user(user_id)
    if user:
        update_user(user_id, blocked=0 if user["blocked"] else 1)
        log_activity(
            "user_unblocked" if user["blocked"] else "user_blocked",
            user_id,
        )
    return redirect(url_for("admin_dashboard"))


def run_web_server() -> None:
    logger.info("Web server starting on port %s", PORT)
    serve(app, host="0.0.0.0", port=PORT, threads=8)


if __name__ == "__main__":
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing")
    if not PUBLIC_URL.startswith("https://"):
        raise RuntimeError("PUBLIC_URL must start with https://")

    init_db()
    threading.Thread(
        target=run_web_server,
        name="reward-bot-web",
        daemon=True,
    ).start()
    run_bot()
