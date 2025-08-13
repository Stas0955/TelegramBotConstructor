import yaml
import os
import asyncio
import sys
import sqlite3
import logging
from html import escape
from datetime import datetime, time
from typing import List, Dict, Set, Optional, Callable, Awaitable, Any, Union

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import Command, CommandObject 
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
    InputMediaPhoto
)
from aiogram.enums import ChatAction
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot_running = True
bot_task = None

class BlockCheckMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
        event: Union[Message, CallbackQuery],
        data: Dict[str, Any]
    ) -> Any:
        if is_user_blocked(event.from_user.id):

            blocked_msg = config.get(
                "blocked_message",
                {
                    "text": "⛔ Вы заблокированы!",
                    "parse_mode": "HTML"
                }
            )

            if isinstance(blocked_msg, str):
                blocked_msg = {"text": blocked_msg, "parse_mode": "HTML"}
            elif "parse_mode" not in blocked_msg:
                blocked_msg["parse_mode"] = "HTML"

            if isinstance(event, Message):
                await event.answer(**blocked_msg)
            elif isinstance(event, CallbackQuery):
                await event.message.answer(**blocked_msg)
                await event.answer()

            return

        return await handler(event, data)

dp = Dispatcher()

dp.message.middleware(BlockCheckMiddleware())
dp.callback_query.middleware(BlockCheckMiddleware())

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

class RefundStates(StatesGroup):
    waiting_confirmation = State()    

with open("config.yml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

try:
    with open("auto_message.yml", "r", encoding="utf-8") as f:
        auto_messages = yaml.safe_load(f) or {}
        scheduled_messages = auto_messages.get("scheduled", {})
        template_messages = auto_messages.get("templates", {})
except FileNotFoundError:
    logger.warning("Файл auto_message.yml не найден, рассылка отключена")
    scheduled_messages = {}
    template_messages = {}

bot = Bot(
    token=config["bot"]["token"],
    default=DefaultBotProperties(parse_mode="HTML")
)

DB_PATH = "users.db"

def init_users_files():
    """Инициализация базы SQLite для хранения пользователей"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            chat_id INTEGER PRIMARY KEY
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blocked_users (
            chat_id INTEGER PRIMARY KEY
        )
    """)

    conn.commit()
    conn.close()

def save_user(chat_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Сохраняем пользователя, если он не заблокирован"""
    if is_user_blocked(chat_id):
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def get_all_users() -> List[int]:
    """Список всех активных пользователей (не заблокированных)"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT chat_id FROM users
        WHERE chat_id NOT IN (SELECT chat_id FROM blocked_users)
    """)
    rows = cur.fetchall()
    conn.close()
    return [row[0] for row in rows]

def get_active_users_count() -> int:
    """Количество активных пользователей"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM users
        WHERE chat_id NOT IN (SELECT chat_id FROM blocked_users)
    """)
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_total_users_count() -> int:
    count = 0
    if os.path.exists("users.txt"):
        with open("users.txt", "r", encoding="utf-8") as f:
            count = sum(1 for line in f if line.strip())
    return count

def get_total_users_count() -> int:
    """Общее количество пользователей"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM users")
    count = cur.fetchone()[0]
    conn.close()
    return count

def get_blocked_users_count() -> int:
    """Количество заблокированных пользователей"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM blocked_users")
    count = cur.fetchone()[0]
    conn.close()
    return count

def block_user(chat_id: int):
    """Блокировка пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("INSERT OR IGNORE INTO blocked_users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()
    conn.close()

def unblock_user(chat_id: int):
    """Разблокировка пользователя"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM blocked_users WHERE chat_id = ?", (chat_id,))
    conn.commit()
    conn.close()

def is_user_blocked(chat_id: int) -> bool:
    """Проверка блокировки"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM blocked_users WHERE chat_id = ?", (chat_id,))
    result = cur.fetchone()
    conn.close()
    return result is not None

