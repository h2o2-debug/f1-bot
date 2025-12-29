"""Telegram bot for ГО «Ф1».

Key features:
- Friendly UX: Main menu with buttons, works on re-open (no need to искать /start)
- Flow: /start -> Menu -> Почати -> Анонімно? -> Категорія -> Повідомлення
- Extra screens: Про бота, Про ГО «Ф1» (Місія / Напрями діяльності / Контакти)
- Routing: forwards user messages to active groups and active staff (from JSON files)
- Logging: appends each request to Google Sheets (optional; via sheets_logger.py)
- Config: categories/messages/working hours/texts are stored in external JSON files
- Statuses: "Взято / Очікую / Закрито" buttons under ticket header for staff/groups

Env vars:
- TELEGRAM_BOT_TOKEN (required)
- BOT_OWNER_ID (optional, for /staff /groups debug + status clicks)
- F1_BOT_DATA (optional, path to runtime data file for small state; default bot_data.json)
- F1_SHEETS_ID, F1_SHEETS_TAB, F1_GOOGLE_SA_JSON (optional, for Google Sheets logger)

Files (in repo root):
- categories.json
- bot_config.json
- info_texts.json
- staff.json
- groups.json
- sheets_logger.py
"""

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any, Dict, List, Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest


# -------------------- Logging --------------------

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("f1-bot")

# Не светим токен в логах
logging.getLogger("httpx").setLevel(logging.WARNING)


# -------------------- Paths / Env --------------------

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0") or "0")

DATA_FILE = os.environ.get("F1_BOT_DATA", "bot_data.json")

CATEGORIES_FILE = os.environ.get("F1_CATEGORIES_FILE", "categories.json")
CONFIG_FILE = os.environ.get("F1_CONFIG_FILE", "bot_config.json")
INFO_TEXTS_FILE = os.environ.get("F1_INFO_TEXTS_FILE", "info_texts.json")
STAFF_FILE = os.environ.get("F1_STAFF_FILE", "staff.json")
GROUPS_FILE = os.environ.get("F1_GROUPS_FILE", "groups.json")


# -------------------- Models --------------------

@dataclass
class StaffMember:
    user_id: int
    username: Optional[str] = None
    name: Optional[str] = None
    active: bool = True


@dataclass
class GroupTarget:
    chat_id: int
    name: Optional[str] = None
    active: bool = True


# -------------------- Helpers: file loading --------------------

def _load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning("JSON not found: %s", path)
        return default
    except Exception as e:
        logger.exception("Failed to load JSON %s: %s", path, e)
        return default


def load_categories() -> List[Dict[str, str]]:
    cats = _load_json(CATEGORIES_FILE, [])
    if isinstance(cats, list):
        return [c for c in cats if isinstance(c, dict) and c.get("key") and c.get("label")]
    return []


def load_config() -> Dict[str, Any]:
    cfg = _load_json(CONFIG_FILE, {})
    return cfg if isinstance(cfg, dict) else {}


def load_info_texts() -> Dict[str, str]:
    info = _load_json(INFO_TEXTS_FILE, {})
    return info if isinstance(info, dict) else {}


def load_staff() -> List[StaffMember]:
    data = _load_json(STAFF_FILE, {})
    out: List[StaffMember] = []
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                if not isinstance(v, dict):
                    continue
                out.append(
                    StaffMember(
                        user_id=int(k),
                        username=(v.get("username") or None),
                        name=(v.get("name") or None),
                        active=bool(v.get("active", True)),
                    )
                )
            except Exception:
                continue
    return [m for m in out if m.active]


def load_groups() -> List[GroupTarget]:
    data = _load_json(GROUPS_FILE, {})
    out: List[GroupTarget] = []
    if isinstance(data, dict):
        for k, v in data.items():
            try:
                if not isinstance(v, dict):
                    continue
                out.append(
                    GroupTarget(
                        chat_id=int(k),
                        name=(v.get("name") or None),
                        active=bool(v.get("active", True)),
                    )
                )
            except Exception:
                continue
    return [g for g in out if g.active]


def is_staff_user(user_id: int) -> bool:
    if OWNER_ID and user_id == OWNER_ID:
        return True
    return any(m.user_id == user_id for m in load_staff())


# -------------------- Helpers: runtime data --------------------

def load_runtime_data() -> Dict[str, Any]:
    return _load_json(DATA_FILE, {"counters": {"ticket": 0}})


def save_runtime_data(data: Dict[str, Any]) -> None:
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception("Failed to save runtime data: %s", e)


