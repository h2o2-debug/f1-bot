"""Telegram bot for ГО «Ф1».

Функції:
- Сценарій звернення:
  /start -> кнопка «Почати» -> питання про анонімність -> вибір категорії -> повідомлення
- Приймає повідомлення від користувачів і пересилає:
  1) у робочу групу (необовʼязково)
  2) у приватні повідомлення співробітникам (список керується командами)
- Категорії, опис бота, інформація про ГО, робочі години і тексти відповідей - у зовнішніх файлах.

Встановлення:
pip install -U python-telegram-bot==21.6

Змінні середовища:
TELEGRAM_BOT_TOKEN  - токен бота
BOT_OWNER_ID        - ваш numeric Telegram ID
ROUTING_GROUP_ID    - ID робочої групи (необовʼязково; можна задати через /setgroup)

Необовʼязково:
F1_BOT_DATA         - шлях до файлу даних (default: bot_data.json)
F1_BOT_CONFIG       - шлях до конфігу (default: bot_config.json)
F1_BOT_CATEGORIES   - шлях до категорій (default: categories.json)
F1_BOT_INFO         - шлях до інформаційних текстів (default: info_texts.json)
"""

import os
import json
from dataclasses import dataclass, asdict
from datetime import datetime, time
from typing import Dict, Optional, List, Tuple

from zoneinfo import ZoneInfo

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ================== FILES / ENV ==================

DATA_FILE = os.environ.get("F1_BOT_DATA", "bot_data.json")
CONFIG_FILE = os.environ.get("F1_BOT_CONFIG", "bot_config.json")
CATEGORIES_FILE = os.environ.get("F1_BOT_CATEGORIES", "categories.json")
INFO_FILE = os.environ.get("F1_BOT_INFO", "info_texts.json")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))
DEFAULT_GROUP_ID = int(os.environ.get("ROUTING_GROUP_ID", "0"))

# ================== DEFAULTS ==================

DEFAULT_CATEGORIES = [
    {"key": "psy", "label": "Психологічна підтримка"},
    {"key": "law", "label": "Юридична допомога"},
    {"key": "edu", "label": "Навчання / SkillsLab_F1"},
    {"key": "hum", "label": "Гуманітарна допомога"},
    {"key": "gbv", "label": "Насильство / Булінг"},
    {"key": "other", "label": "Інше"},
]

DEFAULT_CONFIG = {
    "timezone": "Europe/Kyiv",
    "working_hours": {
        "mon": [["09:00", "18:00"]],
        "tue": [["09:00", "18:00"]],
        "wed": [["09:00", "18:00"]],
        "thu": [["09:00", "18:00"]],
        "fri": [["09:00", "18:00"]],
        "sat": [],
        "sun": [],
    },
    "messages": {
        "welcome": "Натисніть «Почати», щоб створити звернення.",
        "ask_anonymous": "Хочете надіслати звернення анонімно?",
        "ask_category": "Оберіть категорію звернення:",
        "chosen_category": "✅ Обрано: {category}\n\nТепер напишіть повідомлення - я передам його команді.",
        "need_start": "Щоб надіслати звернення, натисніть /start і пройдіть короткі кроки.",
        "sent_working": "✅ Дякуємо! Повідомлення передано команді. Ми відповімо протягом робочого часу.",
        "sent_off": "✅ Дякуємо! Повідомлення передано команді. Зараз поза робочим часом - ми відповімо у найближчий робочий час.",
        "cancelled": "Скасовано. Щоб почати знову - натисніть /start.",
    },
}

DEFAULT_INFO = {
    "bot_description": (
        "🤖 Бот ГО «Ф1» - це канал звʼязку з командою.\n\n"
        "Як це працює:\n"
        "- натисніть «Почати»\n"
        "- оберіть анонімність\n"
        "- оберіть категорію\n"
        "- надішліть повідомлення (текст, фото, файл)\n\n"
        "Ми працюємо з повагою, конфіденційністю та без осуду."
    ),
    "ngo_info": (
        "ℹ️ ГО «Ф1» - всеукраїнське громадське обʼєднання.\n"
        "Напрями: протидія насильству, психосоціальна підтримка, навчання та перекваліфікація, гуманітарна допомога, розвиток громад."
    ),
}

# ================== DATA ==================

@dataclass
class StaffMember:
    user_id: int
    username: Optional[str] = None
    name: Optional[str] = None


