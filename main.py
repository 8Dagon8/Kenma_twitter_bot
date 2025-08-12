import os
import re
import json
import datetime
import threading
from typing import List

import pytz
from flask import Flask, request
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ==== ENV ====
BOT_TOKEN = os.environ.get("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
BOT_OWNER_ID = int(os.environ.get("BOT_OWNER_ID", "0"))  # 0 = не ограничивать
TIMEZONE = os.environ.get("TIMEZONE", "Asia/Tokyo")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # https://<project>.up.railway.app
PORT = int(os.environ.get("PORT", "5000"))

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_TOKEN")
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY")
if not WEBHOOK_URL:
    WEBHOOK_URL = os.environ.get("RAILWAY_PUBLIC_DOMAIN") or os.environ.get("RAILWAY_STATIC_URL")
    if WEBHOOK_URL and not WEBHOOK_URL.startswith("http"):
        WEBHOOK_URL = f"https://{WEBHOOK_URL}"
    if not WEBHOOK_URL:
        raise RuntimeError("Missing WEBHOOK_URL (или RAILWAY_PUBLIC_DOMAIN)")

# ==== OpenAI (SDK 1.x) ====
from openai import OpenAI
client = OpenAI(api_key=OPENAI_API_KEY)
MODEL = "gpt-4o-mini"

# ==== Telegram & Flask ====
bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
app = Flask(__name__)

# ==== История и буферы ====
HISTORY_PATH = "history.json"
TOKEN_LIMIT = 1000                   # ~1000 токенов (≈4 символа на токен)
DEFAULT_VARIANTS = 3
PENDING_OPTIONS: dict[int, List[str]] = {}   # {user_id: [варианты]}
LAST_FILE_BY_USER: dict[int, str] = {}       # {user_id: сырой текст из последнего .txt}

# ---------- helpers: history ----------
def load_history() -> List[str]:
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []

def save_history(history: List[str]):
    total_chars = 0
    trimmed: List[str] = []
    for post in reversed(history):
        total_chars += len(post)
        if total_chars / 4.0 > TOKEN_LIMIT:
            break
        trimmed.append(post)
    trimmed.reverse()
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(trimmed, f, ensure_ascii=False, indent=2)

def normalize(s: str) -> str:
    return " ".join((s or "").lower().strip().split())

def add_to_history(posts: list[str]) -> int:
    history = load_history()
    seen = {normalize(p) for p in history}
    added = 0
    for p in posts:
        p = (p or "").strip()
        if not p:
            continue
        key = normalize(p)
        if key in seen:
            continue
        history.append(p)
        seen.add(key)
        added += 1
    save_history(history)
    return added

def parse_posts_from_text(text: str) -> list[str]:
    # каждый пост — абзац, отделённый пустой строкой
    chunks = [c.strip() for c in (text or "").split("\n\n") if c.strip()]
    return [c for c in chunks if len(c) >= 10]

# ---------- helpers: generation ----------
def get_today_context() -> str:
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

def _clean_code_fences(s: str) -> str:
    # срезаем ```json ... ``` или ``` ... ```
    s = re.sub(r"^\s*```(?:json)?\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s*```\s*$", "", s)
    return s.strip()

def generate_posts(n: int = DEFAULT_VARIANTS) -> List[str]:
    history = load_history()
    context = get_today_context()

    previous = "\n".join(f"- {p}" for p in history[-20:]) if history else "- (none)"
    prompt = (
        "You are Kenma Kozume from Haikyuu!! (15, introverted gamer). "
        "Summer 2025, before manga events. Minimal, calm, slightly ironic.\n"
        f"{context}\n\n"
        f"Write {n} alternative tweet drafts. Each draft: 1–3 short lines in English, "
        "then add its Russian translation on the next line(s). "
        "No emojis. No hashtags.\n\n"
        "Avoid repeating the wording/ideas of previous posts below:\n"
        f"{previous}\n\n"
        f"Return ONLY a valid JSON array of exactly {n} strings with no extra text, no code fences."
    )

    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.9,
        max_tokens=600,
    )
    raw = resp.choices[0].message.content.strip()
    cleaned = _clean_code_fences(raw)

    options = None
    # 1) прямой JSON
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            options = parsed
    except Exception:
        pass

    # 2) вырезаем первый массив [ ... ]
    if options is None:
        try:
            start = cleaned.find("[")
            end = cleaned.rfind("]")
            if start != -1 and end != -1 and end > start:
                options = json.loads(cleaned[start:end+1])
        except Exception:
            options = None

    # 3) fallback — по абзацам
    if options is None:
        options = [s.strip() for s in cleaned.split("\n\n") if s.strip()]

    # 4) если массив внутри строки
    if len(options) == 1 and isinstance(options[0], str) and options[0].lstrip().startswith("["):
        try:
            inner = _clean_code_fences(options[0])
            options = json.loads(inner)
        except Exception:
            pass

    # фильтр повторов
    normalized_history = {normalize(p) for p in history}
    unique: List[str] = []
    for o in options:
        if isinstance(o, dict):
            o = o.get("text", "")
        if not isinstance(o, str):
            continue
        o = o.strip()
        if not o:
            continue
        if normalize(o) in normalized_history:
            continue
        unique.append(o)

    return unique[:n]

