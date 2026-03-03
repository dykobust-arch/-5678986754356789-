#!/usr/bin/env python3
# REFERRAL TELEGRAM BOT — FULL FIX

import logging
import sqlite3
from datetime import date

import httpx
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler,
)

# ── CONFIG ──────────────────────────────────────────────────
BOT_TOKEN           = "8575300323:AAGU_zPPaXbBl5EbXJ6j9E1rvb-M38poNR0"
ADMIN_ID            = 8535260202
ADMIN_USERNAME      = "famelonov"
CHANNEL_LINK        = "https://t.me/REF_GO_PAY"
REWARD_PER_REFERRAL = 2
MIN_WITHDRAWAL      = 15
TASK_REWARD         = 3

TGRASS_URL  = "https://tgrass.space/offers"
TGRASS_AUTH = "aca6f2a2ad034cf5af45277689c2fa1e"
# ────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(__name__)

DB_FILE = "bot.db"

# ── DATABASE ─────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_FILE)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id      INTEGER PRIMARY KEY,
                username     TEXT,
                full_name    TEXT,
                referrer_id  INTEGER,
                balance      REAL    DEFAULT 0,
                total_earned REAL    DEFAULT 0,
                ref_count    INTEGER DEFAULT 0,
                tasks_done   INTEGER DEFAULT 0,
                joined_at    TEXT    DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS withdrawals (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                amount     REAL    NOT NULL,
                details    TEXT    NOT NULL,
                bank       TEXT    NOT NULL,
                status     TEXT    DEFAULT 'pending',
                created_at TEXT    DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS stats (
                id              INTEGER PRIMARY KEY DEFAULT 1,
                total_withdrawn REAL DEFAULT 0
            );
            INSERT OR IGNORE INTO stats VALUES (1, 0);
        """)
        # Миграция для старых БД
        try:
            c.execute("ALTER TABLE users ADD COLUMN tasks_done INTEGER DEFAULT 0")
            log.info("Миграция: добавлена колонка tasks_done")
        except Exception:
            pass


def get_user(uid):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (uid,)).fetchone()


def register_user(uid, username, full_name, referrer_id=None):
    with _conn() as c:
        if c.execute("SELECT 1 FROM users WHERE user_id=?", (uid,)).fetchone():
            return False
        c.execute(
            "INSERT INTO users (user_id, username, full_name, referrer_id) VALUES (?,?,?,?)",
            (uid, username, full_name, referrer_id),
        )
        if referrer_id:
            c.execute(
                """UPDATE users
                   SET ref_count=ref_count+1,
                       balance=balance+?,
                       total_earned=total_earned+?
                   WHERE user_id=?""",
                (REWARD_PER_REFERRAL, REWARD_PER_REFERRAL, referrer_id),
            )
        return True


# ── KEYBOARDS ────────────────────────────────────────────────

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("💰 Заработать"), KeyboardButton("📋 Задания")],
        [KeyboardButton("👤 Кабинет"),    KeyboardButton("ℹ️ О боте")],
    ],
    resize_keyboard=True,
)

CANCEL_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True,
)


# ── /start ───────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    args = ctx.args or []
    referrer_id = None

    if args:
        try:
            ref = int(str(args[0]).replace("ref_", ""))
            if ref != u.id and get_user(ref):
                referrer_id = ref
        except (ValueError, TypeError):
            pass

    is_new = register_user(u.id, u.username, u.full_name, referrer_id)

    if is_new and referrer_id:
        referrer = get_user(referrer_id)
        if referrer:
            try:
                await ctx.bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        f"🎉 По вашей реферальной ссылке зарегистрировался новый пользователь!\n\n"
                        f"👤 {u.full_name}\n"
                        f"💰 Начислено: +{REWARD_PER_REFERRAL}₽\n"
                        f"💵 Баланс: {referrer['balance'] + REWARD_PER_REFERRAL:.0f}₽"
                    ),
                )
            except Exception as e:
                log.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")

    label = "С возвращением" if not is_new else "Добро пожаловать"
    await update.message.reply_text(
        f"👋 {label}, {u.first_name}!\n\n"
        f"🤑 Приглашай друзей и получай {REWARD_PER_REFERRAL}₽ за каждого!\n\n"
        f"Выбери действие 👇",
        reply_markup=MAIN_KB,
    )


# ── 💰 Заработать ────────────────────────────────────────────

async def earn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = get_user(uid)
    bot_me = await ctx.bot.get_me()
    ref_link  = f"https://t.me/{bot_me.username}?start=ref_{uid}"
    balance   = u["balance"]   if u else 0
    ref_count = u["ref_count"] if u else 0
    share_url = (
        f"https://t.me/share/url?url={ref_link}"
        f"&text=Зарабатывай+вместе+со+мной!"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Поделиться ссылкой", url=share_url)
    ]])
    await update.message.reply_text(
        f"💰 Заработок\n\n"
        f"🔗 Ваша реферальная ссылка:\n{ref_link}\n\n"
        f"👥 Приглашено рефералов: {ref_count}\n"
        f"💵 Ваш баланс: {balance:.0f}₽\n\n"
        f"💡 За каждого приглашённого — {REWARD_PER_REFERRAL}₽ на баланс",
        reply_markup=kb,
    )


# ── 👤 Кабинет ───────────────────────────────────────────────

async def cabinet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = get_user(uid)
    balance   = u["balance"]      if u else 0
    ref_count = u["ref_count"]    if u else 0
    earned    = u["total_earned"] if u else 0
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("💳 Вывести", callback_data="cabinet_withdraw")
    ]])
    await update.message.reply_text(
        f"👤 Личный кабинет\n\n"
        f"💵 Баланс: {balance:.0f}₽\n"
        f"💸 Всего заработано: {earned:.0f}₽\n"
        f"👥 Рефералов приглашено: {ref_count}",
        reply_markup=kb,
    )


async def cabinet_withdraw_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    u = get_user(uid)
    balance = u["balance"] if u else 0
    await q.message.reply_text(
        f"💳 Вывод средств\n\n"
        f"💵 Ваш баланс: {balance:.0f}₽\n"
        f"📊 Минимум: {MIN_WITHDRAWAL}₽\n\n"
        f"Введите сумму вывода:",
        reply_markup=CANCEL_KB,
    )
    return W_AMOUNT


# ── 📋 Задания (tgrass) ──────────────────────────────────────

async def _tgrass_request(user):
    """Запрос к tgrass API. Возвращает (status_code, data_dict | None)."""
    payload = {
        "tg_user_id": int(user.id),
        "tg_login":   user.username or "",
        "lang":       getattr(user, "language_code", "ru") or "ru",
        "is_premium": bool(getattr(user, "is_premium", False)),
    }
    headers = {
        "accept":       "application/json",
        "Content-Type": "application/json",
        "Auth":         TGRASS_AUTH,
    }
    log.info(f"tgrass request [{user.id}]: {payload}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            resp = await client.post(TGRASS_URL, json=payload, headers=headers)
        log.info(f"tgrass response [{user.id}]: HTTP {resp.status_code} | {resp.text[:500]}")
        try:
            data = resp.json()
        except Exception:
            log.warning(f"tgrass: не удалось распарсить JSON: {resp.text[:200]}")
            data = None
        return resp.status_code, data
    except httpx.TimeoutException:
        log.warning(f"tgrass timeout [{user.id}]")
        return None, None
    except Exception as e:
        log.warning(f"tgrass exception [{user.id}]: {e}")
        return None, None


def _build_offers_kb(offers: list) -> InlineKeyboardMarkup:
    """Собирает клавиатуру из списка офферов + кнопка проверки."""
    kb = []
    for offer in offers:
        offer_type = offer.get("type", "")
        btn_text   = "Подписаться" if offer_type == "channel" else "Перейти"
        link       = offer.get("link") or offer.get("url", "")
        if link:
            kb.append([InlineKeyboardButton(text=btn_text, url=link)])
    kb.append([InlineKeyboardButton(text="✅ Проверить выполнение", callback_data="check_tgrass")])
    return InlineKeyboardMarkup(kb)


async def tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text("⏳ Загружаю задания...")

    status_code, data = await _tgrass_request(user)

    if status_code is None or data is None:
        await update.message.reply_text(
            "⚠️ Не удалось загрузить задания. Попробуйте позже."
        )
        return

    tg_status = data.get("status") if isinstance(data, dict) else None
    offers    = data.get("offers", []) if isinstance(data, dict) else []

    # Если появились новые задания — сбрасываем флаг выполнения
    if tg_status == "not_ok" and offers:
        u = get_user(user.id)
        if u and u["tasks_done"]:
            with _conn() as c:
                c.execute("UPDATE users SET tasks_done=0 WHERE user_id=?", (user.id,))

    if status_code == 200 and tg_status == "not_ok" and offers:
        await update.message.reply_text(
            f"📋 Задания\n\n"
            f"Выполни все задания и получи {TASK_REWARD}₽ на баланс:",
            reply_markup=_build_offers_kb(offers),
        )

    elif status_code == 200 and tg_status == "ok":
        u = get_user(user.id)
        if u and u["tasks_done"]:
            await update.message.reply_text(
                "✅ Вы уже выполнили все задания и получили награду!\n\n"
                "Следите за появлением новых заданий."
            )
        else:
            # Статус ok, но награда ещё не начислена — начисляем
            with _conn() as c:
                c.execute(
                    """UPDATE users
                       SET balance=balance+?, total_earned=total_earned+?, tasks_done=1
                       WHERE user_id=?""",
                    (TASK_REWARD, TASK_REWARD, user.id),
                )
            u = get_user(user.id)
            await update.message.reply_text(
                f"🎉 Задания выполнены!\n\n"
                f"💰 Начислено: {TASK_REWARD}₽\n"
                f"💵 Ваш баланс: {u['balance']:.0f}₽"
            )

    else:
        await update.message.reply_text(
            "На данный момент нет доступных заданий.\n"
            "Загляните позже!"
        )


async def check_tgrass_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("Проверяю...")
    user = q.from_user

    # Уже получил награду?
    u = get_user(user.id)
    if u and u["tasks_done"]:
        await q.message.reply_text("✅ Вы уже получили награду за задания!")
        return

    # Атомарная блокировка: tasks_done = 2 означает "в процессе проверки"
    # UPDATE сработает только если tasks_done = 0 (не выполнено и не в процессе)
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET tasks_done=2 WHERE user_id=? AND tasks_done=0",
            (user.id,),
        )
        if cur.rowcount == 0:
            # Либо уже выполнено (1), либо другой запрос уже проверяет (2)
            await q.message.reply_text("⏳ Уже проверяется, подождите...")
            return

    status_code, data = await _tgrass_request(user)

    if status_code is None or data is None:
        # Снимаем блокировку чтобы можно было попробовать снова
        with _conn() as c:
            c.execute("UPDATE users SET tasks_done=0 WHERE user_id=? AND tasks_done=2", (user.id,))
        await q.message.reply_text("⚠️ Ошибка проверки. Попробуйте позже.")
        return

    tg_status = data.get("status") if isinstance(data, dict) else None
    offers    = data.get("offers", []) if isinstance(data, dict) else []

    if status_code == 200 and tg_status == "ok":
        with _conn() as c:
            c.execute(
                """UPDATE users
                   SET balance=balance+?, total_earned=total_earned+?, tasks_done=1
                   WHERE user_id=?""",
                (TASK_REWARD, TASK_REWARD, user.id),
            )
        u = get_user(user.id)
        new_balance = u["balance"] if u else 0
        await q.message.reply_text(
            f"🎉 Задание успешно выполнено!\n\n"
            f"💰 Начислено: {TASK_REWARD}₽\n"
            f"💵 Ваш баланс: {new_balance:.0f}₽"
        )

    elif status_code == 200 and tg_status == "not_ok":
        # Задания не выполнены — снимаем блокировку
        with _conn() as c:
            c.execute("UPDATE users SET tasks_done=0 WHERE user_id=? AND tasks_done=2", (user.id,))
        if offers:
            await q.message.reply_text(
                "❌ Не все задания выполнены.\n\n"
                "Выполните оставшиеся и нажмите «Проверить» снова:",
                reply_markup=_build_offers_kb(offers),
            )
        else:
            await q.message.reply_text(
                "❌ Задания ещё не выполнены. Подпишитесь и попробуйте снова."
            )
    else:
        with _conn() as c:
            c.execute("UPDATE users SET tasks_done=0 WHERE user_id=? AND tasks_done=2", (user.id,))
        await q.message.reply_text("⚠️ Не удалось проверить задания. Попробуйте позже.")


# ── 💳 Вывод средств ─────────────────────────────────────────

W_AMOUNT, W_DETAILS, W_BANK = range(3)


async def withdraw_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    uid = update.effective_user.id
    u = get_user(uid)
    balance = u["balance"] if u else 0

    try:
        amount = float(text.replace(",", "."))
        if amount <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Введите корректное число:")
        return W_AMOUNT

    if amount < MIN_WITHDRAWAL:
        await update.message.reply_text(
            f"❌ Минимальная сумма: {MIN_WITHDRAWAL}₽\n\nВведите другую сумму:"
        )
        return W_AMOUNT

    if amount > balance:
        await update.message.reply_text(
            f"❌ Недостаточно средств.\n"
            f"💵 Ваш баланс: {balance:.0f}₽\n\n"
            f"Введите другую сумму:"
        )
        return W_AMOUNT

    ctx.user_data["w_amount"] = amount
    await update.message.reply_text(
        f"✅ Сумма: {amount:.0f}₽\n\n"
        f"💳 Введите реквизиты (номер карты или телефона):"
    )
    return W_DETAILS


async def withdraw_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    ctx.user_data["w_details"] = text
    await update.message.reply_text(
        "🏦 Укажите банк получателя:\n"
        "(например: Сбербанк, Тинькофф, ВТБ, Альфа-Банк)"
    )
    return W_BANK


async def withdraw_bank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    uid = update.effective_user.id
    u = get_user(uid)
    amount  = ctx.user_data.get("w_amount", 0)
    details = ctx.user_data.get("w_details", "")
    bank    = text

    # Финальная проверка баланса
    if not u or u["balance"] < amount:
        await update.message.reply_text(
            "❌ Недостаточно средств. Заявка отменена.",
            reply_markup=MAIN_KB,
        )
        return ConversationHandler.END

    with _conn() as c:
        c.execute(
            "INSERT INTO withdrawals (user_id, amount, details, bank) VALUES (?,?,?,?)",
            (uid, amount, details, bank),
        )
        wid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (amount, uid))

    await update.message.reply_text(
        f"✅ Заявка #{wid} принята!\n\n"
        f"💵 Сумма: {amount:.0f}₽\n"
        f"💳 Реквизиты: {details}\n"
        f"🏦 Банк: {bank}\n\n"
        f"⏳ Ожидайте обработки администратором.",
        reply_markup=MAIN_KB,
    )

    uname_str = f"@{u['username']}" if u and u["username"] else "нет"
    full_name = u["full_name"] if u else "Unknown"
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Одобрить",  callback_data=f"appr_{wid}"),
        InlineKeyboardButton("❌ Отклонить", callback_data=f"rjct_{wid}"),
    ]])
    try:
        await ctx.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"💳 Новая заявка на вывод #{wid}\n\n"
                f"👤 {full_name}\n"
                f"📱 Username: {uname_str}\n"
                f"🆔 ID: {uid}\n\n"
                f"💵 Сумма: {amount:.0f}₽\n"
                f"💳 Реквизиты: {details}\n"
                f"🏦 Банк: {bank}"
            ),
            reply_markup=kb,
        )
    except Exception as e:
        log.warning(f"Не удалось уведомить администратора: {e}")

    return ConversationHandler.END


async def w_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
    return ConversationHandler.END


# ── ℹ️ О боте ────────────────────────────────────────────────

async def about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    today = date.today().isoformat()
    with _conn() as c:
        total_users     = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        today_users     = c.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(joined_at)=?", (today,)
        ).fetchone()[0]
        total_withdrawn = c.execute(
            "SELECT total_withdrawn FROM stats WHERE id=1"
        ).fetchone()[0]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Канал с выплатами", url=CHANNEL_LINK)],
        [InlineKeyboardButton("👨‍💼 Администратор", url=f"https://t.me/{ADMIN_USERNAME}")],
    ])
    await update.message.reply_text(
        f"ℹ️ О боте\n\n📊 Статистика:\n"
        f"├ 👥 Всего пользователей: {total_users}\n"
        f"├ 📅 Новых сегодня: {today_users}\n"
        f"└ 💸 Всего выплачено: {total_withdrawn:.0f}₽\n\n"
        f"💡 Приглашай друзей — получай {REWARD_PER_REFERRAL}₽ за каждого!",
        reply_markup=kb,
    )


# ── Одобрить / Отклонить ─────────────────────────────────────

async def approve_reject_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        await q.answer("⛔ Нет доступа", show_alert=True)
        return

    action, wid_str = q.data.split("_", 1)
    wid = int(wid_str)

    with _conn() as c:
        w = c.execute("SELECT * FROM withdrawals WHERE id=?", (wid,)).fetchone()
        if not w:
            await q.answer("⚠️ Заявка не найдена", show_alert=True)
            return
        if w["status"] != "pending":
            await q.answer("ℹ️ Заявка уже обработана", show_alert=True)
            return

        if action == "appr":
            c.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
            c.execute(
                "UPDATE stats SET total_withdrawn=total_withdrawn+? WHERE id=1",
                (w["amount"],),
            )
            verdict  = "✅ ОДОБРЕНО"
            user_msg = (
                f"✅ Ваша заявка #{wid} одобрена!\n\n"
                f"💵 Сумма: {w['amount']:.0f}₽\n"
                f"💳 Реквизиты: {w['details']}\n"
                f"🏦 Банк: {w['bank']}\n\n"
                f"Средства будут переведены в ближайшее время 🎉"
            )
        else:
            c.execute("UPDATE withdrawals SET status='rejected' WHERE id=?", (wid,))
            c.execute(
                "UPDATE users SET balance=balance+? WHERE user_id=?",
                (w["amount"], w["user_id"]),
            )
            verdict  = "❌ ОТКЛОНЕНО (баланс возвращён)"
            user_msg = (
                f"❌ Ваша заявка #{wid} отклонена.\n\n"
                f"💵 Сумма {w['amount']:.0f}₽ возвращена на баланс.\n\n"
                f"По вопросам обратитесь к администратору."
            )

    try:
        await q.edit_message_text(q.message.text + f"\n\n— {verdict}")
    except Exception:
        pass
    try:
        await ctx.bot.send_message(chat_id=w["user_id"], text=user_msg)
    except Exception:
        pass


# ── /admin ───────────────────────────────────────────────────

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    with _conn() as c:
        total   = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        pending = c.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE status='pending'"
        ).fetchone()[0]
        total_w = c.execute(
            "SELECT total_withdrawn FROM stats WHERE id=1"
        ).fetchone()[0]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(
            f"📋 Заявки на вывод ({pending})", callback_data="adm_withdrawals"
        )],
    ])
    await update.message.reply_text(
        f"👨‍💼 Панель администратора\n\n"
        f"👥 Пользователей: {total}\n"
        f"💸 Выплачено: {total_w:.0f}₽\n"
        f"⏳ Заявок в очереди: {pending}",
        reply_markup=kb,
    )


async def adm_withdrawals_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return
    with _conn() as c:
        rows = c.execute(
            """SELECT w.*, u.full_name, u.username FROM withdrawals w
               JOIN users u ON w.user_id=u.user_id
               WHERE w.status='pending'
               ORDER BY w.created_at ASC LIMIT 20"""
        ).fetchall()
    if not rows:
        await q.message.reply_text("✅ Заявок на вывод нет.")
        return
    for w in rows:
        uname = f"@{w['username']}" if w["username"] else "нет"
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Одобрить",  callback_data=f"appr_{w['id']}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"rjct_{w['id']}"),
        ]])
        await q.message.reply_text(
            f"💳 Заявка #{w['id']}\n\n"
            f"👤 {w['full_name']} ({uname})\n"
            f"🆔 {w['user_id']}\n\n"
            f"💵 Сумма: {w['amount']:.0f}₽\n"
            f"💳 Реквизиты: {w['details']}\n"
            f"🏦 Банк: {w['bank']}\n"
            f"📅 {w['created_at'][:16]}",
            reply_markup=kb,
        )


# ── 📢 Рассылка ───────────────────────────────────────────────

B_CONTENT, B_BTN_CHOICE, B_BTN_TEXT, B_BTN_URL, B_CONFIRM = range(5, 10)


async def bc_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    await q.message.reply_text(
        "📢 Создание рассылки\n\n"
        "Отправьте контент рассылки:\n"
        "• Текст\n• Фото с подписью\n• Видео с подписью\n\n"
        "Для отмены: /cancel"
    )
    return B_CONTENT


async def bc_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    msg = update.message
    if msg.photo:
        ctx.user_data.update({
            "bc_type":             "photo",
            "bc_file":             msg.photo[-1].file_id,
            "bc_caption":          msg.caption,
            "bc_caption_entities": msg.caption_entities,
        })
    elif msg.video:
        ctx.user_data.update({
            "bc_type":             "video",
            "bc_file":             msg.video.file_id,
            "bc_caption":          msg.caption,
            "bc_caption_entities": msg.caption_entities,
        })
    else:
        ctx.user_data.update({
            "bc_type":     "text",
            "bc_text":     msg.text,
            "bc_entities": msg.entities,
        })
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да", callback_data="bc_btn_yes"),
        InlineKeyboardButton("❌ Нет", callback_data="bc_btn_no"),
    ]])
    await msg.reply_text("Добавить кнопку к рассылке?", reply_markup=kb)
    return B_BTN_CHOICE


async def bc_btn_choice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "bc_btn_no":
        ctx.user_data["bc_button"]   = None
        ctx.user_data["bc_btn_text"] = None
        return await _bc_confirm_msg(q.message, ctx)
    await q.message.reply_text("✏️ Введите текст кнопки:")
    return B_BTN_TEXT


async def bc_btn_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["bc_btn_text"] = update.message.text.strip()
    await update.message.reply_text("🔗 Введите URL кнопки:")
    return B_BTN_URL


async def bc_btn_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    ctx.user_data["bc_button"] = InlineKeyboardMarkup([[
        InlineKeyboardButton(ctx.user_data["bc_btn_text"], url=url)
    ]])
    return await _bc_confirm_msg(update.message, ctx)


async def _bc_confirm_msg(message, ctx):
    with _conn() as c:
        count = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    btn_line = (
        f"🔘 Кнопка: {ctx.user_data.get('bc_btn_text')}\n"
        if ctx.user_data.get("bc_button") else ""
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚀 Отправить", callback_data="bc_send"),
        InlineKeyboardButton("❌ Отмена",    callback_data="bc_cancel"),
    ]])
    await message.reply_text(
        f"📢 Подтверждение рассылки\n\n"
        f"👥 Получателей: {count}\n"
        f"{btn_line}\n"
        f"Запустить рассылку?",
        reply_markup=kb,
    )
    return B_CONFIRM


async def bc_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "bc_cancel":
        await q.edit_message_text("❌ Рассылка отменена.")
        return ConversationHandler.END

    with _conn() as c:
        users = c.execute("SELECT user_id FROM users").fetchall()

    bc_type = ctx.user_data.get("bc_type")
    button  = ctx.user_data.get("bc_button")
    sent = failed = 0

    await q.edit_message_text("⏳ Рассылка запущена...")

    for row in users:
        uid = row["user_id"]
        try:
            if bc_type == "photo":
                await ctx.bot.send_photo(
                    uid,
                    photo=ctx.user_data["bc_file"],
                    caption=ctx.user_data.get("bc_caption"),
                    caption_entities=ctx.user_data.get("bc_caption_entities"),
                    reply_markup=button,
                )
            elif bc_type == "video":
                await ctx.bot.send_video(
                    uid,
                    video=ctx.user_data["bc_file"],
                    caption=ctx.user_data.get("bc_caption"),
                    caption_entities=ctx.user_data.get("bc_caption_entities"),
                    reply_markup=button,
                )
            else:
                await ctx.bot.send_message(
                    uid,
                    text=ctx.user_data["bc_text"],
                    entities=ctx.user_data.get("bc_entities"),
                    reply_markup=button,
                )
            sent += 1
        except Exception:
            failed += 1

    await ctx.bot.send_message(
        ADMIN_ID,
        text=f"✅ Рассылка завершена!\n\n✅ Успешно: {sent}\n❌ Ошибок: {failed}",
    )
    return ConversationHandler.END


async def bc_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Рассылка отменена.", reply_markup=MAIN_KB)
    return ConversationHandler.END


# ── Главное меню (текст) ──────────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "💰 Заработать":
        await earn(update, ctx)
    elif t == "📋 Задания":
        await tasks(update, ctx)
    elif t == "👤 Кабинет":
        await cabinet(update, ctx)
    elif t == "ℹ️ О боте":
        await about(update, ctx)


# ── MAIN ─────────────────────────────────────────────────────

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    withdraw_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cabinet_withdraw_cb, pattern=r"^cabinet_withdraw$"),
        ],
        states={
            W_AMOUNT:  [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount)],
            W_DETAILS: [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_details)],
            W_BANK:    [MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_bank)],
        },
        fallbacks=[
            CommandHandler("cancel", w_cancel),
            MessageHandler(filters.Regex(r"^❌ Отмена$"), w_cancel),
        ],
        allow_reentry=True,
    )

    broadcast_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(bc_start, pattern=r"^adm_broadcast$"),
        ],
        states={
            B_CONTENT: [MessageHandler(
                (filters.TEXT | filters.PHOTO | filters.VIDEO) & ~filters.COMMAND,
                bc_content,
            )],
            B_BTN_CHOICE: [CallbackQueryHandler(bc_btn_choice, pattern=r"^bc_btn_")],
            B_BTN_TEXT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, bc_btn_text)],
            B_BTN_URL:    [MessageHandler(filters.TEXT & ~filters.COMMAND, bc_btn_url)],
            B_CONFIRM:    [CallbackQueryHandler(bc_confirm, pattern=r"^bc_")],
        },
        fallbacks=[CommandHandler("cancel", bc_cancel)],
        allow_reentry=True,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("admin", cmd_admin))
    app.add_handler(withdraw_conv)
    app.add_handler(broadcast_conv)
    app.add_handler(CallbackQueryHandler(adm_withdrawals_cb, pattern=r"^adm_withdrawals$"))
    app.add_handler(CallbackQueryHandler(approve_reject_cb,  pattern=r"^(appr|rjct)_\d+$"))
    app.add_handler(CallbackQueryHandler(check_tgrass_cb,    pattern=r"^check_tgrass$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("✅ Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