def next_ticket_id() -> str:
    data = load_runtime_data()
    counters = data.setdefault("counters", {})
    counters["ticket"] = int(counters.get("ticket", 0)) + 1
    save_runtime_data(data)
    year = datetime.utcnow().year
    return f"F1-{year}-{counters['ticket']:04d}"


# -------------------- Helpers: time / messages --------------------

def _parse_hhmm(value: str) -> Optional[time]:
    try:
        hh, mm = value.split(":")
        return time(int(hh), int(mm))
    except Exception:
        return None


def is_working_time(cfg: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    now = now or datetime.now()
    wh = cfg.get("working_hours") or {}
    if not isinstance(wh, dict):
        return True
    days = wh.get("days")
    start_s = wh.get("start")
    end_s = wh.get("end")
    if not isinstance(days, list) or not start_s or not end_s:
        return True

    if now.weekday() not in days:
        return False
    start_t = _parse_hhmm(start_s)
    end_t = _parse_hhmm(end_s)
    if not start_t or not end_t:
        return True
    cur = now.time()
    if start_t <= end_t:
        return start_t <= cur <= end_t
    return cur >= start_t or cur <= end_t


def get_user_reply_text(cfg: Dict[str, Any], working: bool) -> str:
    msgs = cfg.get("messages") or {}
    if not isinstance(msgs, dict):
        return "Дякуємо! Повідомлення передано команді."
    if working:
        return msgs.get("working_time_reply") or "Дякуємо! Повідомлення передано команді."
    return msgs.get("off_time_reply") or "Дякуємо! Ми відповімо у робочий час."


# -------------------- Safe edit helper --------------------

async def safe_edit(q, text: str, reply_markup=None, parse_mode=None) -> None:
    try:
        await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# -------------------- Keyboards --------------------

def kb_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Почати", callback_data="menu:start")],
            [InlineKeyboardButton("Категорії", callback_data="menu:categories")],
            [
                InlineKeyboardButton("Інформація про бота", callback_data="menu:about_bot"),
                InlineKeyboardButton("Інформація про ГО «Ф1»", callback_data="menu:about_ngo"),
            ],
        ]
    )


def kb_back_to_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Меню", callback_data="menu:home")]])


def kb_anon() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Так, анонімно", callback_data="anon:yes"),
                InlineKeyboardButton("Ні, не анонімно", callback_data="anon:no"),
            ],
            [InlineKeyboardButton("Назад", callback_data="menu:home")],
        ]
    )


def kb_ngo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Місія", callback_data="ngo:mission")],
            [InlineKeyboardButton("Напрями діяльності", callback_data="ngo:directions")],
            [InlineKeyboardButton("Контакти", callback_data="ngo:contacts")],
            [InlineKeyboardButton("Назад", callback_data="menu:home")],
        ]
    )


def kb_categories(include_info_buttons: bool = True) -> InlineKeyboardMarkup:
    cats = load_categories()
    rows: List[List[InlineKeyboardButton]] = []
    for c in cats:
        rows.append([InlineKeyboardButton(c["label"], callback_data=f"cat:{c['key']}")])

    if include_info_buttons:
        rows.append([InlineKeyboardButton("Інформація про бота", callback_data="menu:about_bot")])
        rows.append([InlineKeyboardButton("Інформація про ГО «Ф1»", callback_data="menu:about_ngo")])

    rows.append([InlineKeyboardButton("Назад", callback_data="menu:home")])
    return InlineKeyboardMarkup(rows)


def _cat_label(cat_key: str) -> str:
    for c in load_categories():
        if c.get("key") == cat_key:
            return c.get("label") or cat_key
    return cat_key


def kb_status(ticket_id: str) -> InlineKeyboardMarkup:
    # Codes: t=Взято, w=Очікую, c=Закрито
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Взято", callback_data=f"st:{ticket_id}:t"),
                InlineKeyboardButton("Очікую", callback_data=f"st:{ticket_id}:w"),
                InlineKeyboardButton("Закрито", callback_data=f"st:{ticket_id}:c"),
            ]
        ]
    )


def _status_label(code: str) -> str:
    return {"t": "Взято", "w": "Очікую", "c": "Закрито"}.get(code, code)


# -------------------- Sheets logger (optional) --------------------

try:
    from sheets_logger import append_row  # type: ignore
except Exception:
    append_row = None  # type: ignore


