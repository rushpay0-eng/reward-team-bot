import logging
import os
import random
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatMemberStatus, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

CHANNEL_ID = os.getenv("CHANNEL_ID", "@YourChannelUsername").strip()
GROUP_ID = os.getenv("GROUP_ID", "@YourGroupUsername").strip()

CHANNEL_LINK = os.getenv("CHANNEL_LINK", "https://t.me/YourChannelUsername").strip()
GROUP_LINK = os.getenv("GROUP_LINK", "https://t.me/YourGroupUsername").strip()
REGISTRATION_LINK = os.getenv("REGISTRATION_LINK", "https://example.com/register").strip()

DB_PATH = os.getenv("DB_PATH", "reward_bot.db")

FIRST_SCRATCH_REWARDS = [10, 12, 15, 18, 20]
SECOND_SCRATCH_REWARDS = [10, 12, 15, 18, 20]

WHEEL_REWARDS = [50, 100, 200, 300, 400, 500]
WHEEL_WEIGHTS = [85, 10, 2.5, 1.5, 0.7, 0.3]

WAITING_REGISTRATION_PROOF = "waiting_registration_proof"
WAITING_REGISTRATION_ID = "waiting_registration_id"
WAITING_NEWBIE_PROOF = "waiting_newbie_proof"
WAITING_NEWBIE_ID = "waiting_newbie_id"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT NOT NULL,
                verified INTEGER NOT NULL DEFAULT 0,
                first_scratch INTEGER NOT NULL DEFAULT 0,
                first_reward INTEGER NOT NULL DEFAULT 0,
                registration_status TEXT NOT NULL DEFAULT 'not_submitted',
                registration_id TEXT,
                registration_proof_file_id TEXT,
                second_scratch INTEGER NOT NULL DEFAULT 0,
                second_reward INTEGER NOT NULL DEFAULT 0,
                newbie_status TEXT NOT NULL DEFAULT 'not_submitted',
                newbie_id TEXT,
                newbie_proof_file_id TEXT,
                wheel_used INTEGER NOT NULL DEFAULT 0,
                wheel_reward INTEGER NOT NULL DEFAULT 0,
                balance INTEGER NOT NULL DEFAULT 0,
                state TEXT
            );

            CREATE TABLE IF NOT EXISTS admin_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                request_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                reviewed_at TEXT,
                reviewed_by INTEGER
            );
            """
        )


def ensure_user(update: Update) -> sqlite3.Row:
    user = update.effective_user
    now = datetime.now(timezone.utc).isoformat()

    with db_connection() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
            """,
            (user.id, user.username, user.first_name, now),
        )
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user.id,)
        ).fetchone()


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def update_user(user_id: int, **values) -> None:
    if not values:
        return
    columns = ", ".join(f"{key} = ?" for key in values)
    params = list(values.values()) + [user_id]
    with db_connection() as conn:
        conn.execute(f"UPDATE users SET {columns} WHERE user_id = ?", params)


