"""
Echospy Telegram bot (webhook-режим, для бесплатного Render Web Service).

Обрабатывает:
  /start                 — приветствие с кнопками «Начать игру», «Узнать больше».
  /start join_<CODE>     — присоединение к онлайн-комнате по ссылке-приглашению
                            (просто присылает кнопку «Открыть комнату» — сам
                            игрок регистрируется в комнате уже внутри мини-приложения).

Кнопка «Играть онлайн» и вся логика комнаты (создание, слоты игроков, приглашения)
живут в самом мини-приложении (index.html), а не в чате бота. Бот здесь только:
  1) отдаёт мини-приложению API для комнат (создание/вход/старт/раздача ролей);
  2) отвечает на диплинк-приглашения текстом со ссылкой на комнату.

Онлайн-комнаты хранятся в памяти процесса (без базы данных). Пока хотя бы
один игрок находится в комнате с открытым мини-приложением, оно опрашивает
сервер каждые несколько секунд — это держит бесплатный Render Web Service
"проснувшимся" (он засыпает только после ~15 минут без единого запроса).

Работает как маленький HTTP-сервер (Flask) и сам регистрирует себя как
вебхук в Telegram при старте — используя публичный адрес, который Render
даёт бесплатным Web Service автоматически (переменная RENDER_EXTERNAL_URL).

Переменные окружения:
  BOT_TOKEN             — токен бота (обязательно, задаётся в Render → Environment)
  RENDER_EXTERNAL_URL   — подставляется Render автоматически, вручную не нужно
"""

import hashlib
import hmac
import json
import logging
import os
import random
import string
import time
from urllib.parse import parse_qsl

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

# ---------------------------------------------------------------------------
# Онлайн-комнаты (в памяти).
# ---------------------------------------------------------------------------

rooms = {}  # code -> {host_id, players: [{id, name, photo_url}], started, topic, spy_id, created_at}
ROOM_CODE_CHARS = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0O1I")
ROOM_MAX_AGE_SECONDS = 3 * 60 * 60  # чистим комнаты старше 3 часов
BOT_USERNAME = None  # заполняется в setup_webhook() через getMe


def prune_old_rooms():
    now = time.time()
    stale = [code for code, r in rooms.items() if now - r["created_at"] > ROOM_MAX_AGE_SECONDS]
    for code in stale:
        rooms.pop(code, None)


def gen_room_code():
    while True:
        code = "".join(random.choices(ROOM_CODE_CHARS, k=5))
        if code not in rooms:
            return code


