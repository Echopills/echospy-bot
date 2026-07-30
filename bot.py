"""
Echospy Telegram bot (webhook-режим, для бесплатного Render Web Service).

Обрабатывает команду /start: присылает приветственное сообщение и две кнопки:
  1. "🎮 Начать игру" — открывает Mini App (сайт игры Echospy).
  2. "ℹ️ Узнать больше" — открывает Telegram-канал с новостями о проектах.

Работает как маленький HTTP-сервер (Flask) и сам регистрирует себя как
вебхук в Telegram при старте — используя публичный адрес, который Render
даёт бесплатным Web Service автоматически (переменная RENDER_EXTERNAL_URL).

Переменные окружения:
  BOT_TOKEN            — токен бота (обязательно, задаётся в Render → Environment)
  RENDER_EXTERNAL_URL   — подставляется Render автоматически, вручную не нужно
"""

import logging
import os

import requests
from flask import Flask, request

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Укажите токен бота в переменной окружения BOT_TOKEN.")

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# Ссылка на Mini App (сайт игры).
MINI_APP_URL = "https://echopills.github.io/echospy/"

# Ссылка на Telegram-канал с новостями о проектах.
NEWS_CHANNEL_URL = "https://t.me/Echodraftss"

WELCOME_TEXT = (
    "🕵️ *ECHOSPY*\n\n"
    "Вы в игре. Один из вас — шпион.\n"
    "Остальные знают тему. Шпион — нет.\n"
    "Говорите осторожно. Задавайте вопросы аккуратно.\n\n"
    "Один неверный шаг — и тебя раскроют.\n"
    "Не спались."
)

app = Flask(__name__)


def send_welcome(chat_id: int) -> None:
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎮 Начать игру", "web_app": {"url": MINI_APP_URL}}],
            [{"text": "ℹ️ Узнать больше", "url": NEWS_CHANNEL_URL}],
        ]
    }
    requests.post(
        f"{API_URL}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": WELCOME_TEXT,
            "parse_mode": "Markdown",
            "reply_markup": keyboard,
        },
        timeout=10,
    )


@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    logger.info("Update: %s", update)

    message = update.get("message")
    if message and message.get("text", "").startswith("/start"):
        send_welcome(message["chat"]["id"])

    return {"ok": True}


@app.route("/", methods=["GET"])
def health():
    return "Echospy bot is running."


def setup_webhook() -> None:
    external_url = os.environ.get("RENDER_EXTERNAL_URL")
    if not external_url:
        logger.warning(
            "RENDER_EXTERNAL_URL не задан — webhook не настроен автоматически. "
            "Задайте его вручную через setWebhook, если хостинг не Render."
        )
        return

    webhook_url = external_url.rstrip("/") + "/webhook"
    resp = requests.post(f"{API_URL}/setWebhook", json={"url": webhook_url}, timeout=10)
    logger.info("setWebhook(%s) -> %s", webhook_url, resp.json())


setup_webhook()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
