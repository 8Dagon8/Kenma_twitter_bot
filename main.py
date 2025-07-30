import os
import datetime
import pytz
import telebot
import openai

# --- Настройки ---
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tokyo")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "123456789"))

# --- Telegram бот ---
bot = telebot.TeleBot(BOT_TOKEN)

# --- OpenAI настройка ---
openai.api_key = OPENAI_API_KEY

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
        f"You are Kenma Kozume from Haikyuu!!, a 15-year-old introverted gamer who just started using social media "
        f"after being convinced by Kuroo. The year is 2025, before the manga events. Be minimal, introspective, slightly ironic. "
        f"{context}\n\n"
        "Write a short tweet in English, then add its Russian translation."
    )

    response = openai.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8,
        max_tokens=300,
    )

    return response.choices[0].message.content

@bot.message_handler(commands=['post'])
def send_post(message):
    if message.from_user.id != BOT_OWNER_ID:
        return
    text = generate_post()
    bot.send_message(message.chat.id, text)

@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_message(message.chat.id, "Привет! Отправь /post, чтобы получить новый твит от Кенмы.")

print("🤖 Bot is running...")
bot.infinity_polling()