def validate_init_data(init_data):
    """Проверяет подпись Telegram WebApp initData. Возвращает dict пользователя или None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None
    user_json = pairs.get("user")
    if not user_json:
        return None
    try:
        return json.loads(user_json)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Telegram helpers.
# ---------------------------------------------------------------------------

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{API_URL}/sendMessage", json=payload, timeout=10)
    except requests.RequestException:
        logger.exception("sendMessage failed")


def answer_callback_query(callback_query_id, text=None):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    try:
        requests.post(f"{API_URL}/answerCallbackQuery", json=payload, timeout=10)
    except requests.RequestException:
        logger.exception("answerCallbackQuery failed")


def send_welcome(chat_id):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🎮 Начать игру", "web_app": {"url": MINI_APP_URL}}],
            [{"text": "ℹ️ Узнать больше", "url": NEWS_CHANNEL_URL}],
        ]
    }
    send_message(chat_id, WELCOME_TEXT, keyboard)


def send_room_joined(chat_id, code):
    text = f"✅ Приглашение в комнату `{code}` принято!\nОткрой комнату — там и определится твоя роль."
    keyboard = {
        "inline_keyboard": [
            [{"text": "📲 Открыть комнату", "web_app": {"url": f"{MINI_APP_URL}?room={code}"}}],
        ]
    }
    send_message(chat_id, text, keyboard)


# ---------------------------------------------------------------------------
# Webhook.
# ---------------------------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    logger.info("Update: %s", update)

    callback_query = update.get("callback_query")
    if callback_query:
        # Callback-кнопок в текущем меню бота нет, но на всякий случай гасим
        # "часики" на кнопке, если Telegram всё же пришлёт такой апдейт.
        answer_callback_query(callback_query["id"])
        return {"ok": True}

    message = update.get("message")
    if message:
        text = message.get("text", "")
        chat_id = message["chat"]["id"]

        if text.startswith("/start"):
            payload = text[len("/start"):].strip()
            if payload.startswith("join_"):
                code = payload[len("join_"):].strip().upper()
                prune_old_rooms()
                room = rooms.get(code)
                if not room:
                    send_message(chat_id, "❌ Комната не найдена или уже закрыта. Попроси хоста прислать ссылку заново.")
                elif room["started"]:
                    send_message(chat_id, "❌ Игра в этой комнате уже началась.")
                else:
                    send_room_joined(chat_id, code)
            else:
                send_welcome(chat_id)

    return {"ok": True}


# ---------------------------------------------------------------------------
# API для мини-приложения (index.html).
# ---------------------------------------------------------------------------

@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


@app.route("/api/room/create", methods=["POST", "OPTIONS"])
def api_room_create():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(force=True, silent=True) or {}
    user = validate_init_data(body.get("initData", ""))
    if not user:
        return {"error": "auth_failed"}, 401
    prune_old_rooms()
    code = gen_room_code()
    rooms[code] = {
        "host_id": user["id"],
        "players": [
            {
                "id": user["id"],
                "name": user.get("first_name", "Игрок"),
                "photo_url": user.get("photo_url"),
            }
        ],
        "started": False,
        "topic": None,
        "spy_id": None,
        "created_at": time.time(),
    }
    return {"code": code}


@app.route("/api/room/<code>/join", methods=["POST", "OPTIONS"])
def api_room_join(code):
    if request.method == "OPTIONS":
        return "", 204
    room = rooms.get(code.upper())
    if not room:
        return {"error": "not_found"}, 404
    body = request.get_json(force=True, silent=True) or {}
    user = validate_init_data(body.get("initData", ""))
    if not user:
        return {"error": "auth_failed"}, 401

    existing = next((p for p in room["players"] if p["id"] == user["id"]), None)
    if existing:
        existing["name"] = user.get("first_name", existing["name"])
        existing["photo_url"] = user.get("photo_url", existing.get("photo_url"))
        return {"ok": True}

    if room["started"]:
        return {"error": "already_started"}, 400

    room["players"].append(
        {
            "id": user["id"],
            "name": user.get("first_name", "Игрок"),
            "photo_url": user.get("photo_url"),
        }
    )
    return {"ok": True}


@app.route("/api/room/<code>", methods=["GET"])
def api_room_status(code):
    room = rooms.get(code.upper())
    if not room:
        return {"error": "not_found"}, 404
    return {
        "hostId": room["host_id"],
        "players": [{"name": p["name"], "photoUrl": p.get("photo_url")} for p in room["players"]],
        "started": room["started"],
    }


@app.route("/api/room/<code>/start", methods=["POST", "OPTIONS"])
def api_room_start(code):
    if request.method == "OPTIONS":
        return "", 204
    room = rooms.get(code.upper())
    if not room:
        return {"error": "not_found"}, 404
    body = request.get_json(force=True, silent=True) or {}
    user = validate_init_data(body.get("initData", ""))
    if not user or user.get("id") != room["host_id"]:
        return {"error": "forbidden"}, 403
    if room["started"]:
        return {"ok": True}
    if len(room["players"]) < 3:
        return {"error": "not_enough_players"}, 400
    topic = body.get("topic")
    if not topic or not isinstance(topic, dict):
        return {"error": "bad_topic"}, 400
    spy = random.choice(room["players"])
    room["topic"] = topic
    room["spy_id"] = spy["id"]
    room["started"] = True
    return {"ok": True}


@app.route("/api/room/<code>/role", methods=["POST", "OPTIONS"])
def api_room_role(code):
    if request.method == "OPTIONS":
        return "", 204
    room = rooms.get(code.upper())
    if not room:
        return {"error": "not_found"}, 404
    body = request.get_json(force=True, silent=True) or {}
    user = validate_init_data(body.get("initData", ""))
    if not user:
        return {"error": "auth_failed"}, 401
    if not room["started"]:
        return {"error": "not_started"}, 400
    if not any(p["id"] == user["id"] for p in room["players"]):
        return {"error": "forbidden"}, 403
    is_spy = user["id"] == room["spy_id"]
    return {"isSpy": is_spy, "topic": None if is_spy else room["topic"]}


@app.route("/", methods=["GET"])
def health():
    return "Echospy bot is running."


def setup_webhook():
    global BOT_USERNAME
    try:
        me_resp = requests.get(f"{API_URL}/getMe", timeout=10)
        me = me_resp.json()
        if me.get("ok"):
            BOT_USERNAME = me["result"]["username"]
            logger.info("Bot username: %s", BOT_USERNAME)
    except Exception:
        logger.warning("Не удалось получить username бота через getMe.")

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