def log_to_sheets(row: List[Any]) -> None:
    if append_row is None:
        return
    try:
        append_row(row)
    except Exception as e:
        logger.exception("Sheets logging failed: %s", e)


# -------------------- State helpers --------------------

def reset_user_flow(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop("anon", None)
    context.user_data.pop("category", None)
    context.user_data["stage"] = "menu"


def set_stage(context: ContextTypes.DEFAULT_TYPE, stage: str) -> None:
    context.user_data["stage"] = stage


def get_stage(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(context.user_data.get("stage") or "menu")


# -------------------- Commands --------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_user_flow(context)
    info = load_info_texts()
    desc = info.get("bot_description") or "Бот ГО «Ф1». Натисніть «Почати», щоб залишити звернення."
    await update.message.reply_text(desc, reply_markup=kb_main_menu())


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_user_flow(context)
    await update.message.reply_text("Оберіть дію:", reply_markup=kb_main_menu())


async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID and update.effective_user and update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("Нема доступу.")
    members = load_staff()
    if not members:
        return await update.message.reply_text("Список співробітників порожній.")
    lines = []
    for m in sorted(members, key=lambda x: x.user_id):
        extra = " ".join([f"@{m.username}" if m.username else "", m.name or ""]).strip()
        lines.append(f"- `{m.user_id}` {extra}".strip())
    await update.message.reply_text("Співробітники:\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if OWNER_ID and update.effective_user and update.effective_user.id != OWNER_ID:
        return await update.message.reply_text("Нема доступу.")
    gs = load_groups()
    if not gs:
        return await update.message.reply_text("Список груп порожній.")
    lines = []
    for g in sorted(gs, key=lambda x: x.chat_id):
        nm = f" ({g.name})" if g.name else ""
        lines.append(f"- `{g.chat_id}`{nm}")
    await update.message.reply_text("Групи:\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# -------------------- Callback handlers --------------------

def _apply_status_to_header(header: str, status: str, who: str) -> str:
    # Remove previous status/responsible lines if exist
    lines = header.splitlines()
    filtered = []
    for ln in lines:
        if ln.startswith("Статус:") or ln.startswith("Відповідальний:"):
            continue
        filtered.append(ln)
    filtered.append(f"Статус: {status}")
    filtered.append(f"Відповідальний: {who}")
    return "\n".join(filtered)


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    data = q.data or ""
    info = load_info_texts()

    # ---- STATUS buttons ----
    if data.startswith("st:"):
        # st:<ticket>:<code>
        parts = data.split(":")
        if len(parts) != 3:
            return
        ticket_id, code = parts[1], parts[2]

        user = update.effective_user
        if not user or not is_staff_user(user.id):
            return  # silent ignore

        status = _status_label(code)
        who = user.full_name
        if user.username:
            who += f" (@{user.username})"

        new_text = _apply_status_to_header(q.message.text or "", status, who)

        await safe_edit(q, new_text, reply_markup=kb_status(ticket_id))

        # Log to sheets: STATUS change
        log_to_sheets([
            datetime.utcnow().isoformat(timespec="seconds") + "Z",
            "STATUS",
            ticket_id,
            status,
            who,
            str(user.id),
        ])
        return

    # ---- MENU / FLOW ----
    if data in ("menu:home",):
        reset_user_flow(context)
        return await safe_edit(q, "Оберіть дію:", reply_markup=kb_main_menu())

    if data == "menu:start":
        set_stage(context, "anon")
        return await safe_edit(q, "Бажаєте залишити звернення анонімно?", reply_markup=kb_anon())

    if data == "menu:categories":
        if context.user_data.get("anon") is None:
            set_stage(context, "anon_then_categories")
            return await safe_edit(q, "Бажаєте залишити звернення анонімно?", reply_markup=kb_anon())
        set_stage(context, "category")
        return await safe_edit(q, "Оберіть категорію звернення:", reply_markup=kb_categories())

    if data == "menu:about_bot":
        text = info.get("bot_description") or "Бот ГО «Ф1»."
        return await safe_edit(q, text, reply_markup=kb_back_to_menu())

    if data == "menu:about_ngo":
        return await safe_edit(q, "Інформація про ГО «Ф1». Оберіть розділ:", reply_markup=kb_ngo_menu())

    if data.startswith("ngo:"):
        key = data.split(":", 1)[1]
        mission = info.get("ngo_mission")
        directions = info.get("ngo_directions")
        contacts = info.get("ngo_contacts")
        legacy = info.get("ngo_info")

        if key == "mission":
            return await safe_edit(q, mission or legacy or "Місія ГО «Ф1».", reply_markup=kb_ngo_menu())
        if key == "directions":
            return await safe_edit(q, directions or legacy or "Напрями діяльності ГО «Ф1».", reply_markup=kb_ngo_menu())
        if key == "contacts":
            return await safe_edit(q, contacts or legacy or "Контакти ГО «Ф1».", reply_markup=kb_ngo_menu())

    if data.startswith("anon:"):
        anon = data.split(":", 1)[1] == "yes"
        context.user_data["anon"] = anon
        set_stage(context, "category")
        return await safe_edit(q, "Оберіть категорію звернення:", reply_markup=kb_categories())

    if data.startswith("cat:"):
        cat_key = data.split(":", 1)[1]
        context.user_data["category"] = cat_key
        set_stage(context, "await_message")
        cat_label = _cat_label(cat_key)

        return await safe_edit(
            q,
            f"Категорія обрана: *{cat_label}*\n\nНапишіть, будь ласка, ваше повідомлення одним текстом.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Змінити категорію", callback_data="menu:categories")],
                    [InlineKeyboardButton("Меню", callback_data="menu:home")],
                ]
            ),
        )

    return await safe_edit(q, "Оберіть дію:", reply_markup=kb_main_menu())


