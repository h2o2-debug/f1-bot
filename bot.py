"""
Telegram bot for ГО «Ф1»
Пересылает все входящие сообщения:
1) в рабочую группу
2) в личные сообщения сотрудникам

pip install -U python-telegram-bot==21.6
"""

import os
import json
from dataclasses import dataclass, asdict
from typing import Dict, Optional

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================

DATA_FILE = os.environ.get("F1_BOT_DATA", "bot_data.json")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))
DEFAULT_GROUP_ID = int(os.environ.get("ROUTING_GROUP_ID", "0"))

# ================== МОДЕЛИ ==================

@dataclass
class StaffMember:
    user_id: int
    username: Optional[str] = None
    name: Optional[str] = None

# ================== УТИЛІТИ ==================

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"group_id": DEFAULT_GROUP_ID, "staff": {}}

def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_owner(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)

def parse_int(value: str):
    try:
        return int(value)
    except Exception:
        return None

def format_user_line(member: StaffMember) -> str:
    u = f"@{member.username}" if member.username else ""
    n = member.name or ""
    extra = " ".join(x for x in [u, n] if x).strip()
    return f"- `{member.user_id}` {extra}".strip()

# ================== КОМАНДИ ==================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Вітаю! Я офіційний бот ГО «Ф1».\n\n"
        "Напишіть мені повідомлення - я передам його команді.\n"
        "Команди: /help"
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Доступні команди:\n"
        "/staff - список співробітників\n"
        "/addstaff <user_id> [@username] [Ім'я] - додати співробітника (тільки власник)\n"
        "/removestaff <user_id> - видалити співробітника (тільки власник)\n"
        "/setgroup <group_id> - встановити групу для пересилки (тільки власник)\n\n"
        "⚠️ Співробітник має натиснути /start боту, "
        "інакше Telegram не дозволить писати йому в особисті."
    )
    await update.message.reply_text(text)

async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("⛔ Немає доступу.")

    if not context.args:
        return await update.message.reply_text(
            "Використання: /setgroup <group_id>\n"
            "Наприклад: -1001234567890"
        )

    gid = parse_int(context.args[0])
    if gid is None:
        return await update.message.reply_text("group_id має бути числом.")

    data = load_data()
    data["group_id"] = gid
    save_data(data)

    await update.message.reply_text(
        f"✅ Робочу групу встановлено: `{gid}`",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    staff: Dict[str, dict] = data.get("staff", {})

    if not staff:
        return await update.message.reply_text("Список співробітників порожній.")

    members = [StaffMember(**v) for v in staff.values()]
    lines = "\n".join(
        format_user_line(m) for m in sorted(members, key=lambda x: x.user_id)
    )

    await update.message.reply_text(
        "👥 Співробітники:\n" + lines,
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_addstaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("⛔ Немає доступу.")

    if not context.args:
        return await update.message.reply_text(
            "Використання: /addstaff <user_id> [@username] [Ім'я]"
        )

    uid = parse_int(context.args[0])
    if uid is None:
        return await update.message.reply_text("user_id має бути числом.")

    username = None
    name = None

    if len(context.args) >= 2:
        username = context.args[1].lstrip("@")
    if len(context.args) >= 3:
        name = " ".join(context.args[2:]).strip()

    data = load_data()
    staff = data.setdefault("staff", {})
    staff[str(uid)] = asdict(
        StaffMember(user_id=uid, username=username, name=name)
    )
    save_data(data)

    await update.message.reply_text(
        f"✅ Додано співробітника: `{uid}`",
        parse_mode=ParseMode.MARKDOWN,
    )

async def cmd_removestaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("⛔ Немає доступу.")

    if not context.args:
        return await update.message.reply_text(
            "Використання: /removestaff <user_id>"
        )

    uid = parse_int(context.args[0])
    if uid is None:
        return await update.message.reply_text("user_id має бути числом.")

    data = load_data()
    staff = data.get("staff", {})

    if str(uid) in staff:
        del staff[str(uid)]
        save_data(data)
        await update.message.reply_text(
            f"🗑 Видалено: `{uid}`",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        await update.message.reply_text("Такого співробітника немає.")

# ================== ОБРОБКА ПОВІДОМЛЕНЬ ==================

async def route_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    data = load_data()
    group_id = int(data.get("group_id") or 0)
    staff_dict: Dict[str, dict] = data.get("staff", {})

    user = update.effective_user
    from_line = f"Від: {user.full_name} (id {user.id})"
    if user.username:
        from_line += f" @{user.username}"

    header = f"🟦 Нове звернення\n{from_line}"

    # 1) В робочу групу
    if group_id != 0:
        try:
            await context.bot.send_message(chat_id=group_id, text=header)
            await msg.copy(chat_id=group_id)
        except Exception:
            await msg.reply_text(
                "⚠️ Не вдалося передати повідомлення в робочу групу."
            )

    # 2) В особисті співробітникам
    for v in staff_dict.values():
        member = StaffMember(**v)
        try:
            await context.bot.send_message(chat_id=member.user_id, text=header)
            await msg.copy(chat_id=member.user_id)
        except Exception:
            pass

    await msg.reply_text("✅ Дякуємо! Повідомлення передано команді.")

# ================== ЗАПУСК ==================

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан TELEGRAM_BOT_TOKEN")
    if OWNER_ID == 0:
        raise SystemExit("❌ Не задан BOT_OWNER_ID")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CommandHandler("addstaff", cmd_addstaff))
    app.add_handler(CommandHandler("removestaff", cmd_removestaff))

    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND, route_incoming)
    )

    app.run_polling(allowed_updates=Update.ALL_TYPES,)

if __name__ == "__main__":
    main()