def generate_reply_to_text(user_text: str) -> str:
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are Kenma Kozume (Haikyuu!!). Minimal, calm, slightly ironic. No emojis, no hashtags."},
            {"role": "user", "content": f"{get_today_context()}\nUser said: {user_text}\nReply briefly in English, then add a Russian translation below."}
        ],
        temperature=0.7,
        max_tokens=220,
    )
    return resp.choices[0].message.content.strip()

# ==== Команды ====
@bot.message_handler(commands=['start'])
def welcome(message):
    bot.send_message(
        message.chat.id,
        "Привет! Напиши текст — отвечу как Кенма.\n"
        "Команды:\n"
        "• /post — сгенерировать несколько вариантов и выбрать\n"
        "• /history — последние посты\n"
        "• /clear_history — очистить память\n"
        "• /import_history — импорт постов в память\n"
        "• /export_history — экспорт постов"
    )

@bot.message_handler(commands=['post', 'posts'])  # алиас /posts
def post_variants(message):
    try:
        parts = message.text.split()
        n = int(parts[1]) if len(parts) > 1 else DEFAULT_VARIANTS
        n = max(1, min(n, 6))
    except Exception:
        n = DEFAULT_VARIANTS

    if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
        bot.send_message(message.chat.id, "⛔️ You are not authorized to use this command.")
        return

    chat_id = message.chat.id
    user_id = message.from_user.id

    def worker():
        try:
            bot.send_chat_action(chat_id, 'typing')
            options = generate_posts(n)
            if not options:
                bot.send_message(chat_id, "Не смогла придумать варианты. Попробуй ещё раз.")
                return

            PENDING_OPTIONS[user_id] = options
            body = "Варианты:\n\n" + "\n\n".join(f"{i+1}) {opt}" for i, opt in enumerate(options))

            # Кнопки рядами по 3
            kb = InlineKeyboardMarkup()
            row = []
            for i in range(len(options)):
                row.append(InlineKeyboardButton(str(i+1), callback_data=f"pick:{i}"))
                if len(row) == 3:
                    kb.row(*row); row = []
            if row:
                kb.row(*row)
            kb.add(InlineKeyboardButton("Отменить", callback_data="pick:cancel"))

            bot.send_message(chat_id, body, reply_markup=kb)
        except Exception as e:
            bot.send_message(chat_id, f"Ошибка генерации: {e}")

    threading.Thread(target=worker, daemon=True).start()

@bot.callback_query_handler(func=lambda c: c.data and c.data.startswith("pick:"))
def on_pick(call):
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    choice = call.data.split(":", 1)[1]

    if choice == "cancel":
        PENDING_OPTIONS.pop(user_id, None)
        bot.answer_callback_query(call.id, "Отменено.")
        try:
            bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
        except Exception:
            pass
        return

    options = PENDING_OPTIONS.get(user_id)
    if not options:
        bot.answer_callback_query(call.id, "Нет активных вариантов.")
        return

    try:
        idx = int(choice)
        if not (0 <= idx < len(options)):
            raise ValueError
    except Exception:
        bot.answer_callback_query(call.id, "Некорректный выбор.")
        return

    picked = options[idx]
    PENDING_OPTIONS.pop(user_id, None)
    bot.answer_callback_query(call.id, f"Выбрано {idx+1}")
    bot.send_message(chat_id, picked)

    history = load_history()
    history.append(picked)
    save_history(history)

    try:
        bot.edit_message_reply_markup(chat_id=chat_id, message_id=call.message.message_id, reply_markup=None)
    except Exception:
        pass

@bot.message_handler(commands=['history'])
def show_history(message):
    history = load_history()
    if not history:
        bot.send_message(message.chat.id, "История пуста.")
        return
    out = "\n\n".join(f"{i+1}) {p}" for i, p in enumerate(history[-20:]))
    bot.send_message(message.chat.id, "Последние посты:\n\n" + out)