async def check_user_blocked_middleware(handler: Callable[[Message, Dict[str, Any]], Awaitable[Any]],
                                      event: Message,
                                      data: Dict[str, Any]) -> Any:
    """Middleware для проверки блокировки пользователя"""
    if is_user_blocked(event.from_user.id):
        blocked_message = config.get("blocked_message", 
                                   {"text": "⛔ Вы заблокированы и не можете взаимодействовать с ботом"})
        await event.answer(**blocked_message)
        return
    return await handler(event, data)

init_users_files()

def format_html_description(text: str) -> str:
    """Форматирует описание для send_invoice с поддержкой HTML-тегов"""
    replacements = {
        '<b>': '__B_OPEN__', '</b>': '__B_CLOSE__',
        '<i>': '__I_OPEN__', '</i>': '__I_CLOSE__',
        '<u>': '__U_OPEN__', '</u>': '__U_CLOSE__',
        '<s>': '__S_OPEN__', '</s>': '__S_CLOSE__',
        '<code>': '__CODE_OPEN__', '</code>': '__CODE_CLOSE__',
        '<pre>': '__PRE_OPEN__', '</pre>': '__PRE_CLOSE__',
        '<blockquote>': '__BQ_OPEN__', '</blockquote>': '__BQ_CLOSE__'
    }
    for original, temp in replacements.items():
        text = text.replace(original, temp)

    text = escape(text)  

    for original, temp in replacements.items():
        text = text.replace(temp, original)

    return text

def get_reply_keyboard(buttons):
    if not buttons:
        return None

    keyboard = []

    if isinstance(buttons, list) and any(isinstance(row, list) for row in buttons):
        for row in buttons:
            if isinstance(row, list):
                keyboard_row = []
                for btn in row:
                    if isinstance(btn, str):
                        keyboard_row.append(KeyboardButton(text=btn))
                if keyboard_row:
                    keyboard.append(keyboard_row)
            elif isinstance(row, str):
                keyboard.append([KeyboardButton(text=row)])

    elif isinstance(buttons, list):
        for btn in buttons:
            if isinstance(btn, str):
                keyboard.append([KeyboardButton(text=btn)])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_inline_keyboard(buttons):
    if not buttons:
        return None

    keyboard = []
    payments_cfg = config.get("payments", {})

    if isinstance(buttons, list) and any(isinstance(row, list) for row in buttons):
        for row in buttons:
            if isinstance(row, list):
                keyboard_row = []
                for btn in row:
                    if isinstance(btn, dict):
                        if "url" in btn:
                            keyboard_row.append(InlineKeyboardButton(text=btn["text"], url=btn["url"]))
                        elif "callback_data" in btn:
                            keyboard_row.append(InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]))
                    elif isinstance(btn, str):
                        if btn in payments_cfg:
                            pay_cfg = payments_cfg[btn]
                            keyboard_row.append(
                                InlineKeyboardButton(
                                    text=pay_cfg.get("title", "Оплатить"),
                                    pay=True
                                )
                            )
                        else:
                            keyboard_row.append(InlineKeyboardButton(text=btn, callback_data=btn))
                if keyboard_row:
                    keyboard.append(keyboard_row)
            elif isinstance(row, str):
                if row in payments_cfg:
                    pay_cfg = payments_cfg[row]
                    keyboard.append([
                        InlineKeyboardButton(
                            text=pay_cfg.get("title", "Оплатить"),
                            pay=True
                        )
                    ])
                else:
                    keyboard.append([InlineKeyboardButton(text=row, callback_data=row)])

    elif isinstance(buttons, list):
        keyboard_row = []
        for btn in buttons:
            if isinstance(btn, dict):
                if "url" in btn:
                    keyboard_row.append(InlineKeyboardButton(text=btn["text"], url=btn["url"]))
                elif "callback_data" in btn:
                    keyboard_row.append(InlineKeyboardButton(text=btn["text"], callback_data=btn["callback_data"]))
            elif isinstance(btn, str):
                if btn in payments_cfg:
                    pay_cfg = payments_cfg[btn]
                    keyboard_row.append(
                        InlineKeyboardButton(
                            text=pay_cfg.get("title", "Оплатить"),
                            pay=True
                        )
                    )
                else:
                    keyboard_row.append(InlineKeyboardButton(text=btn, callback_data=btn))
        if keyboard_row:
            keyboard.append(keyboard_row)

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

