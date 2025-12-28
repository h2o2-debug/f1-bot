# bot.py
# F1 Support Bot - routes incoming messages to configured groups and staff,
# with category flow, anonymity option, working-hours auto replies,
# and logging to Google Sheets.

import os
import json
import uuid
from dataclasses import dataclass
from typing import Dict, Optional, List, Any, Tuple
from datetime import datetime, time
from zoneinfo import ZoneInfo

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

from sheets_logger import SheetsLogger

# ------------------- Paths / files (static config in repo) -------------------

DATA_DIR = os.environ.get("F1_DATA_DIR", ".")  # for future volumes if needed

CATEGORIES_FILE = os.environ.get("F1_BOT_CATEGORIES", "categories.json")
INFO_FILE = os.environ.get("F1_BOT_INFO", "info_texts.json")
BOT_CONFIG_FILE = os.environ.get("F1_BOT_CONFIG", "bot_config.json")

STAFF_FILE = os.environ.get("F1_BOT_STAFF", "staff.json")     # edited manually in GitHub
GROUPS_FILE = os.environ.get("F1_BOT_GROUPS", "groups.json")  # edited manually in GitHub

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0") or "0")

KYIV_TZ = ZoneInfo(os.environ.get("F1_TZ", "Europe/Kyiv"))

# ------------------- Helpers -------------------

def _load_json(path: str, default: Any) -> Any:
    # allow mounting under DATA_DIR if user passes relative paths
    p = path
    if not os.path.isabs(p):
        p = os.path.join(DATA_DIR, p)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _now_local() -> datetime:
    return datetime.now(tz=KYIV_TZ)

def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%d.%m.%Y %H:%M")

def _dow_key(dt: datetime) -> str:
    # mon..sun
    return ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][dt.weekday()]

def _parse_hhmm(s: str) -> time:
    hh, mm = s.strip().split(":")
    return time(int(hh), int(mm))

def is_working_time(cfg: dict, dt: datetime) -> bool:
    wh = (cfg or {}).get("working_hours", {}) or {}
    day = _dow_key(dt)
    ranges = wh.get(day, []) or []
    if not ranges:
        return False
    t = dt.timetz().replace(tzinfo=None)  # compare naive times
    for r in ranges:
        if not isinstance(r, list) or len(r) != 2:
            continue
        start = _parse_hhmm(r[0])
        end = _parse_hhmm(r[1])
        if start <= t <= end:
            return True
    return False

def is_owner(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == OWNER_ID

@dataclass
class StaffMember:
    user_id: int
    name: str = ""
    username: str = ""
    active: bool = True

def load_staff() -> Dict[str, StaffMember]:
    raw = _load_json(STAFF_FILE, {}) or {}
    out: Dict[str, StaffMember] = {}
    for k, v in raw.items():
        try:
            uid = int(k)
        except Exception:
            continue
        if isinstance(v, dict):
            out[str(uid)] = StaffMember(
                user_id=uid,
                name=str(v.get("name", "") or ""),
                username=str(v.get("username", "") or "").lstrip("@"),
                active=bool(v.get("active", True)),
            )
    return out

def load_groups() -> Dict[str, dict]:
    # groups.json format:
    # {
    #   "-100123...": {"name":"...", "active": true},
    #   "-100456...": {"name":"...", "active": false}
    # }
    raw = _load_json(GROUPS_FILE, {}) or {}
    out: Dict[str, dict] = {}
    for k, v in raw.items():
        try:
            gid = int(k)
        except Exception:
            continue
        if isinstance(v, dict):
            out[str(gid)] = {
                "id": gid,
                "name": str(v.get("name", "") or ""),
                "active": bool(v.get("active", True)),
            }
    return out

def load_categories() -> List[dict]:
    # expected list: [{"key":"psy","label":"..."}]
    raw = _load_json(CATEGORIES_FILE, []) or []
    cats = []
    for it in raw:
        if isinstance(it, dict) and it.get("key") and it.get("label"):
            cats.append({"key": str(it["key"]), "label": str(it["label"])})
    return cats

def load_info() -> dict:
    # keys: bot_description, ngo_mission, ngo_directions, ngo_contacts
    return _load_json(INFO_FILE, {}) or {}

def load_bot_config() -> dict:
    return _load_json(BOT_CONFIG_FILE, {}) or {}

def build_categories_keyboard(categories: List[dict]) -> InlineKeyboardMarkup:
    kb: List[List[InlineKeyboardButton]] = []
    for c in categories:
        kb.append([InlineKeyboardButton(c["label"], callback_data=f"cat:{c['key']}")])
    kb.append([InlineKeyboardButton("ℹ️ Інформація про бота", callback_data="info:bot")])
    kb.append([InlineKeyboardButton("ℹ️ Інформація про ГО «Ф1»", callback_data="info:ngo")])
    kb.append([InlineKeyboardButton("🔁 Змінити анонімність", callback_data="flow:anon")])
    kb.append([InlineKeyboardButton("❌ Скасувати", callback_data="flow:cancel")])
    return InlineKeyboardMarkup(kb)

def build_start_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Почати", callback_data="flow:start")]])

def build_anon_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Так, анонімно", callback_data="anon:yes")],
        [InlineKeyboardButton("Ні, не анонімно", callback_data="anon:no")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back:start")],
    ])

