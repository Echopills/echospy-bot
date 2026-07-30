"""
Echospy Telegram bot.

Обрабатывает команду /start: присылает приветственное сообщение и две кнопки:
  1. "🎮 Начать игру" — открывает Mini App (сайт игры Echospy).
  2. "ℹ️ Узнать больше" — пока ничего не делает (заглушка).

Установка зависимостей:
    pip install python-telegram-bot==21.*

Запуск:
    python3 bot.py
(токен читается из переменной окружения BOT_TOKEN, либо можно
 подставить его напрямую в переменную BOT_TOKEN ниже — см. комментарий).
"""

import logging
import os

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Токен бота задаётся ТОЛЬКО через переменную окружения BOT_TOKEN
# (в настройках хостинга — Render/Railway/VPS), в код он не зашит
# из соображений безопасности, чтобы не утёк вместе с репозиторием.
BOT_TOKEN = os.environ.get("BOT_TOKEN")

# Ссылка на Mini App (сайт игры).
MINI_APP_URL = "https://echopills.github.io/echospy/"

WELCOME_TEXT = (
    "🕵️ *ECHOSPY*\n\n"
    "Вы в игре. Один из вас — шпион.\n"
    "Остальные знают тему. Шпион — нет.\n"
    "Говорите осторожно. Задавайте вопросы аккуратно.\n\n"
    "Один неверный шаг — и тебя раскроют.\n"
    "Не спались."
)

# callback_data для кнопки-заглушки "Узнать больше"
LEARN_MORE_CALLBACK = "learn_more_noop"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [
        [InlineKeyboardButton("🎮 Начать игру", web_app=WebAppInfo(url=MINI_APP_URL))],
        [InlineKeyboardButton("ℹ️ Узнать больше", callback_data=LEARN_MORE_CALLBACK)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        WELCOME_TEXT,
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def learn_more_noop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка-заглушка: просто гасит "часики" на кнопке, ничего не делает."""
    query = update.callback_query
    await query.answer()  # без текста и без alert — визуально ничего не происходит


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Укажите токен бота в переменной окружения BOT_TOKEN.")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(learn_more_noop, pattern=f"^{LEARN_MORE_CALLBACK}$"))

    logger.info("Bot started, polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
