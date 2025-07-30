import os
import openai
import telebot
from flask import Flask, request
import datetime
import pytz

# Переменные среды
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "123456789"))
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tokyo")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://your-project.up.railway.app

# Настройка OpenAI
openai.api_key = OPENAI_API_KEY
model = "gpt-4o"

# Telegram бот
bot = telebot.TeleBot(BOT_TOKEN)

# Flask-приложение
app = Flask(__name__)

# Получение текущего дня и сезона
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

# Генерация твита от Кенмы
def generate_post():
    context = get_today_context()
    prompt = (
        f"You are Kenma Kozume from Haikyuu!!, a 15-year-old introverted gamer who just started using social media "
        f"after being convinced by Kuroo. The year is 2025, before the manga events. Be minimal, introspective, slightly ironic. "
        f"{context}\n\n"
        "Write a short tweet in English, then add its Russian translation."
    )
    response = openai.ChatCompletion.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=300,
    )
    return response['choices'][0]['message']['content']

# Команда /post
@bot.message_handler(commands=['post'])
def send_post(message):
    if message.from_user.id != BOT_OWNER_ID:
        bot.send_message(message.chat.id, "⛔️ You are not authorized to use this bot.")
        return
    text = generate_post()
    bot.send_message(message.chat.id, text)

# Команда /start
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_message(message.chat.id, "Привет! Отправь /post, чтобы получить новый твит от Кенмы.")

# Webhook endpoint
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def receive_update():
    json_string = request.get_data().decode("utf-8")
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

# Проверка, что всё запустилось
@app.route("/", methods=["GET"])
def index():
    return "Kenma bot is running."

# Установка webhook при старте
bot.remove_webhook()
bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")

# Запуск сервера
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))