def _safe_read_json(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def load_config() -> dict:
    cfg = _safe_read_json(CONFIG_FILE, DEFAULT_CONFIG)
    if not isinstance(cfg, dict):
        cfg = dict(DEFAULT_CONFIG)
    cfg.setdefault("timezone", DEFAULT_CONFIG["timezone"])
    cfg.setdefault("working_hours", DEFAULT_CONFIG["working_hours"])
    cfg.setdefault("messages", DEFAULT_CONFIG["messages"])
    for k, v in DEFAULT_CONFIG["messages"].items():
        cfg["messages"].setdefault(k, v)
    return cfg


CFG = load_config()
TZ = ZoneInfo(CFG.get("timezone", "Europe/Kyiv"))


def load_categories() -> List[Tuple[str, str]]:
    items = _safe_read_json(CATEGORIES_FILE, DEFAULT_CATEGORIES)
    out: List[Tuple[str, str]] = []
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict) and it.get("key") and it.get("label"):
                out.append((str(it["key"]), str(it["label"])))
    return out or [(c["key"], c["label"]) for c in DEFAULT_CATEGORIES]


CATEGORIES = load_categories()


def load_info() -> dict:
    info = _safe_read_json(INFO_FILE, DEFAULT_INFO)
    if not isinstance(info, dict):
        info = dict(DEFAULT_INFO)
    for k, v in DEFAULT_INFO.items():
        info.setdefault(k, v)
    return info


INFO = load_info()


def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    else:
        data = {}

    data.setdefault("group_id", DEFAULT_GROUP_ID)
    data.setdefault("staff", {})
    data.setdefault("tickets", {})  # id -> ticket dict
    return data


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_owner(update: Update) -> bool:
    return bool(update.effective_user and update.effective_user.id == OWNER_ID)


def parse_int(s: str):
    try:
        return int(s)
    except Exception:
        return None


def is_working_time(now_utc: datetime) -> bool:
    now = now_utc.astimezone(TZ)
    dow = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
    intervals = (CFG.get("working_hours") or {}).get(dow, [])
    if not intervals:
        return False

    for start_s, end_s in intervals:
        try:
            sh, sm = map(int, start_s.split(":"))
            eh, em = map(int, end_s.split(":"))
            start_t = time(sh, sm)
            end_t = time(eh, em)
        except Exception:
            continue

        if start_t <= now.time() <= end_t:
            return True
    return False


def short_ticket_id() -> str:
    # 6 символів base36-ish
    import random, string
    alphabet = string.digits + string.ascii_lowercase
    return "".join(random.choice(alphabet) for _ in range(6))


def allowed_staff_ids() -> set[int]:
    data = load_data()
    ids = {OWNER_ID}
    for v in (data.get("staff") or {}).values():
        try:
            ids.add(int(v.get("user_id")))
        except Exception:
            pass
    return ids


# ================== KEYBOARDS ==================

def kb_start() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("Почати", callback_data="flow:start")]])


def kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Скасувати", callback_data="flow:cancel")]])


def kb_anonymous() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Так", callback_data="anon:yes"),
                InlineKeyboardButton("Ні", callback_data="anon:no"),
            ],
            [InlineKeyboardButton("❌ Скасувати", callback_data="flow:cancel")],
        ]
    )


def kb_categories() -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(label, callback_data=f"cat:{key}")] for key, label in CATEGORIES]
    buttons.append([InlineKeyboardButton("ℹ️ Інформація про бота", callback_data="info:bot")])
    buttons.append([InlineKeyboardButton("ℹ️ Інформація про ГО «Ф1»", callback_data="info:ngo")])
    buttons.append([InlineKeyboardButton("🔁 Змінити анонімність", callback_data="flow:change_anon")])
    buttons.append([InlineKeyboardButton("❌ Скасувати", callback_data="flow:cancel")])
    return InlineKeyboardMarkup(buttons)


def kb_back_to_categories() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back:categories")]])




def kb_ngo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Місія", callback_data="info:ngo:mission")],
            [InlineKeyboardButton("Напрями діяльності", callback_data="info:ngo:directions")],
            [InlineKeyboardButton("Контакти", callback_data="info:ngo:contacts")],
            [InlineKeyboardButton("⬅️ Назад", callback_data="back:categories")],
        ]
    )


def kb_back_to_ngo_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Назад", callback_data="back:ngo_menu")]])

def kb_ticket_actions(ticket_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Взято", callback_data=f"t:take:{ticket_id}"),
                InlineKeyboardButton("⏳ Очікуємо", callback_data=f"t:wait:{ticket_id}"),
                InlineKeyboardButton("🏁 Закрито", callback_data=f"t:done:{ticket_id}"),
            ]
        ]
    )