@dp.pre_checkout_query()
async def process_pre_checkout_query(pre_checkout_query: types.PreCheckoutQuery):
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)

@dp.message(F.successful_payment)
async def successful_payment_handler(message: types.Message):
    payload = message.successful_payment.invoice_payload
    payments_cfg = config.get("payments", {})

    for name, pay_cfg in payments_cfg.items():
        if pay_cfg.get("payload") == payload:

            await message.answer(
                pay_cfg.get("successful_msg", "Спасибо за оплату!"),
                reply_markup=get_inline_keyboard(pay_cfg.get("inline_buttons"))
            )
            break

async def send_response(chat_id: int, data: dict):
    payments_cfg = config.get("payments", {})

    if "backup" in data:
        await asyncio.sleep(data["backup"])

    if "backup_print" in data:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(data["backup_print"])

    text = data.get("text", "").strip()
    reply_markup = None

    if "inline_buttons" in data:
        for btn in (data["inline_buttons"] if isinstance(data["inline_buttons"], list) else []):
            if isinstance(btn, str) and btn in payments_cfg:
                pay_cfg = payments_cfg[btn]
                await bot.send_invoice(
                    chat_id=chat_id,
                    title=pay_cfg["title"],
                    description=format_html_description(pay_cfg["description"]),
                    payload=pay_cfg["payload"],
                    currency="XTR",  
                    prices=[types.LabeledPrice(label=pay_cfg["title"], amount=pay_cfg["stars"])],
                    start_parameter="star_payment",
                    reply_markup=get_inline_keyboard([[btn]])
                )
                return  

        reply_markup = get_inline_keyboard(data["inline_buttons"])

    elif "reply_buttons" in data:
        reply_markup = get_reply_keyboard(data["reply_buttons"])

    if "image" in data and os.path.exists(data["image"]):
        await bot.send_photo(
            chat_id=chat_id,
            photo=FSInputFile(data["image"]),
            caption=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

    elif text:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode="HTML"
        )

async def process_command(chat_id: int, command_data):
    if isinstance(command_data, list):
        for message_data in command_data:
            await send_response(chat_id, message_data)
    else:
        await send_response(chat_id, command_data)

async def interval_broadcast(interval: int, message_data: dict):
    while bot_running:
        try:
            users = get_all_users()
            logger.info(f"Начинаем интервальную рассылку для {len(users)} пользователей")

            for chat_id in users:
                try:
                    await process_command(chat_id, message_data)
                    logger.debug(f"Сообщение отправлено в {chat_id}")
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Ошибка отправки в {chat_id}: {str(e)}")

            logger.info(f"Интервальная рассылка завершена. Ожидаем {interval} сек.")
            for _ in range(interval):
                if not bot_running:
                    break
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Критическая ошибка в интервальной рассылке: {str(e)}")
            await asyncio.sleep(60)

async def time_broadcast(broadcast_time: str, message_data: dict):
    while bot_running:
        try:
            now = datetime.now().time()
            target_time = time.fromisoformat(broadcast_time)

            now_datetime = datetime.now()
            target_datetime = datetime.combine(now_datetime.date(), target_time)

            if now > target_time:
                target_datetime = target_datetime.replace(day=target_datetime.day + 1)

            wait_seconds = (target_datetime - now_datetime).total_seconds()
            logger.info(f"Следующая рассылка в {broadcast_time} через {wait_seconds:.0f} секунд")

            for _ in range(int(wait_seconds)):
                if not bot_running:
                    return
                await asyncio.sleep(1)

            users = get_all_users()
            logger.info(f"Начинаем рассылку в {broadcast_time} для {len(users)} пользователей")

            for chat_id in users:
                try:
                    await process_command(chat_id, message_data)
                    await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"Ошибка отправки в {chat_id}: {str(e)}")

            logger.info(f"Рассылка в {broadcast_time} завершена")

        except Exception as e:
            logger.error(f"Критическая ошибка во временной рассылке: {str(e)}")
            await asyncio.sleep(60)