def build_ngo_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Місія", callback_data="ngo:mission")],
        [InlineKeyboardButton("Напрями діяльності", callback_data="ngo:directions")],
        [InlineKeyboardButton("Контакти", callback_data="ngo:contacts")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back:cats")],
    ])

def build_back_to_ngo_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back:ngo")]])

def build_status_keyboard(case_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Взято", callback_data=f"st:{case_id}:taken"),
            InlineKeyboardButton("⏳ Очікуємо", callback_data=f"st:{case_id}:wait"),
            InlineKeyboardButton("🏁 Закрито", callback_data=f"st:{case_id}:done"),
        ]
    ])

# ------------------- Global loaded config (static) -------------------

CATEGORIES = load_categories()
INFO_TEXTS = load_info()
BOT_CFG = load_bot_config()
STAFF = load_staff()
GROUPS = load_groups()

SHEETS = SheetsLogger(
    spreadsheet_id=os.environ.get("F1_SHEETS_ID", "").strip(),
    tab_name=os.environ.get("F1_SHEETS_TAB", "log").strip(),
    sa_json=os.environ.get("F1_GOOGLE_SA_JSON", "").strip(),
    sa_file=os.environ.get("F1_GOOGLE_SA_FILE", "").strip(),
)

# ------------------- UI render helpers -------------------

async def show_start(update_or_q, context: ContextTypes.DEFAULT_TYPE, edit: bool = False):
    text = INFO_TEXTS.get("bot_description", "Вітаю! Це бот ГО «Ф1».")
    if edit:
        await update_or_q.edit_message_text(text, reply_markup=build_start_keyboard())
    else:
        await update_or_q.message.reply_text(text, reply_markup=build_start_keyboard())

async def show_categories(q, context: ContextTypes.DEFAULT_TYPE):
    await q.edit_message_text(
        "Оберіть тему звернення:",
        reply_markup=build_categories_keyboard(CATEGORIES),
    )

async def show_ngo_menu(q, context: ContextTypes.DEFAULT_TYPE):
    await q.edit_message_text(
        "Інформація про ГО «Ф1»:",
        reply_markup=build_ngo_menu_keyboard(),
    )

# ------------------- Commands (no staff/group editing; only viewing) -------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # reset flow state
    context.user_data.setdefault("flow", {})
    await show_start(update, context, edit=False)

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "Команди:\n"
        "/start - почати\n"
        "/help - довідка\n"
        "/staff - список співробітників (тільки власник)\n"
        "/groups - список груп (тільки власник)\n"
        "/worktime - показати робочий час\n\n"
        "Надішліть повідомлення - бот передасть його команді."
    )
    await update.message.reply_text(txt)