# -------------------- Message routing --------------------

def _header_for_message(update: Update, ticket_id: str, cat_key: str, anon: bool) -> str:
    user = update.effective_user
    cat_label = _cat_label(cat_key)

    header_lines = [
        "Нове звернення (ГО «Ф1»)",
        f"ID: {ticket_id}",
        f"Категорія: {cat_label}",
        f"Анонімно: {'Так' if anon else 'Ні'}",
    ]

    if not anon and user:
        line = f"Від: {user.full_name} (id {user.id})"
        if user.username:
            line += f" @{user.username}"
        header_lines.append(line)

    header_lines.append(f"Час: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC")
    # Стартовый статус
    header_lines.append("Статус: Очікую")
    header_lines.append("Відповідальний: -")
    return "\n".join(header_lines)


async def route_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    stage = get_stage(context)
    if stage == "menu" and msg.text and msg.text.strip() not in ("/start", "/menu"):
        await msg.reply_text("Оберіть дію в меню нижче:", reply_markup=kb_main_menu())
        return

    cat_key = context.user_data.get("category")
    anon = bool(context.user_data.get("anon", False))

    if get_stage(context) != "await_message" or not cat_key:
        await msg.reply_text("Щоб залишити звернення, натисніть «Почати» і оберіть категорію.", reply_markup=kb_main_menu())
        reset_user_flow(context)
        return

    ticket_id = next_ticket_id()
    header = _header_for_message(update, ticket_id, str(cat_key), anon)

    # send to groups + attach status buttons to header message
    groups = load_groups()
    for g in groups:
        try:
            await context.bot.send_message(chat_id=g.chat_id, text=header, reply_markup=kb_status(ticket_id))
            await msg.copy(chat_id=g.chat_id)
        except Exception as e:
            logger.warning("Failed to forward to group %s: %s", g.chat_id, e)

    # send to staff + attach status buttons to header message
    staff = load_staff()
    for s in staff:
        try:
            await context.bot.send_message(chat_id=s.user_id, text=header, reply_markup=kb_status(ticket_id))
            await msg.copy(chat_id=s.user_id)
        except Exception as e:
            logger.warning("Failed to forward to staff %s: %s", s.user_id, e)

    # log to sheets (REQUEST)
    cfg = load_config()
    working = is_working_time(cfg)
    user = update.effective_user
    row = [
        datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "REQUEST",
        ticket_id,
        _cat_label(str(cat_key)),
        "Так" if anon else "Ні",
        "" if anon else (user.full_name if user else ""),
        "" if anon else (f"@{user.username}" if user and user.username else ""),
        "" if anon else (str(user.id) if user else ""),
        msg.text or msg.caption or "",
        "робочий час" if working else "поза робочим часом",
        "Очікую",
    ]
    log_to_sheets(row)

    reply_text = get_user_reply_text(cfg, working)
    await msg.reply_text(reply_text, reply_markup=kb_main_menu())

    reset_user_flow(context)


# -------------------- Error handler --------------------

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Unhandled error", exc_info=context.error)


# -------------------- Main --------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан TELEGRAM_BOT_TOKEN")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CommandHandler("groups", cmd_groups))

    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_incoming))

    app.add_error_handler(on_error)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