async def setup_broadcasts():
    if not scheduled_messages:
        logger.info("Нет сообщений для автоматической рассылки")
        return

    for message_name, message_config in scheduled_messages.items():
        try:
            if "interval" in message_config:
                asyncio.create_task(
                    interval_broadcast(message_config["interval"], message_config["message"])
                )
                logger.info(f"Запущена интервальная рассылка '{message_name}' каждые {message_config['interval']} секунд")

            elif "time" in message_config:
                asyncio.create_task(
                    time_broadcast(message_config["time"], message_config["message"])
                )
                logger.info(f"Запущена временная рассылка '{message_name}' в {message_config['time']}")

            else:
                logger.warning(f"Неизвестный тип рассылки для сообщения '{message_name}'")

        except Exception as e:
            logger.error(f"Ошибка настройки рассылки '{message_name}': {str(e)}")

@dp.message(Command("m"))
async def cmd_template_message(message: types.Message):
    if not (config.get("admin_ids") and message.from_user.id in config["admin_ids"]):
        await message.answer("⛔ У вас нет прав для этой команды")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        if not template_messages:
            await message.answer("ℹ️ Нет доступных шаблонов сообщений")
            return

        templates_list = "\n".join([f"• <code>{name}</code>" for name in template_messages.keys()])
        await message.answer(
            "📝 Доступные шаблоны сообщений:\n"
            f"{templates_list}\n\n"
            "Используйте: <code>/m название_шаблона</code>",
            parse_mode="HTML"
        )
        return

    template_name = args[1]
    if template_name not in template_messages:
        await message.answer(f"❌ Шаблон <code>{template_name}</code> не найден", parse_mode="HTML")
        return

    template_data = template_messages[template_name]

    if "broadcast" in template_data:
        broadcast_config = template_data["broadcast"]
        message_data = template_data["message"] if "message" in template_data else template_data

        if "interval" in broadcast_config:
            info_text = f"🔹 Интервал: каждые {broadcast_config['interval']} секунд"
        elif "time" in broadcast_config:
            info_text = f"🔹 Время рассылки: {broadcast_config['time']}"
        else:
            info_text = "🔹 Однократная рассылка"
    else:
        message_data = template_data
        info_text = "🔹 Однократная рассылка"

    users = get_all_users()
    total_users = len(users)

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Начать рассылку", callback_data=f"broadcast_confirm:{template_name}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="broadcast_cancel")]
    ])

    preview_text = message_data.get("text", "[Сообщение без текста]")
    preview_text = preview_text.replace("<", "&lt;").replace(">", "&gt;")[:200]  

    await message.answer(
        f"📨 Подтвердите рассылку шаблона <b>{template_name}</b>\n"
        f"{info_text}\n"
        f"🔹 Пользователей: {total_users}\n"
        f"🔹 Предпросмотр: {preview_text}...\n\n"
        "Вы уверены, что хотите начать рассылку?",
        reply_markup=confirm_keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("broadcast_confirm:"))
async def confirm_broadcast(callback: types.CallbackQuery):
    template_name = callback.data.split(":")[1]

    if template_name not in template_messages:
        await callback.answer("❌ Шаблон больше не существует")
        return

    template_data = template_messages[template_name]

    if "broadcast" in template_data:
        broadcast_config = template_data["broadcast"]
        message_data = template_data["message"] if "message" in template_data else template_data

        if "interval" in broadcast_config:

            asyncio.create_task(interval_broadcast(broadcast_config["interval"], message_data))
            await callback.message.edit_text(
                f"🔄 Запущена интервальная рассылка шаблона '<b>{template_name}</b>'\n"
                f"🔹 Интервал: каждые {broadcast_config['interval']} секунд",
                parse_mode="HTML"
            )
        elif "time" in broadcast_config:

            asyncio.create_task(time_broadcast(broadcast_config["time"], message_data))
            await callback.message.edit_text(
                f"🕒 Запущена временная рассылка шаблона '<b>{template_name}</b>'\n"
                f"🔹 Время рассылки: {broadcast_config['time']}",
                parse_mode="HTML"
            )
        else:

            await send_template_to_all_users(template_name, message_data, callback.message)
    else:

        await send_template_to_all_users(template_name, template_data, callback.message)

    await callback.answer()

