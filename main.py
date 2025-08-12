import os
import datetime
import threading
import pytz

from flask import Flask, request
import telebot

# === ENV ===
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))  # 0 = не ограничивать
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tokyo")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # например: https://<project>.up.railway.app
PORT = int(os.environ.get("PORT", "5000"))

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")
if not WEBHOOK_URL:
    # попробуем авто-детект домена Railway, если ты его пробрасываешь
    WEBHOOK_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("RAILWAY_STATIC_URL")
    if WEBHOOK_URL and not WEBHOOK_URL.startswith("http"):
        WEBHOOK_URL = f"https://{WEBHOOK_URL}"
    if not WEBHOOK_URL:
        raise RuntimeError("Missing WEBHOOK_URL (или RAILWAY_PUBLIC_DOMAIN)")

# === OpenAI (новый SDK 1.x) ===
from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL = "gpt-4o-mini"  # быстрее и дешевле для коротких постов

# === Telegram bot & Flask ===
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
app = Flask(__name__)

def get_today_context():
    now = datetime.datetime.now(pytz.timezone(TIMEZONE))
    weekday = now.strftime('%A')
    date = now.strftime('%d %B %Y')
    season = {
        '01': 'winter', '02': 'winter', '03': 'spring',
        '04': 'spring', '05': 'spring', '06': 'summer',
        '07': 'summer', '08': 'summer', '09': 'autumn',
        '10': 'autumn', '11': 'autumn', '12': 'winter',
    }[now.strftime('%m')]
    return f"Today is {weekday}, {date}. It’s {season} in Tokyo."

def generate_post():
    context = get_today_context()
    prompt = (
        "You are Kenma Kozume from Haikyuu!!, a 15-year-old introverted gamer who just started using social media "
        "after being convinced by Kuroo. The year is 2025 (before manga events). Be minimal, introspective, slightly ironic. "
        f"{context}\n\n"
        "Write a short tweet in English (2–3 short lines max), then add its Russian translation on new line(s). "
        "No emojis. No hashtags."
    )
    # Новый вызов (Responses API можно, но для простоты — chat.completions)
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=300,
    )
    return resp.choices[0].message.content.strip()

# === Команды ===
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_message(message.chat.id, "Привет! Напиши текст — отвечу как Кенма. Или используй /post для генерации твита.")

@bot.message_handler(commands=['post'])
def send_post(message):
    if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
        bot.send_message(message.chat.id, "⛔️ You are not authorized to use this command.")
        return

    # Генерацию делаем в фоне, чтобы не блокировать вебхук
    chat_id = message.chat.id
    def worker():
        try:
            bot.send_chat_action(chat_id, 'typing')
            text = generate_post()
            bot.send_message(chat_id, text)
        except Exception as e:
            bot.send_message(chat_id, f"Хм, я споткнулась: {e}")
    threading.Thread(target=worker, daemon=True).start()

# === Обычный текст — тоже отвечаем ===
@bot.message_handler(func=lambda m: True, content_types=['text'])
def on_text(message):
    # если хочешь ограничить только для владельца — раскомментируй:
    # if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
    #     bot.send_message(message.chat.id, "⛔️ Not allowed.")
    #     return

    user_text = message.text or ""
    chat_id = message.chat.id

    def worker():
        try:
            bot.send_chat_action(chat_id, 'typing')
            # лёгкий промпт: ответить в стиле Кенмы на реплику
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "You are Kenma Kozume (Haikyuu!!). Minimal, calm, slightly ironic. No emojis, no hashtags."},
                    {"role": "user", "content": f"{get_today_context()}\nUser said: {user_text}\nReply briefly in English, then a Russian translation below."}
                ],
                temperature=0.7,
                max_tokens=200,
            )
            answer = resp.choices[0].message.content.strip()
            bot.send_message(chat_id, answer)
        except Exception as e:
            bot.send_message(chat_id, f"Ошибка ответа: {e}")

    threading.Thread(target=worker, daemon=True).start()

# === Webhook endpoint: быстрый ACK, обработка в отдельном потоке ===
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def receive_update():
    try:
        json_string = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_string)
        # обрабатываем асинхронно, чтобы сразу вернуть 200
        threading.Thread(target=bot.process_new_updates, args=([update],), daemon=True).start()
        return "OK", 200
    except Exception as e:
        return f"ERR: {e}", 200  # Telegram только 200 любит

@app.route("/", methods=["GET"])
def index():
    return "Kenma bot is running."

# === Установка вебхука при старте ===
bot.remove_webhook()
bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)




