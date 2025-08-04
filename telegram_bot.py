import yaml
import os
import asyncio
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

# Загрузка конфига
with open("config.yml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

# Инициализация бота с HTML по умолчанию
bot = Bot(
    token=config["bot"]["token"],
    default=DefaultBotProperties(parse_mode="HTML")
)
dp = Dispatcher()

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
        # Если кнопка представлена как словарь (для URL или других параметров)
        if isinstance(btn, dict):
            if "url" in btn:
                # URL-кнопка
                keyboard.append([InlineKeyboardButton(text=btn["text"], url=btn["url"])])
            # Можно добавить другие типы кнопок здесь, если нужно
        else:
            # Обычная callback кнопка
            keyboard.append([InlineKeyboardButton(text=btn, callback_data=btn)])
    
    return InlineKeyboardMarkup(inline_keyboard=keyboard)

async def send_response(chat_id, data):
    # Обработка задержки backup
    if "backup" in data:
        await asyncio.sleep(data["backup"])
    
    # Обработка эффекта "печатает..."
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

async def process_command(chat_id, command_data):
    if isinstance(command_data, list):
        # Если команда содержит несколько сообщений
        for message_data in command_data:
            await send_response(chat_id, message_data)
    else:
        # Одиночное сообщение
        await send_response(chat_id, command_data)

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if "/start" in config["commands"]:
        await process_command(message.chat.id, config["commands"]["/start"])

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    if "/help" in config["commands"]:
        await process_command(message.chat.id, config["commands"]["/help"])
    else:
        await send_response(message.chat.id, {"text": "Команда /help не настроена"})

@dp.message()
async def handle_unknown(message: types.Message):
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

@dp.message(F.text.in_(config.get("buttons", {}).keys()))
async def handle_reply_buttons(message: types.Message):
    await process_command(message.chat.id, config["buttons"][message.text])

@dp.callback_query(F.data.in_(config.get("buttons", {}).keys()))
async def handle_inline_buttons(callback: types.CallbackQuery):
    # Проверяем, является ли callback_data URL-ссылкой
    button_data = config["buttons"].get(callback.data, {})
    if isinstance(button_data, dict) and "url" in button_data:
        # Для URL просто отвечаем на callback, чтобы убрать часики
        await callback.answer()
    else:
        await process_command(callback.message.chat.id, button_data)
        await callback.answer()

if __name__ == "__main__":
    print("Бот запущен с поддержкой HTML!")
    dp.run_polling(bot)