# ================== FLOW ==================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # скидаємо вибір
    context.user_data.clear()
    context.user_data["flow_step"] = "start"

    # 1) опис бота
    desc = (INFO.get("bot_description") or "").strip()
    if desc:
        await update.message.reply_text(desc)

    # 2) кнопка «Почати»
    await update.message.reply_text(CFG["messages"]["welcome"], reply_markup=kb_start())


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Команди:\n"
        "/start - почати\n"
        "/category - змінити категорію\n"
        "/worktime - показати робочий статус\n"
        "/staff - список співробітників\n"
        "/addstaff <user_id> [@username] [Ім'я]\n"
        "/removestaff <user_id>\n"
        "/setgroup <group_id>\n"
        "/report [days] - звіт (лише власник)"
    )


async def cmd_worktime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now(tz=ZoneInfo("UTC"))
    if is_working_time(now):
        await update.message.reply_text("🟢 Зараз робочий час.")
    else:
        await update.message.reply_text("🔴 Зараз поза робочим часом.")


async def cmd_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # дозволяє змінити категорію в будь-який момент
    context.user_data["flow_step"] = "category"
    await update.message.reply_text(CFG["messages"]["ask_category"], reply_markup=kb_categories())


async def on_flow_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    context.user_data["flow_step"] = "anon"
    await q.edit_message_text(CFG["messages"]["ask_anonymous"], reply_markup=kb_anonymous())


async def on_flow_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q:
        await q.answer()
        context.user_data.clear()
        try:
            await q.edit_message_text(CFG["messages"]["cancelled"])
        except Exception:
            pass
        return

    # якщо як команда
    context.user_data.clear()
    if update.message:
        await update.message.reply_text(CFG["messages"]["cancelled"])


async def on_change_anon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    context.user_data["flow_step"] = "anon"
    await q.edit_message_text(CFG["messages"]["ask_anonymous"], reply_markup=kb_anonymous())


async def on_anonymous_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if q.data == "anon:yes":
        context.user_data["anonymous"] = True
    else:
        context.user_data["anonymous"] = False

    context.user_data["flow_step"] = "category"
    await q.edit_message_text(CFG["messages"]["ask_category"], reply_markup=kb_categories())


async def on_category_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    data = q.data or ""
    if not data.startswith("cat:"):
        return
    key = data.split("cat:", 1)[1].strip()
    label = next((lbl for k, lbl in CATEGORIES if k == key), None)
    if not label:
        return await q.edit_message_text("Категорія не знайдена. Спробуйте /start ще раз.")

    context.user_data["category_key"] = key
    context.user_data["category_label"] = label
    context.user_data["flow_step"] = "ready"

    await q.edit_message_text(
        CFG["messages"]["chosen_category"].format(category=label),
        parse_mode=ParseMode.MARKDOWN,
    )


async def on_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    if q.data == "info:bot":
        txt = (INFO.get("bot_description", "") or "").strip() or "Немає інформації."
        await q.edit_message_text(f"ℹ️ Інформація про бота\n\n{txt}", reply_markup=kb_back_to_categories())
        return

    if q.data == "info:ngo":
        await q.edit_message_text("ℹ️ Інформація про ГО «Ф1»\n\nОберіть розділ:", reply_markup=kb_ngo_menu())
        return

    if q.data == "info:ngo:mission":
        txt = (INFO.get("ngo_mission", "") or "").strip() or "Немає інформації."
        await q.edit_message_text(f"Місія\n\n{txt}", reply_markup=kb_back_to_ngo_menu())
        return

    if q.data == "info:ngo:directions":
        txt = (INFO.get("ngo_directions", "") or "").strip() or "Немає інформації."
        await q.edit_message_text(f"Напрями діяльності\n\n{txt}", reply_markup=kb_back_to_ngo_menu())
        return

    if q.data == "info:ngo:contacts":
        txt = (INFO.get("ngo_contacts", "") or "").strip() or "Немає інформації."
        await q.edit_message_text(f"Контакти\n\n{txt}", reply_markup=kb_back_to_ngo_menu())
        return


async def on_back_to_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    context.user_data["flow_step"] = "category"
    await q.edit_message_text(CFG["messages"]["ask_category"], reply_markup=kb_categories())



async def on_back_to_ngo_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()
    await q.edit_message_text("ℹ️ Інформація про ГО «Ф1»\n\nОберіть розділ:", reply_markup=kb_ngo_menu())


# ================== ADMIN: GROUP / STAFF ==================