async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("Нема доступу.")
    items = [m for m in STAFF.values() if m.active]
    if not items:
        return await update.message.reply_text("Список співробітників порожній.")
    lines = []
    for m in sorted(items, key=lambda x: x.user_id):
        extra = []
        if m.username:
            extra.append(f"@{m.username}")
        if m.name:
            extra.append(m.name)
        lines.append(f"- `{m.user_id}` {' '.join(extra)}".strip())
    await update.message.reply_text("Співробітники:\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("Нема доступу.")
    items = [g for g in GROUPS.values() if g.get("active")]
    if not items:
        return await update.message.reply_text("Список груп порожній.")
    lines = []
    for g in sorted(items, key=lambda x: int(x["id"])):
        name = g.get("name") or ""
        lines.append(f"- `{g['id']}` {name}".strip())
    await update.message.reply_text("Групи:\n" + "\n".join(lines), parse_mode=ParseMode.MARKDOWN)

async def cmd_worktime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    wh = (BOT_CFG.get("working_hours") or {})
    await update.message.reply_text("Робочий час (Europe/Kyiv):\n" + json.dumps(wh, ensure_ascii=False, indent=2))

# ------------------- Callback flow -------------------

async def on_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""

    if data == "flow:start":
        context.user_data["anonymous"] = None
        context.user_data["category_key"] = None
        context.user_data["category_label"] = None
        await q.edit_message_text("Бажаєте надіслати звернення анонімно?", reply_markup=build_anon_keyboard())
        return

    if data == "flow:anon":
        await q.edit_message_text("Бажаєте надіслати звернення анонімно?", reply_markup=build_anon_keyboard())
        return

    if data == "flow:cancel":
        context.user_data.clear()
        await show_start(q, context, edit=True)
        return

async def on_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "back:start":
        await show_start(q, context, edit=True)
        return
    if data == "back:cats":
        await show_categories(q, context)
        return
    if data == "back:ngo":
        await show_ngo_menu(q, context)
        return

async def on_anonymous(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "anon:yes":
        context.user_data["anonymous"] = True
    else:
        context.user_data["anonymous"] = False
    await show_categories(q, context)

async def on_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    key = (q.data or "").split(":", 1)[1] if ":" in (q.data or "") else ""
    label = None
    for c in CATEGORIES:
        if c["key"] == key:
            label = c["label"]
            break
    context.user_data["category_key"] = key
    context.user_data["category_label"] = label or key
    await q.edit_message_text(
        f"Обрано категорію: *{context.user_data['category_label']}*\n\nТепер напишіть повідомлення (можна текст, фото або файл).",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back:cats")]]),
    )

async def on_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "info:bot":
        txt = INFO_TEXTS.get("bot_description", "Це бот ГО «Ф1».")
        await q.edit_message_text(txt, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back:cats")]]))
        return
    if data == "info:ngo":
        await show_ngo_menu(q, context)
        return

async def on_ngo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if data == "ngo:mission":
        txt = INFO_TEXTS.get("ngo_mission", "Місія: ...")
        await q.edit_message_text(txt, reply_markup=build_back_to_ngo_keyboard())
        return
    if data == "ngo:directions":
        txt = INFO_TEXTS.get("ngo_directions", "Напрями діяльності: ...")
        await q.edit_message_text(txt, reply_markup=build_back_to_ngo_keyboard())
        return
    if data == "ngo:contacts":
        txt = INFO_TEXTS.get("ngo_contacts", "Контакти: ...")
        await q.edit_message_text(txt, reply_markup=build_back_to_ngo_keyboard())
        return

# ------------------- Status buttons in group -------------------

async def on_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    # st:<case_id>:<status>
    parts = data.split(":")
    if len(parts) != 3:
        return
    case_id, st = parts[1], parts[2]
    actor = q.from_user.full_name if q.from_user else ""
    now = _now_local()
    # log status to sheets
    SHEETS.log_event({
        "event": "status",
        "timestamp": _fmt_dt(now),
        "case_id": case_id,
        "status": st,
        "actor": actor,
    })
    try:
        await q.message.reply_text(f"Статус звернення *{case_id}*: *{st}* (від {actor})", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass

# ------------------- Main routing -------------------

def _active_group_ids() -> List[int]:
    ids = []
    for g in GROUPS.values():
        if g.get("active"):
            try:
                ids.append(int(g["id"]))
            except Exception:
                pass
    return ids

def _active_staff() -> List[StaffMember]:
    return [m for m in STAFF.values() if m.active]

def _build_header(user, anonymous: bool, category_label: str, case_id: str, dt: datetime) -> str:
    lines = ["🟦 Нове звернення", f"Категорія: {category_label}", f"ID: {case_id}", f"Час: {_fmt_dt(dt)}"]
    if anonymous:
        lines.append("🔒 Анонімне звернення")
    else:
        from_line = f"Від: {user.full_name} (id {user.id})"
        if user.username:
            from_line += f" @{user.username}"
        lines.append(from_line)
    return "\n".join(lines)

async def route_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not update.effective_user:
        return

    user = update.effective_user
    dt = _now_local()

    anonymous = bool(context.user_data.get("anonymous"))
    category_key = context.user_data.get("category_key") or ""
    category_label = context.user_data.get("category_label") or (category_key or "Без категорії")

    # if user hasn't selected category yet, gently prompt
    if not category_label or category_label == "Без категорії":
        await msg.reply_text("Будь ласка, натисніть /start і оберіть категорію звернення.")
        return

    case_id = uuid.uuid4().hex[:8]

    # ---------- LOG TO SHEETS (immediately) ----------
    # store minimal text safely (no PII when anonymous)
    text_preview = ""
    if msg.text:
        text_preview = msg.text[:2000]
    event = {
        "event": "incoming",
        "timestamp": _fmt_dt(dt),
        "case_id": case_id,
        "anonymous": "yes" if anonymous else "no",
        "category_key": category_key,
        "category_label": category_label,
        "message_type": "text" if msg.text else ("caption" if msg.caption else "media"),
        "text": ("" if anonymous else text_preview),
        "user_id": ("" if anonymous else str(user.id)),
        "username": ("" if anonymous else (user.username or "")),
        "full_name": ("" if anonymous else user.full_name),
    }
    SHEETS.log_event(event)

    # ---------- ROUTE ----------
    header = _build_header(user, anonymous, category_label, case_id, dt)

    # to groups
    group_ids = _active_group_ids()
    for gid in group_ids:
        try:
            await context.bot.send_message(chat_id=gid, text=header, reply_markup=build_status_keyboard(case_id))
            await msg.copy(chat_id=gid)
        except Exception:
            # ignore; no spam to user
            pass

    # to staff DMs
    for m in _active_staff():
        try:
            await context.bot.send_message(chat_id=m.user_id, text=header)
            await msg.copy(chat_id=m.user_id)
        except Exception:
            pass

    # ---------- AUTO-REPLY to user by working time ----------
    msgs = BOT_CFG.get("messages", {}) or {}
    if is_working_time(BOT_CFG, dt):
        reply = msgs.get("in_hours", "Дякуємо! Повідомлення передано команді.")
    else:
        reply = msgs.get("out_of_hours", "Дякуємо! Ми отримали звернення поза робочим часом і відповімо в найближчий робочий день.")
    await msg.reply_text(reply)

# ------------------- Bootstrap -------------------

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан TELEGRAM_BOT_TOKEN")
    if OWNER_ID == 0:
        raise SystemExit("❌ Не задан BOT_OWNER_ID")

    app = Application.builder().token(BOT_TOKEN).build()

    # commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CommandHandler("groups", cmd_groups))
    app.add_handler(CommandHandler("worktime", cmd_worktime))

    # callbacks (order matters)
    app.add_handler(CallbackQueryHandler(on_back, pattern=r"^back:(start|cats|ngo)$"))
    app.add_handler(CallbackQueryHandler(on_flow, pattern=r"^flow:(start|anon|cancel)$"))
    app.add_handler(CallbackQueryHandler(on_anonymous, pattern=r"^anon:(yes|no)$"))
    app.add_handler(CallbackQueryHandler(on_category_pick, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(on_info, pattern=r"^info:"))
    app.add_handler(CallbackQueryHandler(on_ngo, pattern=r"^ngo:"))
    app.add_handler(CallbackQueryHandler(on_status, pattern=r"^st:"))

    # messages
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_incoming))

    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