@bot.message_handler(commands=['clear_history'])
def clear_history(message):
    if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
        bot.send_message(message.chat.id, "⛔️ Not allowed.")
        return
    save_history([])
    bot.send_message(message.chat.id, "История очищена.")

@bot.message_handler(commands=['import_history'])
def import_history_cmd(message):
    if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
        bot.send_message(message.chat.id, "⛔️ Not allowed.")
        return

    # разбиваем по ЛЮБОМУ пробелу/переносу
    parts = message.text.split(None, 1)
    text_after = parts[1] if len(parts) > 1 else ""

    replied_text = ""
    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        replied_text = message.reply_to_message.text or message.reply_to_message.caption

    source_text = (text_after or replied_text).strip()

    # если текста нет — попробуем последний файл от этого юзера
    if not source_text:
        pending = LAST_FILE_BY_USER.pop(message.from_user.id, None)
        if pending:
            posts = parse_posts_from_text(pending)
            added = add_to_history(posts)
            bot.send_message(message.chat.id, f"Импортировано из последнего файла: {added} пост(ов).")
            return

    if source_text:
        posts = parse_posts_from_text(source_text)
        added = add_to_history(posts)
        bot.send_message(message.chat.id, f"Импортировано: {added} пост(ов).")
        return

    bot.send_message(message.chat.id,
        "Пришли .txt с постами (каждый пост отдели пустой строкой) и добавь подпись: /import_history\n"
        "Или пришли текст сразу после команды /import_history."
    )

@bot.message_handler(content_types=['document'])
def import_from_file(message):
    if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
        return

    caption = (message.caption or "").lower()
    pass_caption_mode = "/import_history" in caption

    if not message.document or not message.document.file_name.lower().endswith(".txt"):
        if pass_caption_mode:
            bot.send_message(message.chat.id, "Нужен .txt файл с постами. Каждый пост — через пустую строку.")
        return

    try:
        file_info = bot.get_file(message.document.file_id)
        file_bytes = bot.download_file(file_info.file_path)
        text = file_bytes.decode("utf-8", errors="ignore")

        if pass_caption_mode:
            posts = parse_posts_from_text(text)
            added = add_to_history(posts)
            bot.send_message(message.chat.id, f"Импортировано из файла: {added} пост(ов).")
        else:
            LAST_FILE_BY_USER[message.from_user.id] = text
            bot.send_message(message.chat.id, "Файл получен. Теперь пришли /import_history (можно просто отдельным сообщением).")
    except Exception as e:
        bot.send_message(message.chat.id, f"Не удалось прочитать файл: {e}")

@bot.message_handler(commands=['export_history'])
def export_history(message):
    if BOT_OWNER_ID and BOT_OWNER_ID != 0 and message.from_user.id != BOT_OWNER_ID:
        bot.send_message(message.chat.id, "⛔️ Not allowed.")
        return
    data = load_history()
    if not data:
        bot.send_message(message.chat.id, "История пуста.")
        return
    content = "\n\n".join(data)
    path = "history_export.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    with open(path, "rb") as f:
        bot.send_document(message.chat.id, f, caption="Экспорт истории")

# ==== Обычный текст (игнорим команды) ====
@bot.message_handler(func=lambda m: True, content_types=['text'])
def on_text(message):
    # фикс: не перехватывать команды
    if (message.text or "").startswith("/"):
        return

    chat_id = message.chat.id
    user_text = message.text or ""

    def worker():
        try:
            bot.send_chat_action(chat_id, 'typing')
            answer = generate_reply_to_text(user_text)
            bot.send_message(chat_id, answer)
        except Exception as e:
            bot.send_message(chat_id, f"Ошибка ответа: {e}")

    threading.Thread(target=worker, daemon=True).start()

# ==== Webhook ====
@app.route(f"/{BOT_TOKEN}", methods=["POST"])
def receive_update():
    try:
        raw = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(raw)
        threading.Thread(target=bot.process_new_updates, args=([update],), daemon=True).start()
        return "OK", 200
    except Exception as e:
        print("!! receive_update error:", e, flush=True)
        return "OK", 200

@app.route("/", methods=["GET"])
def index():
    return "Kenma bot is running."

# ==== Установка вебхука ====
bot.remove_webhook()
ok = bot.set_webhook(url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
print("Webhook set:", ok, f"{WEBHOOK_URL}/{BOT_TOKEN}", flush=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)








