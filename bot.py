import os
import re
import sqlite3
import requests
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ (ключи не хранятся в коде) ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_NAME = "deepseek-reasoner"
MAX_TOKENS = 1000
TEMPERATURE = 0.7

# ========== ПОСТОЯННОЕ ХРАНИЛИЩЕ (данные не теряются при обновлении) ==========
DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'bot.db')

def init_db():
    """Создаёт таблицу для истории, если её нет"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON history(user_id)')
    conn.commit()
    conn.close()
    print(f"✅ База данных готова: {DB_PATH}")

def load_history(user_id: int, limit: int = 20) -> list:
    """Загружает последние limit сообщений пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT role, content FROM history 
        WHERE user_id = ? 
        ORDER BY timestamp DESC 
        LIMIT ?
    ''', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

def save_history_pair(user_id: int, user_message: str, assistant_reply: str):
    """Сохраняет пару сообщений (пользователь + ассистент)"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)',
              (user_id, "user", user_message))
    c.execute('INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)',
              (user_id, "assistant", assistant_reply))
    conn.commit()
    conn.close()

def clear_history(user_id: int):
    """Очищает всю историю пользователя"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# Инициализируем БД при старте
init_db()

# ========== ПРОМПТ ==========
SYSTEM_PROMPT = """ВАЖНЕЙШЕЕ ПРАВИЛО: ТЫ ГОВОРИШЬ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. НИКАКИХ АНГЛИЙСКИХ ИЛИ КИТАЙСКИХ СЛОВ.

Ты — Внутренний компас. Ты не заменяешь живого специалиста, но создаёшь пространство для честного и бережного самоисследования.

Твой стиль: живой, тёплый, внимательный и глубокий. Отвечай коротко — 2-5 предложений. Не давай диагнозов и не рекомендуй лекарства.

Если пользователь говорит о желании причинить себе вред — немедленно предложи позвонить на горячую линию 112 или 8-800-2000-122.

Перед отправкой ответа проверь: в нём нет ни одной английской или китайской буквы. Только русский язык."""

# ========== КНОПКИ ==========
def get_main_keyboard():
    """Возвращает клавиатуру с кнопками"""
    keyboard = [
        [KeyboardButton("🆕 Новый диалог")],
        [KeyboardButton("❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — *Внутренний компас*. Я не даю советов и не ставлю диагнозов.\n"
        "Я здесь, чтобы помочь тебе *услышать себя*.\n\n"
        "Просто напиши, что тебя беспокоит, или используй кнопки ниже.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Помощь*\n\n"
        "Просто напиши, что тебя беспокоит.\n\n"
        "Если ты в кризисе — позвони 112 или 8-800-2000-122.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def new_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text(
        "🧹 Начинаем новый диалог. Расскажи, что у тебя сейчас внутри?",
        reply_markup=get_main_keyboard()
    )

# ========== ВЫЗОВ DEEPSEEK ==========
async def call_deepseek(messages: list) -> str:
    """Вызов DeepSeek-R1 через официальный API"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
    }
    try:
        response = requests.post(DEEPSEEK_API_URL, headers=headers, json=payload, timeout=45)
        response.raise_for_status()
        result = response.json()
        reply = result["choices"][0]["message"]["content"]
        # Удаляем английские буквы на случай, если модель ошиблась
        reply = re.sub(r'[a-zA-Z]', '', reply)
        return reply.strip()
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return "Извини, сейчас что-то пошло не так. Попробуй ещё раз."

# ========== ОБРАБОТКА ТЕКСТОВЫХ СООБЩЕНИЙ ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Обработка кнопок
    if user_text == "🆕 Новый диалог":
        clear_history(user_id)
        await update.message.reply_text(
            "🧹 Начинаем новый диалог. Расскажи, что у тебя сейчас внутри?",
            reply_markup=get_main_keyboard()
        )
        return
    
    if user_text == "❓ Помощь":
        await help_command(update, context)
        return
    
    # Игнорируем команды
    if user_text.startswith('/'):
        return
    
    # Отправляем индикатор набора текста
    await update.message.chat.send_action(action="typing")
    
    # Загружаем историю
    history = load_history(user_id)
    
    # Формируем запрос к DeepSeek
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]
    
    # Получаем ответ
    reply = await call_deepseek(messages)
    
    # Сохраняем в историю
    save_history_pair(user_id, user_text, reply)
    
    # Отправляем ответ
    await update.message.reply_text(reply, reply_markup=get_main_keyboard())

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_dialog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Бот «Внутренний компас» запущен с постоянной БД...")
    app.run_polling()

if __name__ == "__main__":
    main()
