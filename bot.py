import os
import re
import sqlite3
import requests
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_NAME = "deepseek-reasoner"
MAX_TOKENS = 1000
TEMPERATURE = 0.7

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'bot.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, role TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON history(user_id)')
    conn.commit()
    conn.close()

def load_history(user_id: int, limit: int = 20) -> list:
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT role, content FROM history WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

def save_history_pair(user_id: int, user_message: str, assistant_reply: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)', (user_id, "user", user_message))
    c.execute('INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)', (user_id, "assistant", assistant_reply))
    conn.commit()
    conn.close()

def clear_history(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

init_db()

SYSTEM_PROMPT = """Ты — Внутренний компас. Ты помогаешь исследовать себя. Отвечай только на русском, коротко (2-5 предложений). Не ставь диагнозы. Если пользователь говорит о вреде себе — предложи позвонить 112."""

def get_main_keyboard():
    keyboard = [[KeyboardButton("🆕 Новый диалог")], [KeyboardButton("❓ Помощь")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Привет! Я — Внутренний компас. Напиши, что тебя беспокоит.", reply_markup=get_main_keyboard())

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Просто напиши, что чувствуешь. /new — начать заново.", reply_markup=get_main_keyboard())

async def new_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("Начинаем новый диалог.", reply_markup=get_main_keyboard())

async def call_deepseek(messages: list) -> str:
    headers = {"Authorization": f"Bearer {DEEPSEEK_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": MODEL_NAME, "messages": messages, "max_tokens": MAX_TOKENS, "temperature": TEMPERATURE}
    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        reply = response.json()["choices"][0]["message"]["content"]
        return re.sub(r'[a-zA-Z]', '', reply).strip()
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return "Извини, ошибка. Попробуй ещё раз."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    if text == "🆕 Новый диалог":
        clear_history(user_id)
        await update.message.reply_text("Новый диалог начат.", reply_markup=get_main_keyboard())
        return
    if text == "❓ Помощь":
        await help_command(update, context)
        return
    if text.startswith('/'):
        return
    history = load_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": text}]
    reply = await call_deepseek(messages)
    save_history_pair(user_id, text, reply)
    await update.message.reply_text(reply, reply_markup=get_main_keyboard())

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_dialog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("✅ Бот запущен")
    app.run_polling()

if __name__ == "__main__":
    main()