async def prepare_message_data(message_data: dict) -> dict:
    """Подготавливает данные сообщения, идентично scheduled рассылкам"""

    prepared_data = message_data.copy()

    if 'text' in prepared_data:
        text = prepared_data['text']

        replacements = {
            '<b>': '__TAG_B_OPEN__', '</b>': '__TAG_B_CLOSE__',
            '<i>': '__TAG_I_OPEN__', '</i>': '__TAG_I_CLOSE__',
            '<u>': '__TAG_U_OPEN__', '</u>': '__TAG_U_CLOSE__',
            '<s>': '__TAG_S_OPEN__', '</s>': '__TAG_S_CLOSE__',
            '<code>': '__TAG_CODE_OPEN__', '</code>': '__TAG_CODE_CLOSE__',
            '<pre>': '__TAG_PRE_OPEN__', '</pre>': '__TAG_PRE_CLOSE__',
            '<blockquote>': '__TAG_BQ_OPEN__', '</blockquote>': '__TAG_BQ_CLOSE__'
        }

        for original, temp in replacements.items():
            text = text.replace(original, temp)

        text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

        for original, temp in replacements.items():
            text = text.replace(temp, original)

        prepared_data['text'] = text
        prepared_data['parse_mode'] = 'HTML'

    return prepared_data

async def send_template_to_all_users(template_name: str, message_data: dict, message: types.Message):
    """Отправляет шаблон всем пользователям, идентично scheduled"""
    users = get_all_users()
    total_users = len(users)

    await message.edit_text(f"⏳ Начинаю рассылку шаблона '<b>{template_name}</b>'...", parse_mode="HTML")

    success = 0
    failed = 0

    for i, chat_id in enumerate(users, 1):
        try:

            prepared_data = await prepare_message_data(message_data)

            if 'image' in prepared_data and os.path.exists(prepared_data['image']):
                media = InputMediaPhoto(
                    media=FSInputFile(prepared_data['image']),
                    caption=prepared_data.get('text', ''),
                    parse_mode='HTML'
                )
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=media.media,
                    caption=media.caption,
                    reply_markup=get_reply_keyboard(prepared_data.get('reply_buttons')) or 
                              get_inline_keyboard(prepared_data.get('inline_buttons'))
                )
            elif prepared_data.get('text'):
                await bot.send_message(
                    chat_id=chat_id,
                    text=prepared_data['text'],
                    reply_markup=get_reply_keyboard(prepared_data.get('reply_buttons')) or 
                              get_inline_keyboard(prepared_data.get('inline_buttons')),
                    parse_mode='HTML'
                )

            success += 1

            if i % 10 == 0:
                await message.edit_text(
                    f"📨 Рассылка шаблона '<b>{template_name}</b>'\n"
                    f"✅ Успешно: {success}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"🔹 Всего: {i}/{total_users}",
                    parse_mode="HTML"
                )

            await asyncio.sleep(0.1)
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки в {chat_id}: {str(e)}")

    await message.edit_text(
        f"✅ Рассылка шаблона '<b>{template_name}</b>' завершена:\n"
        f"• Успешно: {success}\n"
        f"• Не удалось: {failed}\n"
        f"• Всего: {total_users}",
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()

@dp.message(Command("refund"))
async def cmd_refund(message: types.Message, command: CommandObject, state: FSMContext):
    """Обработка команды возврата средств"""

    refund_config = config.get("refund", {})

    if not refund_config.get("enabled", True):
        await message.answer(refund_config.get("disabled_message", "⛔ Возвраты временно отключены"))
        return

    if refund_config.get("admin_only", False):
        if not (config.get("admin_ids") and message.from_user.id in config["admin_ids"]):
            await message.answer("⛔ Эта команда доступна только администраторам")
            return

    if not command.args:
        await message.answer("ℹ️ Используйте: /refund <ID_транзакции>")
        return

    transaction_id = command.args.strip()

    await state.update_data(transaction_id=transaction_id)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да", callback_data="refund_confirm"),
            InlineKeyboardButton(text="❌ Нет", callback_data="refund_cancel")
        ]
    ])

    await message.answer(
        "Вы уверены, что хотите вернуть оплату?",
        reply_markup=confirm_kb
    )

    await state.set_state(RefundStates.waiting_confirmation)

