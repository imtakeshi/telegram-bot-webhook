from __future__ import annotations

import json
import os

from flask import Flask, request
from telebot import types

# Импортируем бота с уже зарегистрированными хендлерами
from bot import bot  # noqa: E402

WEBHOOK_URL = (os.getenv("WEBHOOK_URL") or "").strip()
WEBHOOK_PATH = (os.getenv("WEBHOOK_PATH") or "/webhook").strip()

app = Flask(__name__)


@app.get("/health")
def health() -> tuple[str, int]:
    return "ok", 200


@app.post(WEBHOOK_PATH)
def telegram_webhook() -> tuple[str, int]:
    # Telegram присылает JSON объекта Update
    if request.headers.get("content-type", "").startswith("application/json"):
        raw = request.get_data(as_text=True)
        update = types.Update.de_json(json.loads(raw))
        bot.process_new_updates([update])
        return "ok", 200

    return "bad request", 400


def setup_webhook() -> None:
    if not WEBHOOK_URL:
        print("WEBHOOK_URL пустой — webhook не настраиваем")
        return

    # Настроим webhook при старте сервиса.
    # drop_pending_updates=True не используется здесь, чтобы не терять обновления.
    try:
        bot.remove_webhook()
    except Exception:
        pass

    bot.set_webhook(url=WEBHOOK_URL)
    print("Webhook настроен:", WEBHOOK_URL)


if __name__ == "__main__":
    setup_webhook()
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)

