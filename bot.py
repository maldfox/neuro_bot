import os
import re
import sqlite3
import requests
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
ADMIN_ID = 824728893  # Ваш Telegram ID (для уведомлений и обратной связи)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_NAME = "deepseek-reasoner"
MAX_TOKENS = 1000
TEMPERATURE = 0.7

# ========== ПОСТОЯННОЕ ХРАНИЛИЩЕ ==========
DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'bot.db')

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Таблица истории
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Таблица пользователей (для счётчика)
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON history(user_id)')
    conn.commit()
    conn.close()
    print(f"✅ База данных готова: {DB_PATH}")

def register_user(user_id: int, username: str = None, first_name: str = None):
    """Регистрирует пользователя (если новый) и обновляет last_seen"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
    ''', (user_id, username, first_name))
    c.execute('''
        UPDATE users SET last_seen = CURRENT_TIMESTAMP
        WHERE user_id = ?
    ''', (user_id,))
    conn.commit()
    conn.close()

def get_user_stats() -> dict:
    """Возвращает статистику по пользователям"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Всего пользователей
    c.execute('SELECT COUNT(*) FROM users')
    total = c.fetchone()[0]
    # Новых за сегодня
    today = datetime.now().date()
    c.execute('''
        SELECT COUNT(*) FROM users 
        WHERE DATE(first_seen) = ?
    ''', (today,))
    new_today = c.fetchone()[0]
    conn.close()
    return {"total": total, "new_today": new_today}

def load_history(user_id: int, limit: int = 20) -> list:
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
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)',
              (user_id, "user", user_message))
    c.execute('INSERT INTO history (user_id, role, content) VALUES (?, ?, ?)',
              (user_id, "assistant", assistant_reply))
    conn.commit()
    conn.close()

def clear_history(user_id: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM history WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

init_db()

# ========== ПРОМПТ ==========
SYSTEM_PROMPT = """ВАЖНЕЙШЕЕ ПРАВИЛО: ТЫ ГОВОРИШЬ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. НИКАКИХ АНГЛИЙСКИХ ИЛИ КИТАЙСКИХ СЛОВ.

Ты — Внутренний компас. Ты не заменяешь живого специалиста, но создаёшь пространство для честного и бережного самоисследования.

Твой стиль: живой, тёплый, внимательный и глубокий. Отвечай коротко — 2-5 предложений. Не давай диагнозов и не рекомендуй лекарства.

Если пользователь говорит о желании причинить себе вред — немедленно предложи позвонить на горячую линию 112 или 8-800-2000-122."""

# ========== КНОПКИ ==========
def get_main_keyboard():
    keyboard = [
        [KeyboardButton("🆕 Новый диалог")],
        [KeyboardButton("ℹ️ О боте"), KeyboardButton("📝 Обратная связь")],
        [KeyboardButton("❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Регистрируем пользователя
    register_user(user_id, user.username, user.first_name)
    
    # Проверяем, новый ли пользователь
    stats = get_user_stats()
    
    # Если это первый запуск пользователя — уведомляем админа
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT first_seen FROM users WHERE user_id = ?', (user_id,))
    first_seen = c.fetchone()[0]
    conn.close()
    
    # Если пользователь зарегистрировался только что (first_seen совпадает с текущим временем в пределах минуты)
    if datetime.now() - datetime.fromisoformat(first_seen) < timedelta(seconds=60):
        print(f"Пытаюсь отправить уведомление админу {ADMIN_ID}")
try:
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📝 *Новая обратная связь*\n\n👤 От: {user.first_name} (@{user.username}) [ID: {user_id}]\n💬 Сообщение:\n{user_text}",
        parse_mode="Markdown"
    )
    print("✅ Уведомление отправлено")
except Exception as e:
    print(f"❌ Ошибка: {e}")
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 *Новый пользователь!*\n\n👤 {user.first_name} (@{user.username})\n📊 Всего пользователей: {stats['total']}",
            parse_mode="Markdown"
        )
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — *Внутренний компас*. Я не даю советов и не ставлю диагнозов.\n"
        "Я здесь, чтобы помочь тебе *услышать себя*.\n\n"
        "Ты можешь писать текстом или отправлять голосовые сообщения 🎤\n\n"
        "Просто напиши или скажи, что тебя беспокоит.",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def about_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «ℹ️ О боте»"""
    await update.message.reply_text(
        "🧭 *О боте «Внутренний компас»*\n\n"
        "Я — AI-ассистент, который помогает исследовать свои мысли и чувства.\n\n"
        "*Что я умею:*\n"
        "• Вести диалог, задавая глубокие вопросы\n"
        "• Помогать увидеть повторяющиеся паттерны\n"
        "• Создавать безопасное пространство для рефлексии\n\n"
        "*Чего я НЕ умею:*\n"
        "• Ставить диагнозы\n"
        "• Заменять живого психолога\n"
        "• Давать медицинские рекомендации\n\n"
        "Если ты в кризисе — позвони 112 или 8-800-2000-122.\n\n"
        "По вопросам работы бота пиши в форму обратной связи (кнопка «📝 Обратная связь»).",
        parse_mode="Markdown"
    )

# Состояния для обратной связи
FEEDBACK_STATE = {}

async def feedback_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «📝 Обратная связь» — начало формы"""
    user_id = update.effective_user.id
    FEEDBACK_STATE[user_id] = "waiting_for_feedback"
    
    await update.message.reply_text(
        "📝 *Форма обратной связи*\n\n"
        "Пожалуйста, напиши свой вопрос, замечание или пожелание.\n\n"
        "Если передумал(а) — нажми кнопку «❌ Отмена».",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("❌ Отмена")]], 
            resize_keyboard=True, 
            one_time_keyboard=True
        )
    )

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка «❌ Отмена»"""
    user_id = update.effective_user.id
    if user_id in FEEDBACK_STATE:
        del FEEDBACK_STATE[user_id]
    
    await update.message.reply_text(
        "Действие отменено. Возвращаюсь к обычному режиму.",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Помощь*\n\n"
        "Просто напиши или скажи, что тебя беспокоит.\n\n"
        "*Кнопки:*\n"
        "• 🆕 Новый диалог — начать заново\n"
        "• ℹ️ О боте — узнать о моих возможностях\n"
        "• 📝 Обратная связь — написать создателю\n\n"
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

async def send_stats_to_admin(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет статистику админу (вызывается по расписанию)"""
    stats = get_user_stats()
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📊 *Статистика пользователей*\n\n"
             f"👥 Всего: {stats['total']}\n"
             f"🆕 Новых сегодня: {stats['new_today']}",
        parse_mode="Markdown"
    )