@dp.callback_query(F.data == "refund_confirm", RefundStates.waiting_confirmation)
async def confirm_refund(callback: types.CallbackQuery, state: FSMContext):
    """Обработка подтверждения возврата с проверкой на уже возвращенные платежи"""
    data = await state.get_data()
    transaction_id = data.get("transaction_id")

    try:

        await bot.refund_star_payment(
            user_id=callback.from_user.id,
            telegram_payment_charge_id=transaction_id
        )

        await callback.message.edit_text(
            "✅ Возврат успешно выполнен",
            reply_markup=None
        )

    except Exception as e:
        error_msg = str(e)

        if any(phrase in error_msg for phrase in [
            "CHARGE_ALREADY_REFUNDED",
            "already refunded",
            "уже возвращен"
        ]):
            await callback.message.edit_text(
                "ℹ️ Этот платеж уже был возвращен ранее",
                reply_markup=None
            )
        else:
            await callback.message.edit_text(
                f"❌ Ошибка при возврате: {error_msg}",
                reply_markup=None
            )

    await state.clear()

@dp.callback_query(F.data == "refund_cancel", RefundStates.waiting_confirmation)
async def cancel_refund(callback: types.CallbackQuery, state: FSMContext):
    """Отмена возврата"""
    await callback.message.edit_text(
        "❌ Возврат отменен",
        reply_markup=None
    )
    await state.clear()

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    save_user(
        message.chat.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )

    if "/start" in config["commands"]:
        await process_command(message.chat.id, config["commands"]["/start"])

def register_commands():
    for cmd in config["commands"]:
        if cmd.startswith('/'):
            cmd_name = cmd[1:]  

            async def command_handler(message: types.Message, cmd=cmd):
                save_user(
                    message.chat.id,
                    message.from_user.username,
                    message.from_user.first_name,
                    message.from_user.last_name
                )
                await process_command(message.chat.id, config["commands"][cmd])

            dp.message.register(command_handler, Command(cmd_name))

register_commands()

@dp.message(Command("msg"))
async def cmd_msg(message: types.Message, state: FSMContext):
    if config.get("admin_ids") and message.from_user.id in config["admin_ids"]:
        await message.answer(
            "📢 Введите сообщение для рассылки:\n"
            "• Можно отправить текст с форматированием\n"
            "• Или фото с подписью\n"
            "❌ Для отмены отправьте /cancel"
        )

        await state.set_state(BroadcastStates.waiting_for_message)
    else:
        await message.answer("⛔ У вас нет прав для этой команды")

@dp.message(Command("cancel"), BroadcastStates.waiting_for_message)
async def cancel_broadcast(message: types.Message, state: FSMContext):
    await message.answer("❌ Рассылка отменена")
    await state.clear()

@dp.message(BroadcastStates.waiting_for_message)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    users = get_all_users()
    total_users = len(users)
    processing_msg = await message.answer(f"⏳ Начинаю рассылку для {total_users} пользователей...")

    success = 0
    failed = 0

    for chat_id in users:
        try:
            if message.photo:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=message.photo[-1].file_id,
                    caption=message.caption if message.caption else "",
                    parse_mode="HTML"
                )
            elif message.text:
                await bot.send_message(
                    chat_id=chat_id,
                    text=message.text,
                    parse_mode="HTML"
                )
            success += 1
            await asyncio.sleep(0.1)  
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки в {chat_id}: {str(e)}")

    await processing_msg.edit_text(
        f"✅ Рассылка завершена:\n"
        f"• Успешно: {success}\n"
        f"• Не удалось: {failed}\n"
        f"• Всего: {total_users}"
    )
    await state.clear()