async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("Нема доступу.")
    if not context.args:
        return await update.message.reply_text("Використання: /setgroup <group_id>")

    gid = parse_int(context.args[0])
    if gid is None:
        return await update.message.reply_text("group_id має бути числом (наприклад: -1001234567890).")

    data = load_data()
    data["group_id"] = gid
    save_data(data)
    await update.message.reply_text(f"✅ Групу встановлено: `{gid}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_staff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    staff: Dict[str, dict] = data.get("staff", {})
    if not staff:
        return await update.message.reply_text("Список співробітників порожній.")
    members = [StaffMember(**v) for v in staff.values()]

    def line(m: StaffMember) -> str:
        u = f"@{m.username}" if m.username else ""
        n = f"{m.name}" if m.name else ""
        extra = " ".join(x for x in [u, n] if x).strip()
        return f"- `{m.user_id}` {extra}".strip()

    lines = "\n".join(line(m) for m in sorted(members, key=lambda x: x.user_id))
    await update.message.reply_text("Співробітники:\n" + lines, parse_mode=ParseMode.MARKDOWN)


async def cmd_addstaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("Нема доступу.")
    if not context.args:
        return await update.message.reply_text("Використання: /addstaff <user_id> [@username] [Ім'я]")

    uid = parse_int(context.args[0])
    if uid is None:
        return await update.message.reply_text("user_id має бути числом.")

    username = context.args[1].lstrip("@") if len(context.args) >= 2 else None
    name = " ".join(context.args[2:]).strip() if len(context.args) >= 3 else None

    data = load_data()
    staff = data.setdefault("staff", {})
    staff[str(uid)] = asdict(StaffMember(user_id=uid, username=username, name=name))
    save_data(data)

    await update.message.reply_text(f"✅ Додано співробітника: `{uid}`", parse_mode=ParseMode.MARKDOWN)


async def cmd_removestaff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("Нема доступу.")
    if not context.args:
        return await update.message.reply_text("Використання: /removestaff <user_id>")

    uid = parse_int(context.args[0])
    if uid is None:
        return await update.message.reply_text("user_id має бути числом.")

    data = load_data()
    staff = data.get("staff", {})
    if str(uid) in staff:
        del staff[str(uid)]
        save_data(data)
        await update.message.reply_text(f"✅ Видалено: `{uid}`", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text("Такого співробітника немає у списку.")


# ================== TICKETS / MEAL ==================

def _now_iso() -> str:
    return datetime.now(tz=ZoneInfo("UTC")).isoformat()


def create_ticket(data: dict, *, user_id: int, anonymous: bool, category: str) -> str:
    tickets = data.setdefault("tickets", {})
    tid = short_ticket_id()
    # ensure unique
    while tid in tickets:
        tid = short_ticket_id()

    tickets[tid] = {
        "id": tid,
        "created_at": _now_iso(),
        "user_id": user_id,
        "anonymous": bool(anonymous),
        "category": category,
        "status": "new",
        "assignee": None,
        "last_update": _now_iso(),
    }
    return tid


def set_ticket_status(data: dict, tid: str, status: str, assignee: Optional[str]) -> bool:
    t = (data.get("tickets") or {}).get(tid)
    if not t:
        return False
    t["status"] = status
    t["assignee"] = assignee
    t["last_update"] = _now_iso()
    return True


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return await update.message.reply_text("Нема доступу.")
    days = 7
    if context.args:
        try:
            days = max(1, min(90, int(context.args[0])))
        except Exception:
            days = 7

    data = load_data()
    tickets = list((data.get("tickets") or {}).values())
    if not tickets:
        return await update.message.reply_text("Немає звернень у журналі.")

    cutoff = datetime.now(tz=ZoneInfo("UTC")).timestamp() - days * 86400

    def parse_ts(s: str) -> float:
        try:
            return datetime.fromisoformat(s).timestamp()
        except Exception:
            return 0.0

    tickets = [t for t in tickets if parse_ts(t.get("created_at", "")) >= cutoff]
    if not tickets:
        return await update.message.reply_text(f"Немає звернень за останні {days} днів.")

    by_cat: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    anon_count = 0

    for t in tickets:
        by_cat[t.get("category") or ""] = by_cat.get(t.get("category") or "", 0) + 1
        by_status[t.get("status") or ""] = by_status.get(t.get("status") or "", 0) + 1
        if t.get("anonymous"):
            anon_count += 1

    lines = [f"📊 Звіт за {days} днів", f"Всього звернень: {len(tickets)}", f"Анонімних: {anon_count}"]
    lines.append("\nЗа категоріями:")
    for k, v in sorted(by_cat.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {k}: {v}")

    lines.append("\nЗа статусами:")
    for k, v in sorted(by_status.items(), key=lambda x: (-x[1], x[0])):
        lines.append(f"- {k}: {v}")

    await update.message.reply_text("\n".join(lines))


# ================== ROUTING ==================

async def route_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    cat_label = context.user_data.get("category_label")
    if not cat_label:
        return await msg.reply_text(CFG["messages"]["need_start"])

    data = load_data()
    group_id = int(data.get("group_id") or 0)
    staff_dict: Dict[str, dict] = data.get("staff", {})

    user = update.effective_user
    anonymous = bool(context.user_data.get("anonymous"))

    # створюємо ticket
    tid = create_ticket(data, user_id=user.id if user else 0, anonymous=anonymous, category=cat_label)
    save_data(data)

    # header
    if anonymous:
        from_line = "Від: Анонімно"
        privacy_line = "🔒 Анонімне звернення"
    else:
        from_line = f"Від: {user.full_name}"
        if user.username:
            from_line += f" @{user.username}"
        # id залишаємо тільки для неанонімних
        from_line += f" (id {user.id})"
        privacy_line = ""

    header_lines = [f"🟦 Нове звернення [{cat_label}] #{tid}", from_line]
    if privacy_line:
        header_lines.append(privacy_line)
    header = "\n".join(header_lines)

    # у групу
    if group_id != 0:
        try:
            await context.bot.send_message(chat_id=group_id, text=header, reply_markup=kb_ticket_actions(tid))
            await msg.copy(chat_id=group_id)
        except Exception:
            pass

    # співробітникам
    for v in staff_dict.values():
        m = StaffMember(**v)
        try:
            await context.bot.send_message(chat_id=m.user_id, text=header)
            await msg.copy(chat_id=m.user_id)
        except Exception:
            pass

    # відповідь користувачу залежно від робочого часу
    if is_working_time(datetime.now(tz=ZoneInfo("UTC"))):
        await msg.reply_text(CFG["messages"]["sent_working"])
    else:
        await msg.reply_text(CFG["messages"]["sent_off"])


async def on_ticket_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    await q.answer()

    # права: власник або співробітник зі списку
    uid = q.from_user.id if q.from_user else 0
    if uid not in allowed_staff_ids():
        return await q.answer("Нема доступу.", show_alert=True)

    parts = (q.data or "").split(":")
    if len(parts) != 3:
        return
    _, action, tid = parts

    status_map = {"take": "in_progress", "wait": "waiting", "done": "done"}
    status = status_map.get(action)
    if not status:
        return

    assignee = q.from_user.full_name if q.from_user else None

    data = load_data()
    ok = set_ticket_status(data, tid, status, assignee)
    if not ok:
        return await q.answer("Звернення не знайдено.", show_alert=True)
    save_data(data)

    # повідомлення у групі як підтвердження (не чіпаємо оригінальний header, щоб не зламати контент)
    try:
        status_ua = {"in_progress": "Взято в роботу", "waiting": "Очікуємо", "done": "Закрито"}[status]
        await q.message.reply_text(f"📌 Статус #{tid}: {status_ua}. Відповідальна особа: {assignee}")
    except Exception:
        pass


# ================== MAIN ==================

def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ Не задан TELEGRAM_BOT_TOKEN")
    if OWNER_ID == 0:
        raise SystemExit("❌ Не задан BOT_OWNER_ID")

    app = Application.builder().token(BOT_TOKEN).build()

    # flow
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("category", cmd_category))
    app.add_handler(CommandHandler("worktime", cmd_worktime))

    app.add_handler(CallbackQueryHandler(on_flow_start, pattern=r"^flow:start$"))
    app.add_handler(CallbackQueryHandler(on_flow_cancel, pattern=r"^flow:cancel$"))
    app.add_handler(CallbackQueryHandler(on_change_anon, pattern=r"^flow:change_anon$"))
    app.add_handler(CallbackQueryHandler(on_anonymous_pick, pattern=r"^anon:(yes|no)$"))
    app.add_handler(CallbackQueryHandler(on_category_pick, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(on_info, pattern=r"^info:"))
    app.add_handler(CallbackQueryHandler(on_back_to_categories, pattern=r"^back:categories$"))

    # tickets
    app.add_handler(CallbackQueryHandler(on_ticket_action, pattern=r"^t:(take|wait|done):"))

    # admin
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("staff", cmd_staff))
    app.add_handler(CommandHandler("addstaff", cmd_addstaff))
    app.add_handler(CommandHandler("removestaff", cmd_removestaff))
    app.add_handler(CommandHandler("report", cmd_report))

    # messages
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_incoming))

    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