def create_request(user_id: int, request_type: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with db_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO admin_requests (user_id, request_type, created_at)
            VALUES (?, ?, ?)
            """,
            (user_id, request_type, now),
        )
        return int(cursor.lastrowid)


def get_request(request_id: int) -> Optional[sqlite3.Row]:
    with db_connection() as conn:
        return conn.execute(
            "SELECT * FROM admin_requests WHERE id = ?", (request_id,)
        ).fetchone()


def review_request(request_id: int, status: str, admin_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with db_connection() as conn:
        conn.execute(
            """
            UPDATE admin_requests
            SET status = ?, reviewed_at = ?, reviewed_by = ?
            WHERE id = ?
            """,
            (status, now, admin_id, request_id),
        )


def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("📢 Join Channel", url=CHANNEL_LINK),
                InlineKeyboardButton("👥 Join Group", url=GROUP_LINK),
            ],
            [InlineKeyboardButton("✅ Verify & Unlock", callback_data="verify_join")],
            [InlineKeyboardButton("🎁 My Rewards", callback_data="my_rewards")],
        ]
    )


def registration_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("📝 Complete Registration", url=REGISTRATION_LINK)],
            [InlineKeyboardButton("📸 Upload Proof", callback_data="upload_registration")],
        ]
    )


def newbie_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("📸 Upload Proof", callback_data="upload_newbie")]]
    )


async def is_member(
    context: ContextTypes.DEFAULT_TYPE, chat_id: str, user_id: int
) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=chat_id, user_id=user_id)
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
    ensure_user(update)
    await update.message.reply_text(
        "🎉 <b>Welcome to Reward Team!</b>\n\n"
        "Official Channel aur Group join karke apna pehla Scratch Card unlock karein.\n\n"
        "🎁 Reward: ₹10–₹20",
        parse_mode=ParseMode.HTML,
        reply_markup=main_menu(),
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    update_user(update.effective_user.id, state=None)
    await update.message.reply_text(
        "Current upload process cancelled.",
        reply_markup=main_menu(),
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    ensure_user(update)

    if query.data == "verify_join":
        channel_ok = await is_member(context, CHANNEL_ID, user_id)
        group_ok = await is_member(context, GROUP_ID, user_id)

        if not channel_ok or not group_ok:
            missing = []
            if not channel_ok:
                missing.append("Channel")
            if not group_ok:
                missing.append("Group")
            await query.message.reply_text(
                "❌ Verification incomplete.\n\n"
                f"Please join: {', '.join(missing)}\n"
                "Join karne ke baad dobara Verify & Unlock par tap karein.",
                reply_markup=main_menu(),
            )
            return

        update_user(user_id, verified=1)
        user = get_user(user_id)

        if user["first_scratch"]:
            await query.message.reply_text(
                "✅ Membership already verified.\n"
                "Aap pehla Scratch Card use kar chuke hain.",
                reply_markup=registration_keyboard(),
            )
            return

        await query.message.reply_text(
            "✅ Channel aur Group membership verified!\n\n"
            "Aapka pehla Scratch Card unlock ho gaya hai. 🎁",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🎁 Scratch Now", callback_data="scratch_one")]]
            ),
        )
        return

    if query.data == "scratch_one":
        user = get_user(user_id)
        if not user["verified"]:
            await query.message.reply_text("Pehle membership verify karein.")
            return
        if user["first_scratch"]:
            await query.answer("Scratch Card already used.", show_alert=True)
            return

        reward = random.choice(FIRST_SCRATCH_REWARDS)
        update_user(
            user_id,
            first_scratch=1,
            first_reward=reward,
            balance=user["balance"] + reward,
        )
        await query.message.reply_text(
            f"🎊 <b>Congratulations!</b>\n\n"
            f"Aapne ₹{reward} ka reward jeeta hai.\n\n"
            "Agla Scratch Card unlock karne ke liye registration complete karein.",
            parse_mode=ParseMode.HTML,
            reply_markup=registration_keyboard(),
        )
        return

    if query.data == "upload_registration":
        user = get_user(user_id)
        if not user["first_scratch"]:
            await query.message.reply_text("Pehle first Scratch Card complete karein.")
            return
        update_user(user_id, state=WAITING_REGISTRATION_PROOF)
        await query.message.reply_text(
            "📸 Registration ka screenshot bhejein.\n\n"
            "Cancel karne ke liye /cancel bhejein."
        )
        return

    if query.data == "scratch_two":
        user = get_user(user_id)
        if user["registration_status"] != "approved":
            await query.message.reply_text("Registration abhi approve nahi hua hai.")
            return
        if user["second_scratch"]:
            await query.answer("Scratch Card already used.", show_alert=True)
            return

        reward = random.choice(SECOND_SCRATCH_REWARDS)
        update_user(
            user_id,
            second_scratch=1,
            second_reward=reward,
            balance=user["balance"] + reward,
        )
        await query.message.reply_text(
            f"🎊 <b>Congratulations!</b>\n\n"
            f"Aapne ₹{reward} ka doosra reward jeeta hai.\n\n"
            "✅ Complete Newbie Order\n\n"
            "Newbie Order complete karne ke baad:\n\n"
            "📸 Upload Proof\n"
            "🆔 Send Your ID",
            parse_mode=ParseMode.HTML,
            reply_markup=newbie_keyboard(),
        )
        return

    if query.data == "upload_newbie":
        user = get_user(user_id)
        if not user["second_scratch"]:
            await query.message.reply_text("Pehle second Scratch Card complete karein.")
            return
        update_user(user_id, state=WAITING_NEWBIE_PROOF)
        await query.message.reply_text(
            "📸 Newbie Order ka screenshot bhejein.\n\n"
            "Cancel karne ke liye /cancel bhejein."
        )
        return

    if query.data == "spin_wheel":
        user = get_user(user_id)
        if user["newbie_status"] != "approved":
            await query.message.reply_text("Newbie Order proof abhi approve nahi hua hai.")
            return
        if user["wheel_used"]:
            await query.answer("Lucky Wheel already used.", show_alert=True)
            return

        reward = random.choices(WHEEL_REWARDS, weights=WHEEL_WEIGHTS, k=1)[0]
        update_user(
            user_id,
            wheel_used=1,
            wheel_reward=reward,
            balance=user["balance"] + reward,
        )
        await query.message.reply_text(
            "🎡 <b>Lucky Wheel Result</b>\n\n"
            f"Congratulations! Aapne ₹{reward} jeeta hai.\n\n"
            f"💰 Total Balance: ₹{user['balance'] + reward}",
            parse_mode=ParseMode.HTML,
        )
        return

    if query.data == "my_rewards":
        user = get_user(user_id)
        await query.message.reply_text(
            "🎁 <b>My Rewards</b>\n\n"
            f"First Scratch: ₹{user['first_reward']}\n"
            f"Second Scratch: ₹{user['second_reward']}\n"
            f"Lucky Wheel: ₹{user['wheel_reward']}\n\n"
            f"💰 Total Balance: ₹{user['balance']}",
            parse_mode=ParseMode.HTML,
        )
        return

    if query.data.startswith("admin_"):
        await admin_callback(update, context)


async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update)
    user_id = update.effective_user.id
    user = get_user(user_id)
    state = user["state"]

    if state == WAITING_REGISTRATION_PROOF:
        file_id = update.message.photo[-1].file_id
        update_user(
            user_id,
            registration_proof_file_id=file_id,
            state=WAITING_REGISTRATION_ID,
        )
        await update.message.reply_text("🆔 Ab apni Registration ID bhejein.")
        return

    if state == WAITING_NEWBIE_PROOF:
        file_id = update.message.photo[-1].file_id
        update_user(
            user_id,
            newbie_proof_file_id=file_id,
            state=WAITING_NEWBIE_ID,
        )
        await update.message.reply_text("🆔 Ab apni ID bhejein.")
        return

    await update.message.reply_text(
        "Pehle Upload Proof button par tap karein.",
        reply_markup=main_menu(),
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_user(update)
    user_id = update.effective_user.id
    user = get_user(user_id)
    state = user["state"]
    text = update.message.text.strip()

    if state == WAITING_REGISTRATION_ID:
        if len(text) < 2:
            await update.message.reply_text("Valid Registration ID bhejein.")
            return

        update_user(
            user_id,
            registration_id=text,
            registration_status="pending",
            state=None,
        )
        request_id = create_request(user_id, "registration")
        await send_registration_request_to_admin(context, user_id, request_id)
        await update.message.reply_text(
            "✅ Registration proof submitted.\n\n"
            "Admin approval ke baad doosra Scratch Card unlock hoga."
        )
        return

    if state == WAITING_NEWBIE_ID:
        if len(text) < 2:
            await update.message.reply_text("Valid ID bhejein.")
            return

        update_user(
            user_id,
            newbie_id=text,
            newbie_status="pending",
            state=None,
        )
        request_id = create_request(user_id, "newbie")
        await send_newbie_request_to_admin(context, user_id, request_id)
        await update.message.reply_text(
            "✅ Newbie Order proof submitted.\n\n"
            "Admin approval ke baad Lucky Wheel unlock hoga."
        )
        return

    await update.message.reply_text(
        "Menu use karne ke liye /start bhejein.",
        reply_markup=main_menu(),
    )


async def send_registration_request_to_admin(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, request_id: int
) -> None:
    if not ADMIN_ID:
        logger.warning("ADMIN_ID is not configured.")
        return

    user = get_user(user_id)
    caption = (
        "📥 <b>New Registration Proof</b>\n\n"
        f"👤 User: {user['first_name']}\n"
        f"🔗 Username: @{user['username'] or 'Not set'}\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        f"📝 Registration ID: <code>{user['registration_id']}</code>"
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ Approve", callback_data=f"admin_reg_approve:{request_id}"
            ),
            InlineKeyboardButton(
                "❌ Reject", callback_data=f"admin_reg_reject:{request_id}"
            ),
        ]]
    )
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=user["registration_proof_file_id"],
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def send_newbie_request_to_admin(
    context: ContextTypes.DEFAULT_TYPE, user_id: int, request_id: int
) -> None:
    if not ADMIN_ID:
        logger.warning("ADMIN_ID is not configured.")
        return

    user = get_user(user_id)
    caption = (
        "📥 <b>Newbie Order Proof</b>\n\n"
        f"👤 User: {user['first_name']}\n"
        f"🔗 Username: @{user['username'] or 'Not set'}\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n"
        f"📝 Submitted ID: <code>{user['newbie_id']}</code>"
    )
    keyboard = InlineKeyboardMarkup(
        [[
            InlineKeyboardButton(
                "✅ Approve", callback_data=f"admin_newbie_approve:{request_id}"
            ),
            InlineKeyboardButton(
                "❌ Reject", callback_data=f"admin_newbie_reject:{request_id}"
            ),
        ]]
    )
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=user["newbie_proof_file_id"],
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard,
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    if query.from_user.id != ADMIN_ID:
        await query.answer("Admin only.", show_alert=True)
        return

    action, request_id_text = query.data.split(":", 1)
    request_id = int(request_id_text)
    request_row = get_request(request_id)

    if not request_row:
        await query.answer("Request not found.", show_alert=True)
        return
    if request_row["status"] != "pending":
        await query.answer("Request already reviewed.", show_alert=True)
        return

    user_id = request_row["user_id"]

    if action == "admin_reg_approve":
        review_request(request_id, "approved", ADMIN_ID)
        update_user(user_id, registration_status="approved")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✅ Registration Approved")
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Registration Approved!\n\nAapka doosra Scratch Card unlock ho gaya hai.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🎁 Scratch Again", callback_data="scratch_two")]]
            ),
        )

    elif action == "admin_reg_reject":
        review_request(request_id, "rejected", ADMIN_ID)
        update_user(user_id, registration_status="rejected")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Registration Rejected")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Registration proof rejected.\n\nDobara Upload Proof button se correct proof submit karein.",
            reply_markup=registration_keyboard(),
        )

    elif action == "admin_newbie_approve":
        review_request(request_id, "approved", ADMIN_ID)
        update_user(user_id, newbie_status="approved")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✅ Newbie Order Approved")
        await context.bot.send_message(
            chat_id=user_id,
            text="✅ Newbie Order Approved!\n\nAapka Lucky Wheel unlock ho gaya hai. 🎡",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🎡 Spin Lucky Wheel", callback_data="spin_wheel")]]
            ),
        )

    elif action == "admin_newbie_reject":
        review_request(request_id, "rejected", ADMIN_ID)
        update_user(user_id, newbie_status="rejected")
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("❌ Newbie Order Rejected")
        await context.bot.send_message(
            chat_id=user_id,
            text="❌ Newbie Order proof rejected.\n\nDobara correct screenshot aur ID submit karein.",
            reply_markup=newbie_keyboard(),
        )


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_user.id != ADMIN_ID:
        return

    with db_connection() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        verified_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE verified = 1"
        ).fetchone()[0]
        pending_reg = conn.execute(
            "SELECT COUNT(*) FROM users WHERE registration_status = 'pending'"
        ).fetchone()[0]
        pending_newbie = conn.execute(
            "SELECT COUNT(*) FROM users WHERE newbie_status = 'pending'"
        ).fetchone()[0]
        total_balance = conn.execute(
            "SELECT COALESCE(SUM(balance), 0) FROM users"
        ).fetchone()[0]

    await update.message.reply_text(
        "📊 <b>Admin Statistics</b>\n\n"
        f"Total Users: {total_users}\n"
        f"Verified Users: {verified_users}\n"
        f"Pending Registration: {pending_reg}\n"
        f"Pending Newbie Orders: {pending_newbie}\n"
        f"Total Reward Balance: ₹{total_balance}",
        parse_mode=ParseMode.HTML,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN environment variable is missing.")
    if ADMIN_ID == 0:
        raise RuntimeError("ADMIN_ID environment variable is missing.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    app.add_error_handler(error_handler)

    logger.info("Reward Team Bot started.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