@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    if config.get("admin_ids") and message.from_user.id in config["admin_ids"]:
        active_users = get_active_users_count()
        total_users = get_total_users_count()
        blocked_users = get_blocked_users_count()

        await message.answer(
            f"📊 Статистика бота:\n"
            f"• Активных пользователей: {active_users}\n"
            f"• Заблокированных: {blocked_users}\n"
            f"• Всего пользователей: {total_users}"
        )
    else:
        await message.answer("⛔ У вас нет прав для этой команды")

@dp.message(Command("block"))
async def cmd_block_user(message: types.Message):
    if not (config.get("admin_ids") and message.from_user.id in config["admin_ids"]):
        await message.answer("⛔ У вас нет прав для этой команды")
        return

    try:
        chat_id = int(message.text.split()[1])
        block_user(chat_id)
        await message.answer(f"✅ Пользователь {chat_id} заблокирован")
    except (IndexError, ValueError):
        await message.answer("Используйте: /block <user_id>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(Command("unblock"))
async def cmd_unblock_user(message: types.Message):
    if not (config.get("admin_ids") and message.from_user.id in config["admin_ids"]):
        await message.answer("⛔ У вас нет прав для этой команды")
        return

    try:
        chat_id = int(message.text.split()[1])
        unblock_user(chat_id)
        await message.answer(f"✅ Пользователь {chat_id} разблокирован")
    except (IndexError, ValueError):
        await message.answer("Используйте: /unblock <user_id>")
    except Exception as e:
        await message.answer(f"❌ Ошибка: {str(e)}")

@dp.message(F.text.in_(config.get("buttons", {}).keys()))
async def handle_reply_buttons(message: types.Message):
    save_user(
        message.chat.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )
    await process_command(message.chat.id, config["buttons"][message.text])

@dp.callback_query(F.data.in_(config.get("buttons", {}).keys()))
async def handle_inline_buttons(callback: types.CallbackQuery):
    await callback.answer()

    save_user(
        callback.message.chat.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name
    )

    button_data = config["buttons"][callback.data]

    if not (isinstance(button_data, dict) and "url" in button_data):
        await process_command(callback.message.chat.id, button_data)

@dp.callback_query()
async def handle_all_inline_buttons(callback: types.CallbackQuery):
    await callback.answer()

    save_user(
        callback.from_user.id,
        callback.from_user.username,
        callback.from_user.first_name,
        callback.from_user.last_name
    )

    if callback.data in config.get("buttons", {}):
        button_data = config["buttons"][callback.data]

        if not (isinstance(button_data, dict) and "url" in button_data):
            await process_command(callback.message.chat.id, button_data)
    else:
        await callback.message.answer("Кнопка не настроена")

@dp.message()
async def handle_unknown(message: types.Message):

    if message.from_user.id == 777000:  
        return

    save_user(
        message.chat.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )

    if message.text and message.text.startswith('/'):
        await send_response(
            message.chat.id,
            config.get("unknown_message", {"text": "Неизвестная команда"})
        )
    elif message.text:  
        await send_response(
            message.chat.id,
            config.get("unknown_message", {"text": "Пожалуйста, используйте команды из меню"})
        )

dp.message.middleware(check_user_blocked_middleware)

async def on_startup():
    logger.info("Бот запущен!")
    await setup_broadcasts()
    await set_bot_commands()

async def set_bot_commands():
    commands = []
    for cmd, data in config["commands"].items():
        if cmd.startswith('/') and "description" in data:
            command = cmd.lstrip('/')  
            description = data["description"]
            commands.append(types.BotCommand(command=command, description=description))

    if commands:
        await bot.set_my_commands(commands)
        logger.info("Команды бота обновлены")

async def run_bot():
    global loop  
    loop = asyncio.get_event_loop()
    dp.startup.register(on_startup)

    try:
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        pass
    finally:
        await bot.session.close()

def start_bot():
    global bot_task
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        bot_task = loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()
        sys.exit(0)

if __name__ == "__main__":
    try:
        start_bot()
    except SystemExit:
        os._exit(0)

    logger.info("Бот остановлен")
    os._exit(0)
