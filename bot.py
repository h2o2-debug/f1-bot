"""Telegram bot for ГО «Ф1».

Функції:
- Приймає повідомлення від користувачів.
- Просить обрати категорію звернення (кнопки).
- Пересилає звернення:
  1) у робочу групу (якщо налаштована)
  2) у особисті повідомлення співробітникам (список керується командами)

Встановлення:
  pip install -U python-telegram-bot==21.6

Змінні середовища:
  TELEGRAM_BOT_TOKEN  - токен бота
  BOT_OWNER_ID        - numeric Telegram ID власника
  F1_BOT_DATA         - шлях до файлу даних (за замовчуванням bot_data.json)
  ROUTING_GROUP_ID    - дефолтний ID групи (необовʼязково; можна задати /setgroup)
"""

import os
import json
from dataclasses import dataclass, asdict
from typing import Dict, Optional, List, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== НАСТРОЙКИ ==================

DATA_FILE = os.environ.get("F1_BOT_DATA", "bot_data.json")
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))
DEFAULT_GROUP_ID = int(os.environ.get("ROUTING_GROUP_ID", "0"))

# ================== КАТЕГОРІЇ ==================

CATEGORIES: List[Tuple[str, str]] = [
    ("psy", "Психологічна підтримка"),
    ("law", "Юридична допомога"),
    ("edu", "Навчання / SkillsLab_F1"),
    ("hum", "Гуманітарна допомога"),
    ("gbv", "Насильство / Булінг"),
    ("other", "Інше"),
]

CAT_PREFIX = "cat:"  # callback_data prefix


# ================== МОДЕЛІ ==================

@dataclass
class StaffMember:
    user_id: int
    username: Optional[str] = None
    name: Optional[str] = None


# ================== УТИЛІТИ ==================

def load_data() -> dict:
    """Load persistent bot data from DATA_FILE."""
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # backward-compatible defaults
            data.setdefault("group_id", DEFAULT_GROUP_ID)
            data.setdefault("staff", {})
            return data
    return {"group_id": DEFAULT_GROUP_ID, "staff": {}}


def save_data(data: dict) -> None:
    """Save persistent bot data to DATA_FILE."""
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


def category_label_by_key(key: str) -> Optional[str]:
    for k, lbl in CATEGORIES:
        if k == key:
            return lbl
    return None


def categories_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text=label, callback_data=f"{CAT_PREFIX}{key}")]
        for key, label in CATEGORIES
    ]
    return InlineKeyboardMarkup(keyboard)


# ================== КОМАНДИ ==================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Show category picker each time for simplicity
    await update.message.reply_text(
        "Вітаю! Я офіційний бот ГО «Ф1».\n\n"
        "Оберіть тему звернення нижче (можна змінити будь-коли командою /category).",
        reply_markup=categories_keyboard(),
    )


async def cmd_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Оберіть тему звернення:",
        reply_markup=categories_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Доступні команди:\n"
        "/category - обрати/змінити категорію звернення\n"
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


# ================== КАТЕГОРІЇ: CALLBACK ==================

async def on_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return

    data = q.data or ""
    if not data.startswith(CAT_PREFIX):
        return

    key = data[len(CAT_PREFIX):].strip()
    label = category_label_by_key(key)
    await q.answer()

    if not label:
        return await q.edit_message_text("Категорія не знайдена. Спробуйте /category.")

    # store choice per user in memory (context.user_data)
    context.user_data["category_key"] = key
    context.user_data["category_label"] = label

    await q.edit_message_text(
        f"Обрано: {label}\n\nТепер напишіть повідомлення - я передам його команді."
    )


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

    cat_label = context.user_data.get("category_label")
    if cat_label:
        header = f"🟦 Нове звернення [{cat_label}]\n{from_line}"
    else:
        header = f"🟦 Нове звернення [Без категорії]\n{from_line}"

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
    app.add_handler(CommandHandler("category", cmd_category))

    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CommandHandler("addstaff", cmd_addstaff))
    app.add_handler(CommandHandler("removestaff", cmd_removestaff))

    app.add_handler(CallbackQueryHandler(on_category_pick, pattern=r"^cat:"))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_incoming))

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