# ========== ВЫЗОВ DEEPSEEK ==========
async def call_deepseek(messages: list) -> str:
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
        reply = re.sub(r'[a-zA-Z]', '', reply)
        return reply.strip()
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return "Извини, сейчас что-то пошло не так. Попробуй ещё раз."

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Регистрируем пользователя при любом сообщении
    user = update.effective_user
    register_user(user_id, user.username, user.first_name)
    
    # Обработка состояния обратной связи
    if user_id in FEEDBACK_STATE and FEEDBACK_STATE[user_id] == "waiting_for_feedback":
        # Отправляем обратную связь админу
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"📝 *Новая обратная связь*\n\n"
                 f"👤 От: {user.first_name} (@{user.username}) [ID: {user_id}]\n"
                 f"💬 Сообщение:\n{user_text}",
            parse_mode="Markdown"
        )
        del FEEDBACK_STATE[user_id]
        await update.message.reply_text(
            "✅ Спасибо за обратную связь! Я всё передал\n\n"
            "Если хочешь что-то добавить — снова нажми кнопку «📝 Обратная связь».",
            reply_markup=get_main_keyboard()
        )
        return
    
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
    
    if user_text == "ℹ️ О боте":
        await about_bot(update, context)
        return
    
    if user_text == "📝 Обратная связь":
        await feedback_start(update, context)
        return
    
    if user_text == "❌ Отмена":
        await cancel_action(update, context)
        return
    
    # Игнорируем команды
    if user_text.startswith('/'):
        return
    
    # Отправляем индикатор набора текста
    await update.message.chat.send_action(action="typing")
    
    # Загружаем историю
    history = load_history(user_id)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]
    
    reply = await call_deepseek(messages)
    
    save_history_pair(user_id, user_text, reply)
    await update.message.reply_text(reply, reply_markup=get_main_keyboard())

# ========== ЗАПУСК ==========
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_dialog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Ежедневная отправка статистики в 9 утра
    import pytz
    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(
            send_stats_to_admin,
            time=datetime.time(hour=9, minute=0),
            days=tuple(range(7)),
            data=app
        )
    
    print("✅ Бот «Внутренний компас» запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
