import yaml
import os
import asyncio
import sys
import signal
import sqlite3
import threading
import tkinter as tk
from tkinter import messagebox
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import (
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
from datetime import datetime, time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

bot_running = True
bot_task = None
dp = Dispatcher()  

class BroadcastStates(StatesGroup):
    waiting_for_message = State()

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

def init_db():
    with sqlite3.connect("users.db") as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                date_added TEXT
            )
        """)

        try:
            conn.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER DEFAULT 0")
        except sqlite3.OperationalError:
            pass  

init_db()

def save_user(chat_id: int, username: str = None, first_name: str = None, last_name: str = None):
    with sqlite3.connect("users.db") as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO users 
            (chat_id, username, first_name, last_name, date_added, is_blocked)
            VALUES (?, ?, ?, ?, ?, 0)
            """,
            (chat_id, username, first_name, last_name, datetime.now().isoformat())
        )

def get_all_users() -> list[int]:
    with sqlite3.connect("users.db") as conn:
        cursor = conn.execute("SELECT chat_id FROM users WHERE is_blocked = 0")
        return [row[0] for row in cursor.fetchall()]

def get_active_users_count() -> int:
    with sqlite3.connect("users.db") as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 0")
        return cursor.fetchone()[0]

def get_total_users_count() -> int:
    with sqlite3.connect("users.db") as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM users")
        return cursor.fetchone()[0]

def get_blocked_users_count() -> int:
    with sqlite3.connect("users.db") as conn:
        cursor = conn.execute("SELECT COUNT(*) FROM users WHERE is_blocked = 1")
        return cursor.fetchone()[0]

def block_user(chat_id: int):
    with sqlite3.connect("users.db") as conn:
        conn.execute("UPDATE users SET is_blocked = 1 WHERE chat_id = ?", (chat_id,))

def unblock_user(chat_id: int):
    with sqlite3.connect("users.db") as conn:
        conn.execute("UPDATE users SET is_blocked = 0 WHERE chat_id = ?", (chat_id,))

def get_reply_keyboard(buttons):
    if not buttons:
        return None
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=btn)] for btn in buttons],
        resize_keyboard=True,
        one_time_keyboard=True
    )

def get_inline_keyboard(buttons):
    if not buttons:
        return None

    keyboard = []
    for btn in buttons:
        if isinstance(btn, dict):
            if "url" in btn:
                keyboard.append([InlineKeyboardButton(text=btn["text"], url=btn["url"])])
        else:
            keyboard.append([InlineKeyboardButton(text=btn, callback_data=btn)])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def send_response(chat_id: int, data: dict):
    if "backup" in data:
        await asyncio.sleep(data["backup"])

    if "backup_print" in data:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
        await asyncio.sleep(data["backup_print"])

    text = data.get("text", "").strip()
    reply_markup = None

    if "inline_buttons" in data:
        reply_markup = get_inline_keyboard(data["inline_buttons"])
    elif "reply_buttons" in data:
        reply_markup = get_reply_keyboard(data["reply_buttons"])

    if "image" in data and os.path.exists(data["image"]):
        media = InputMediaPhoto(
            media=FSInputFile(data["image"]),
            caption=text
        )
        await bot.send_photo(
            chat_id=chat_id,
            photo=media.media,
            caption=media.caption,
            reply_markup=reply_markup
        )
    elif text:
        await bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup
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

    message_data = template_messages[template_name]
    users = get_all_users()
    total_users = len(users)

    confirm_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Начать рассылку", callback_data=f"broadcast_confirm:{template_name}")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="broadcast_cancel")]
    ])

    preview_text = message_data.get("text", "[Сообщение без текста]")[:200]
    await message.answer(
        f"📨 Подтвердите рассылку шаблона <b>{template_name}</b>\n"
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

    message_data = template_messages[template_name]
    users = get_all_users()
    total_users = len(users)

    await callback.message.edit_text(f"⏳ Начинаю рассылку шаблона '{template_name}'...")
    await callback.answer()

    success = 0
    failed = 0

    for i, chat_id in enumerate(users, 1):
        try:
            await process_command(chat_id, message_data)
            success += 1

            if i % 10 == 0:
                await callback.message.edit_text(
                    f"📨 Рассылка шаблона '{template_name}'\n"
                    f"✅ Успешно: {success}\n"
                    f"❌ Ошибок: {failed}\n"
                    f"🔹 Всего: {i}/{total_users}"
                )

            await asyncio.sleep(0.1)
        except Exception as e:
            failed += 1
            logger.error(f"Ошибка отправки в {chat_id}: {str(e)}")

    await callback.message.edit_text(
        f"✅ Рассылка шаблона '{template_name}' завершена:\n"
        f"• Успешно: {success}\n"
        f"• Не удалось: {failed}\n"
        f"• Всего: {total_users}"
    )

@dp.callback_query(F.data == "broadcast_cancel")
async def cancel_broadcast(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Рассылка отменена")
    await callback.answer()

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
                    caption=message.caption if message.caption else ""
                )
            elif message.text:

                await bot.send_message(
                    chat_id=chat_id,
                    text=message.text
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
    save_user(
        message.chat.id,
        message.from_user.username,
        message.from_user.first_name,
        message.from_user.last_name
    )

    if message.text.startswith('/'):
        await send_response(
            message.chat.id,
            config.get("unknown_message", {"text": "Неизвестная команда"})
        )
    else:
        await send_response(
            message.chat.id,
            config.get("unknown_message", {"text": "Пожалуйста, используйте команды из меню"})
        )

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

def create_gui():
    def stop_bot():
        global bot_running
        if messagebox.askyesno("Подтверждение", "Вы уверены, что хотите остановить бота?"):
            bot_running = False

            root.destroy()

            if 'loop' in globals():
                loop.call_soon_threadsafe(loop.stop)

            os.kill(os.getpid(), signal.SIGTERM)

    root = tk.Tk()
    root.title("Управление ботом")
    root.geometry("300x150")

    status_label = tk.Label(root, text="Статус: Бот запущен", fg="green")
    status_label.pack(pady=10)

    stop_button = tk.Button(root, text="Остановить бота", command=stop_bot, bg="red", fg="white")
    stop_button.pack(pady=20)

    root.protocol("WM_DELETE_WINDOW", stop_bot)
    root.mainloop()

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

    gui_thread = threading.Thread(target=create_gui, daemon=True)
    gui_thread.start()

    try:
        start_bot()
    except SystemExit:
        os._exit(0)

    logger.info("Бот остановлен")
    os._exit(0)
