#!/usr/bin/env python3
# REFERRAL TELEGRAM BOT — с заданиями от Tgrass (3₽ за задание)

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
REWARD_PER_TASK     = 3        # ₽ за выполненную серию заданий
MIN_WITHDRAWAL      = 15
OFFERS_LIMIT        = 10       # Количество спонсоров за раз (максимум)

TGRASS_URL       = "https://tgrass.space/offers"
TGRASS_RESET_URL = "https://tgrass.space/reset_offers"
TGRASS_AUTH      = "aca6f2a2ad034cf5af45277689c2fa1e"
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
            CREATE TABLE IF NOT EXISTS completed_tasks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL UNIQUE,
                reward     REAL    NOT NULL,
                created_at TEXT    DEFAULT (datetime('now','localtime'))
            );
            CREATE TABLE IF NOT EXISTS stats (
                id              INTEGER PRIMARY KEY DEFAULT 1,
                total_withdrawn REAL DEFAULT 0
            );
            INSERT OR IGNORE INTO stats VALUES (1, 0);
        """)
        # Совместимость со старыми БД
        for col in ["tasks_done INTEGER DEFAULT 0"]:
            try:
                c.execute(f"ALTER TABLE users ADD COLUMN {col}")
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


def add_task_reward(uid: int, message_id: int) -> bool:
    """Начисляет REWARD_PER_TASK. Возвращает False если уже начислено по этому message_id."""
    with _conn() as c:
        try:
            c.execute(
                "INSERT INTO completed_tasks (user_id, message_id, reward) VALUES (?,?,?)",
                (uid, message_id, REWARD_PER_TASK),
            )
            c.execute(
                """UPDATE users
                   SET balance=balance+?,
                       total_earned=total_earned+?,
                       tasks_done=tasks_done+1
                   WHERE user_id=?""",
                (REWARD_PER_TASK, REWARD_PER_TASK, uid),
            )
            return True
        except sqlite3.IntegrityError:
            return False


# ── KEYBOARDS ────────────────────────────────────────────────

MAIN_KB = ReplyKeyboardMarkup(
    [
        [KeyboardButton("📋 Задания"), KeyboardButton("💰 Заработать")],
        [KeyboardButton("👤 Кабинет"), KeyboardButton("ℹ️ О боте")],
    ],
    resize_keyboard=True,
)

CANCEL_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True,
)


# ── TGRASS ───────────────────────────────────────────────────

async def _tgrass_get_offers(user, offers_limit: int = OFFERS_LIMIT):
    payload = {
        "tg_user_id":   int(user.id),
        "tg_login":     user.username or "",
        "lang":         getattr(user, "language_code", "ru") or "ru",
        "is_premium":   bool(getattr(user, "is_premium", False)),
        "offers_limit": offers_limit,
    }
    log.info(f"tgrass /offers [{user.id}]: {payload}")
    try:
        async with httpx.AsyncClient(verify=False, timeout=15.0) as client:
            resp = await client.post(
                TGRASS_URL,
                json=payload,
                headers={
                    "accept":       "application/json",
                    "Content-Type": "application/json",
                    "Auth":         TGRASS_AUTH,
                },
            )
        log.info(f"tgrass response [{user.id}]: {resp.status_code} | {resp.text[:400]}")
        try:
            data = resp.json()
        except Exception:
            data = None
        return resp.status_code, data
    except httpx.TimeoutException:
        log.warning(f"tgrass timeout [{user.id}]")
        return None, None
    except Exception as e:
        log.warning(f"tgrass exception [{user.id}]: {e}")
        return None, None


async def _tgrass_reset(uid: int):
    try:
        async with httpx.AsyncClient(verify=False, timeout=10.0) as client:
            await client.post(
                TGRASS_RESET_URL,
                json={"tg_user_id": uid},
                headers={
                    "accept":       "application/json",
                    "Content-Type": "application/json",
                    "Auth":         TGRASS_AUTH,
                },
            )
        log.info(f"tgrass reset [{uid}]: done")
    except Exception as e:
        log.warning(f"tgrass reset error [{uid}]: {e}")


def _build_offers_kb(offers: list, check_callback: str) -> InlineKeyboardMarkup:
    """Строит клавиатуру: каждый оффер отдельной кнопкой + кнопка проверки."""
    kb = []
    for offer in offers:
        name = offer.get("name") or "Канал"
        is_channel = offer.get("type") == "channel"
        subscribed = offer.get("subscribed", False)

        status_icon = "✅" if subscribed else ("📢" if is_channel else "🔗")
        action      = "Подписаться" if is_channel else "Перейти"
        btn_text    = f"{status_icon} {action} — {name}"

        link = offer.get("link") or ""
        if link:
            kb.append([InlineKeyboardButton(text=btn_text, url=link)])

    kb.append([InlineKeyboardButton(
        text=f"✅ Проверить подписку (+{REWARD_PER_TASK}₽)",
        callback_data=check_callback,
    )])
    return InlineKeyboardMarkup(kb)


def _offers_message(offers: list) -> str:
    total    = len(offers)
    done_cnt = sum(1 for o in offers if o.get("subscribed"))
    lines    = []
    for o in offers:
        icon = "✅" if o.get("subscribed") else "⬜"
        lines.append(f"{icon} {o.get('name') or 'Канал'}")

    return (
        f"📋 <b>Задания от спонсоров</b>\n\n"
        f"Подпишись на все каналы и нажми «Проверить».\n"
        f"💎 Награда: <b>{REWARD_PER_TASK}₽</b> за всю серию\n\n"
        f"📊 Прогресс: <b>{done_cnt}/{total}</b>\n"
        f"{'─' * 22}\n"
        + "\n".join(lines)
    )


# ── FINISH START ─────────────────────────────────────────────

async def _finish_start(bot, user, referrer_id):
    is_new = register_user(user.id, user.username, user.full_name, referrer_id)

    if is_new and referrer_id:
        referrer = get_user(referrer_id)
        if referrer:
            try:
                await bot.send_message(
                    chat_id=referrer_id,
                    text=(
                        f"🎉 По вашей ссылке зарегистрировался новый пользователь!\n\n"
                        f"👤 {user.full_name}\n"
                        f"💰 Начислено: +{REWARD_PER_REFERRAL}₽\n"
                        f"💵 Баланс: {referrer['balance'] + REWARD_PER_REFERRAL:.0f}₽"
                    ),
                )
            except Exception as e:
                log.warning(f"Не удалось уведомить реферера {referrer_id}: {e}")

    label = "С возвращением" if not is_new else "Добро пожаловать"
    await bot.send_message(
        chat_id=user.id,
        text=(
            f"👋 {label}, <b>{user.first_name}</b>!\n\n"
            f"🤑 Приглашай друзей — <b>{REWARD_PER_REFERRAL}₽</b> за каждого!\n"
            f"📋 Выполняй задания — <b>{REWARD_PER_TASK}₽</b> за серию спонсоров!\n\n"
            f"Выбери действие 👇"
        ),
        parse_mode="HTML",
        reply_markup=MAIN_KB,
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
    ctx.user_data["pending_referrer_id"] = referrer_id

    is_existing = bool(get_user(u.id))
    await update.message.reply_text("⏳ Проверяю доступ...")

    status_code, data = await _tgrass_get_offers(u)
    tg_status = data.get("status") if isinstance(data, dict) else None
    offers    = data.get("offers", []) if isinstance(data, dict) else []

    if status_code == 200 and tg_status == "not_ok" and offers:
        intro = (
            "Для начала работы подпишись на наших партнёров.\n\n"
            if not is_existing else
            "Появились новые задания! Подпишись, чтобы продолжить.\n\n"
        )
        await update.message.reply_text(
            f"📋 <b>Задания от спонсоров</b>\n\n{intro}После подписки нажми кнопку ниже 👇",
            parse_mode="HTML",
            reply_markup=_build_offers_kb(offers, "tgrass_check_start"),
        )
        return

    await _finish_start(ctx.bot, u, referrer_id)


# ── Проверка при старте ───────────────────────────────────────

async def tgrass_check_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("🔄 Проверяю подписку...")
    user = q.from_user

    if get_user(user.id):
        await q.message.reply_text(
            f"👋 С возвращением, <b>{user.first_name}</b>!\n\nВыбери действие 👇",
            parse_mode="HTML",
            reply_markup=MAIN_KB,
        )
        return

    status_code, data = await _tgrass_get_offers(user)
    tg_status = data.get("status") if isinstance(data, dict) else None
    offers    = data.get("offers", []) if isinstance(data, dict) else []

    if status_code == 200 and tg_status == "ok":
        referrer_id = ctx.user_data.get("pending_referrer_id")
        await _finish_start(ctx.bot, user, referrer_id)

    elif status_code == 200 and tg_status == "not_ok" and offers:
        await q.message.reply_text(
            "❌ <b>Ты ещё не подписался на всех партнёров.</b>\n\n"
            "Подпишись и нажми кнопку снова 👇",
            parse_mode="HTML",
            reply_markup=_build_offers_kb(offers, "tgrass_check_start"),
        )
    else:
        referrer_id = ctx.user_data.get("pending_referrer_id")
        await _finish_start(ctx.bot, user, referrer_id)


# ── 📋 ЗАДАНИЯ ────────────────────────────────────────────────

async def tasks(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    db_user    = get_user(u.id)
    tasks_done = db_user["tasks_done"] if db_user else 0

    await update.message.reply_text("⏳ Загружаю задания...")
    status_code, data = await _tgrass_get_offers(u, offers_limit=OFFERS_LIMIT)

    if status_code is None:
        await update.message.reply_text(
            "❌ Ошибка соединения с сервером. Попробуй позже."
        )
        return

    tg_status = data.get("status") if isinstance(data, dict) else None
    offers    = data.get("offers", []) if isinstance(data, dict) else []

    if tg_status == "no_offers":
        await update.message.reply_text(
            f"😔 <b>Новых заданий пока нет.</b>\n\n"
            f"✅ Всего выполнено серий: <b>{tasks_done}</b>\n\n"
            f"Задания обновляются регулярно — заходи позже!",
            parse_mode="HTML",
            reply_markup=MAIN_KB,
        )
        return

    if tg_status == "ok":
        await update.message.reply_text(
            f"✅ <b>Все текущие задания выполнены!</b>\n\n"
            f"Всего серий выполнено: <b>{tasks_done}</b>\n"
            f"Новые задания появятся позже — загляни снова!",
            parse_mode="HTML",
            reply_markup=MAIN_KB,
        )
        return

    if tg_status == "not_ok" and offers:
        await update.message.reply_text(
            _offers_message(offers),
            parse_mode="HTML",
            reply_markup=_build_offers_kb(offers, "tgrass_check_tasks"),
        )
        return

    await update.message.reply_text(
        "😔 Нет доступных заданий. Попробуй позже!",
        reply_markup=MAIN_KB,
    )


# ── Проверка заданий ──────────────────────────────────────────

async def tgrass_check_tasks_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer("🔄 Проверяю подписки...")
    user   = q.from_user
    msg_id = q.message.message_id

    status_code, data = await _tgrass_get_offers(user, offers_limit=OFFERS_LIMIT)
    tg_status = data.get("status") if isinstance(data, dict) else None
    offers    = data.get("offers", []) if isinstance(data, dict) else []

    if status_code == 200 and tg_status == "ok":
        rewarded = add_task_reward(user.id, msg_id)

        if rewarded:
            db_user     = get_user(user.id)
            new_balance = db_user["balance"]    if db_user else 0
            tasks_done  = db_user["tasks_done"] if db_user else 0

            await q.message.reply_text(
                f"🎉 <b>Задание выполнено!</b>\n\n"
                f"💰 Начислено: <b>+{REWARD_PER_TASK}₽</b>\n"
                f"💵 Текущий баланс: <b>{new_balance:.0f}₽</b>\n"
                f"✅ Серий выполнено: <b>{tasks_done}</b>\n\n"
                f"Нажми <b>📋 Задания</b> для следующей серии!",
                parse_mode="HTML",
                reply_markup=MAIN_KB,
            )
            await _tgrass_reset(user.id)   # сбрасываем таймер, чтобы сразу появились новые

        else:
            await q.message.reply_text(
                "⚠️ Награда за эту серию уже была выдана.\n"
                "Нажми <b>📋 Задания</b> для получения новой серии!",
                parse_mode="HTML",
                reply_markup=MAIN_KB,
            )

    elif status_code == 200 and tg_status == "not_ok" and offers:
        not_done = [o for o in offers if not o.get("subscribed")]
        await q.message.reply_text(
            f"❌ <b>Ещё не все подписки выполнены!</b>\n\n"
            f"Осталось: <b>{len(not_done)}</b>\n\n"
            + "\n".join(
                f"{'✅' if o.get('subscribed') else '❌'} {o.get('name') or 'Канал'}"
                for o in offers
            )
            + "\n\nПодпишись и нажми кнопку снова 👇",
            parse_mode="HTML",
            reply_markup=_build_offers_kb(offers, "tgrass_check_tasks"),
        )
    else:
        await q.message.reply_text(
            "❌ Ошибка проверки. Попробуй ещё раз позже.",
            reply_markup=MAIN_KB,
        )


# ── 💰 Заработать ────────────────────────────────────────────

async def earn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u = get_user(uid)
    bot_me    = await ctx.bot.get_me()
    ref_link  = f"https://t.me/{bot_me.username}?start=ref_{uid}"
    balance   = u["balance"]   if u else 0
    ref_count = u["ref_count"] if u else 0
    share_url = (
        f"https://t.me/share/url?url={ref_link}"
        f"&text=Зарабатывай+{REWARD_PER_REFERRAL}₽+за+каждого+друга!"
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📤 Поделиться ссылкой", url=share_url)
    ]])
    await update.message.reply_text(
        f"💰 <b>Реферальная программа</b>\n\n"
        f"🔗 Твоя ссылка:\n<code>{ref_link}</code>\n\n"
        f"👥 Приглашено рефералов: <b>{ref_count}</b>\n"
        f"💵 Баланс: <b>{balance:.0f}₽</b>\n\n"
        f"💡 За каждого приглашённого — <b>{REWARD_PER_REFERRAL}₽</b> на баланс",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── 👤 Кабинет ───────────────────────────────────────────────

async def cabinet(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    u   = get_user(uid)
    balance    = u["balance"]      if u else 0
    ref_count  = u["ref_count"]    if u else 0
    earned     = u["total_earned"] if u else 0
    tasks_done = u["tasks_done"]   if u else 0

    if balance >= MIN_WITHDRAWAL:
        action_btn = [InlineKeyboardButton("💳 Вывести", callback_data="cabinet_withdraw")]
    else:
        need = MIN_WITHDRAWAL - balance
        action_btn = [InlineKeyboardButton(
            f"🔒 Вывод от {MIN_WITHDRAWAL}₽ (нужно ещё {need:.0f}₽)",
            callback_data="cabinet_noop",
        )]

    await update.message.reply_text(
        f"👤 <b>Личный кабинет</b>\n\n"
        f"💵 Баланс: <b>{balance:.0f}₽</b>\n"
        f"💸 Всего заработано: <b>{earned:.0f}₽</b>\n"
        f"👥 Рефералов: <b>{ref_count}</b>\n"
        f"✅ Заданий выполнено: <b>{tasks_done}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([action_btn]),
    )


async def cabinet_noop_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer(
        f"Минимум для вывода — {MIN_WITHDRAWAL}₽", show_alert=True
    )


async def cabinet_withdraw_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid     = q.from_user.id
    u       = get_user(uid)
    balance = u["balance"] if u else 0

    if balance < MIN_WITHDRAWAL:
        await q.answer(f"Минимум {MIN_WITHDRAWAL}₽ для вывода", show_alert=True)
        return W_AMOUNT

    await q.message.reply_text(
        f"💳 <b>Вывод средств</b>\n\n"
        f"💵 Баланс: <b>{balance:.0f}₽</b>\n"
        f"📊 Минимум: <b>{MIN_WITHDRAWAL}₽</b>\n\n"
        f"Введите сумму вывода:",
        parse_mode="HTML",
        reply_markup=CANCEL_KB,
    )
    return W_AMOUNT


# ── 💳 Вывод средств ─────────────────────────────────────────

W_AMOUNT, W_DETAILS, W_BANK = range(3)


async def withdraw_amount(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    uid = update.effective_user.id
    u   = get_user(uid)
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
            f"❌ Минимальная сумма: <b>{MIN_WITHDRAWAL}₽</b>\n\nВведите другую сумму:",
            parse_mode="HTML",
        )
        return W_AMOUNT

    if amount > balance:
        await update.message.reply_text(
            f"❌ Недостаточно средств.\n"
            f"💵 Баланс: <b>{balance:.0f}₽</b>\n\nВведите другую сумму:",
            parse_mode="HTML",
        )
        return W_AMOUNT

    ctx.user_data["w_amount"] = amount
    await update.message.reply_text(
        f"✅ Сумма: <b>{amount:.0f}₽</b>\n\n"
        f"💳 Введите реквизиты (номер карты или телефона):",
        parse_mode="HTML",
    )
    return W_DETAILS


async def withdraw_details(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    ctx.user_data["w_details"] = text
    await update.message.reply_text(
        "🏦 Укажите банк получателя:\n(например: Сбербанк, Тинькофф, ВТБ)"
    )
    return W_BANK


async def withdraw_bank(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if text == "❌ Отмена":
        await update.message.reply_text("❌ Отменено.", reply_markup=MAIN_KB)
        return ConversationHandler.END

    uid     = update.effective_user.id
    u       = get_user(uid)
    amount  = ctx.user_data.get("w_amount", 0)
    details = ctx.user_data.get("w_details", "")
    bank    = text

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
        f"✅ <b>Заявка #{wid} принята!</b>\n\n"
        f"💵 Сумма: <b>{amount:.0f}₽</b>\n"
        f"💳 Реквизиты: <code>{details}</code>\n"
        f"🏦 Банк: {bank}\n\n"
        f"⏳ Ожидайте обработки администратором.",
        parse_mode="HTML",
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
                f"💳 <b>Новая заявка #{wid}</b>\n\n"
                f"👤 {full_name}\n"
                f"📱 Username: {uname_str}\n"
                f"🆔 ID: {uid}\n\n"
                f"💵 Сумма: <b>{amount:.0f}₽</b>\n"
                f"💳 Реквизиты: <code>{details}</code>\n"
                f"🏦 Банк: {bank}"
            ),
            parse_mode="HTML",
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
        tasks_total = c.execute("SELECT COUNT(*) FROM completed_tasks").fetchone()[0]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Канал с выплатами", url=CHANNEL_LINK)],
        [InlineKeyboardButton("👨‍💼 Администратор", url=f"https://t.me/{ADMIN_USERNAME}")],
    ])
    await update.message.reply_text(
        f"ℹ️ <b>О боте</b>\n\n"
        f"📊 <b>Статистика:</b>\n"
        f"├ 👥 Всего пользователей: <b>{total_users}</b>\n"
        f"├ 📅 Новых сегодня: <b>{today_users}</b>\n"
        f"├ ✅ Заданий выполнено: <b>{tasks_total}</b>\n"
        f"└ 💸 Всего выплачено: <b>{total_withdrawn:.0f}₽</b>\n\n"
        f"💡 Реферал — <b>{REWARD_PER_REFERRAL}₽</b> | Задание — <b>{REWARD_PER_TASK}₽</b>",
        parse_mode="HTML",
        reply_markup=kb,
    )


# ── Одобрить / Отклонить вывод ───────────────────────────────

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
            await q.answer("ℹ️ Уже обработана", show_alert=True)
            return

        if action == "appr":
            c.execute("UPDATE withdrawals SET status='approved' WHERE id=?", (wid,))
            c.execute(
                "UPDATE stats SET total_withdrawn=total_withdrawn+? WHERE id=1",
                (w["amount"],),
            )
            verdict  = "✅ ОДОБРЕНО"
            user_msg = (
                f"✅ <b>Заявка #{wid} одобрена!</b>\n\n"
                f"💵 Сумма: <b>{w['amount']:.0f}₽</b>\n"
                f"💳 Реквизиты: <code>{w['details']}</code>\n"
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
                f"❌ <b>Заявка #{wid} отклонена.</b>\n\n"
                f"💵 Сумма <b>{w['amount']:.0f}₽</b> возвращена на баланс.\n"
                f"По вопросам обратитесь к администратору."
            )

    try:
        await q.edit_message_text(
            q.message.text + f"\n\n— {verdict}", parse_mode="HTML"
        )
    except Exception:
        pass
    try:
        await ctx.bot.send_message(
            chat_id=w["user_id"], text=user_msg, parse_mode="HTML"
        )
    except Exception:
        pass


# ── /admin ───────────────────────────────────────────────────

async def cmd_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    today = date.today().isoformat()
    with _conn() as c:
        total       = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        today_u     = c.execute(
            "SELECT COUNT(*) FROM users WHERE DATE(joined_at)=?", (today,)
        ).fetchone()[0]
        pending     = c.execute(
            "SELECT COUNT(*) FROM withdrawals WHERE status='pending'"
        ).fetchone()[0]
        total_w     = c.execute(
            "SELECT total_withdrawn FROM stats WHERE id=1"
        ).fetchone()[0]
        tasks_all   = c.execute("SELECT COUNT(*) FROM completed_tasks").fetchone()[0]
        tasks_today = c.execute(
            "SELECT COUNT(*) FROM completed_tasks WHERE DATE(created_at)=?", (today,)
        ).fetchone()[0]

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Рассылка", callback_data="adm_broadcast")],
        [InlineKeyboardButton(
            f"📋 Заявки на вывод ({pending})", callback_data="adm_withdrawals"
        )],
    ])
    await update.message.reply_text(
        f"👨‍💼 <b>Панель администратора</b>\n\n"
        f"👥 Пользователей: <b>{total}</b>\n"
        f"🆕 Новых сегодня: <b>{today_u}</b>\n"
        f"✅ Заданий всего: <b>{tasks_all}</b>\n"
        f"✅ Заданий сегодня: <b>{tasks_today}</b>\n"
        f"💸 Выплачено: <b>{total_w:.0f}₽</b>\n"
        f"⏳ Заявок в очереди: <b>{pending}</b>",
        parse_mode="HTML",
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
            f"💳 <b>Заявка #{w['id']}</b>\n\n"
            f"👤 {w['full_name']} ({uname})\n"
            f"🆔 {w['user_id']}\n\n"
            f"💵 Сумма: <b>{w['amount']:.0f}₽</b>\n"
            f"💳 Реквизиты: <code>{w['details']}</code>\n"
            f"🏦 Банк: {w['bank']}\n"
            f"📅 {w['created_at'][:16]}",
            parse_mode="HTML",
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
        "📢 <b>Создание рассылки</b>\n\n"
        "Отправьте контент:\n• Текст\n• Фото с подписью\n• Видео с подписью\n\n"
        "Для отмены: /cancel",
        parse_mode="HTML",
    )
    return B_CONTENT


async def bc_content(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    msg = update.message
    if msg.photo:
        ctx.user_data.update({
            "bc_type":    "photo", "bc_file": msg.photo[-1].file_id,
            "bc_caption": msg.caption, "bc_caption_entities": msg.caption_entities,
        })
    elif msg.video:
        ctx.user_data.update({
            "bc_type":    "video", "bc_file": msg.video.file_id,
            "bc_caption": msg.caption, "bc_caption_entities": msg.caption_entities,
        })
    else:
        ctx.user_data.update({
            "bc_type": "text", "bc_text": msg.text, "bc_entities": msg.entities,
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
        ctx.user_data["bc_button"] = None
        return await _bc_confirm_msg(q.message, ctx)
    await q.message.reply_text("✏️ Введите текст кнопки:")
    return B_BTN_TEXT


async def bc_btn_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["bc_btn_text"] = update.message.text.strip()
    await update.message.reply_text("🔗 Введите URL кнопки:")
    return B_BTN_URL


async def bc_btn_url(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["bc_button"] = InlineKeyboardMarkup([[
        InlineKeyboardButton(ctx.user_data["bc_btn_text"], url=update.message.text.strip())
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
        f"📢 <b>Подтверждение рассылки</b>\n\n"
        f"👥 Получателей: <b>{count}</b>\n{btn_line}\nЗапустить?",
        parse_mode="HTML", reply_markup=kb,
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
                    uid, photo=ctx.user_data["bc_file"],
                    caption=ctx.user_data.get("bc_caption"),
                    caption_entities=ctx.user_data.get("bc_caption_entities"),
                    reply_markup=button,
                )
            elif bc_type == "video":
                await ctx.bot.send_video(
                    uid, video=ctx.user_data["bc_file"],
                    caption=ctx.user_data.get("bc_caption"),
                    caption_entities=ctx.user_data.get("bc_caption_entities"),
                    reply_markup=button,
                )
            else:
                await ctx.bot.send_message(
                    uid, text=ctx.user_data["bc_text"],
                    entities=ctx.user_data.get("bc_entities"),
                    reply_markup=button,
                )
            sent += 1
        except Exception:
            failed += 1

    await ctx.bot.send_message(
        ADMIN_ID,
        text=(
            f"✅ <b>Рассылка завершена!</b>\n\n"
            f"✅ Успешно: <b>{sent}</b>\n❌ Ошибок: <b>{failed}</b>"
        ),
        parse_mode="HTML",
    )
    return ConversationHandler.END


async def bc_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Рассылка отменена.", reply_markup=MAIN_KB)
    return ConversationHandler.END


# ── Роутер текстовых кнопок ──────────────────────────────────

async def on_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "📋 Задания":
        await tasks(update, ctx)
    elif t == "💰 Заработать":
        await earn(update, ctx)
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
    app.add_handler(CallbackQueryHandler(tgrass_check_start_cb, pattern=r"^tgrass_check_start$"))
    app.add_handler(CallbackQueryHandler(tgrass_check_tasks_cb, pattern=r"^tgrass_check_tasks$"))
    app.add_handler(CallbackQueryHandler(adm_withdrawals_cb,    pattern=r"^adm_withdrawals$"))
    app.add_handler(CallbackQueryHandler(approve_reject_cb,     pattern=r"^(appr|rjct)_\d+$"))
    app.add_handler(CallbackQueryHandler(cabinet_noop_cb,       pattern=r"^cabinet_noop$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    log.info("✅ Бот запущен.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
