import os
import re
import sqlite3
import requests
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ ==========
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
DEEPSEEK_API_KEY = os.environ["DEEPSEEK_API_KEY"]
ADMIN_ID = 824728893  # Ваш Telegram ID (из @userinfobot)

DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_NAME = "deepseek-reasoner"
MAX_TOKENS = 1000
TEMPERATURE = 0.7

# ========== ПОСТОЯННОЕ ХРАНИЛИЩЕ ==========
DATA_DIR = os.getenv('DATA_DIR', '/app/data')
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, 'bot.db')

def init_db():
    """Создаёт все необходимые таблицы"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица истории диалогов
    c.execute('''
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица пользователей
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            first_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Таблица обратной связи
    c.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            message TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    c.execute('CREATE INDEX IF NOT EXISTS idx_user_id ON history(user_id)')
    conn.commit()
    conn.close()
    print(f"✅ База данных готова: {DB_PATH}")

# ========== РАБОТА С ПОЛЬЗОВАТЕЛЯМИ ==========
def register_user(user_id: int, username: str = None, first_name: str = None):
    """Регистрирует пользователя или обновляет время последнего визита"""
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
    c.execute('SELECT COUNT(*) FROM users')
    total = c.fetchone()[0]
    today = datetime.now().date()
    c.execute('SELECT COUNT(*) FROM users WHERE DATE(first_seen) = ?', (today,))
    new_today = c.fetchone()[0]
    conn.close()
    return {"total": total, "new_today": new_today}

# ========== РАБОТА С ИСТОРИЕЙ ==========
def load_history(user_id: int, limit: int = 20) -> list:
    """Загружает последние сообщения пользователя"""
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

def save_feedback(user_id: int, username: str, message: str):
    """Сохраняет обратную связь в базу"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        INSERT INTO feedback (user_id, username, message)
        VALUES (?, ?, ?)
    ''', (user_id, username, message))
    conn.commit()
    conn.close()

# Инициализируем базу данных
init_db()

# ========== ПРОМПТ ==========
SYSTEM_PROMPT = """ВАЖНЕЙШЕЕ ПРАВИЛО: ТЫ ГОВОРИШЬ ТОЛЬКО НА РУССКОМ ЯЗЫКЕ. НИКАКИХ АНГЛИЙСКИХ ИЛИ КИТАЙСКИХ СЛОВ.

Ты — Внутренний компас. Ты не заменяешь живого специалиста, но создаёшь пространство для честного и бережного самоисследования.

Твой стиль: живой, тёплый, внимательный и глубокий. Отвечай коротко — 2-5 предложений. Не давай диагнозов и не рекомендуй лекарства.

Если пользователь говорит о желании причинить себе вред — немедленно предложи позвонить на горячую линию 112 или 8-800-2000-122."""

# ========== КНОПКИ ==========
def get_main_keyboard():
    """Клавиатура с основными кнопками"""
    keyboard = [
        [KeyboardButton("🆕 Новый диалог")],
        [KeyboardButton("ℹ️ О боте"), KeyboardButton("📝 Обратная связь")],
        [KeyboardButton("❓ Помощь")],
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

def get_cancel_keyboard():
    """Клавиатура с кнопкой отмены"""
    return ReplyKeyboardMarkup([[KeyboardButton("❌ Отмена")]], resize_keyboard=True)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    
    # Регистрируем пользователя
    register_user(user_id, user.username, user.first_name)
    
    # Проверяем, новый ли пользователь
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT first_seen FROM users WHERE user_id = ?', (user_id,))
    first_seen = c.fetchone()[0]
    conn.close()
    
    # Если пользователь зарегистрировался только что (в пределах минуты)
    if datetime.now() - datetime.fromisoformat(first_seen) < timedelta(seconds=60):
        stats = get_user_stats()
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"🆕 *Новый пользователь!*\n\n👤 {user.first_name} (@{user.username})\n📊 Всего пользователей: {stats['total']}",
            parse_mode="Markdown"
        )
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — *Внутренний компас*. Я не заменяю живого психолога, но я здесь, чтобы помочь тебе *услышать себя*.\n\n"
        "*Важно:* Я — искусственный интеллект, но меня создал человек с психологическим образованием и живым опытом. \n\n"
        "Просто напиши, что тебя беспокоит. Если захочешь узнать обо мне больше — нажми «ℹ️ О боте».",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Помощь*\n\n"
        "Просто напиши, что тебя беспокоит.\n\n"
        "*Кнопки:*\n"
        "• 🆕 Новый диалог — начать заново\n"
        "• ℹ️ О боте — узнать о возможностях\n"
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

async def about_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🧭 *О боте «Внутренний компас»*\n\n"
        "Я — *искусственный интеллект*, а не человек. Но меня создал человек, который не понаслышке знает, что такое боль и радость, и имеет профессиональное психологическое образование.\n\n"
        "*Как я работаю:*\n"
        "• Мои ответы основаны на современных психологических подходах (КПТ, IFS, экзистенциальная терапия, теория привязанности)\n"
        "• Я не просто «робот, который отвечает», а бережный собеседник, который помогает исследовать себя\n"
        "• Я был обучен специально для одной цели — помочь тебе *услышать себя*\n\n"
        "*Чего я НЕ умею:*\n"
        "• Ставить диагнозы\n"
        "• Заменять живого психолога (особенно в кризисных ситуациях)\n"
        "• Давать медицинские рекомендации\n\n"
        "*Кто меня создал:*\n"
        "Меня разработал человек с психологическим образованием и живым опытом боли, потерь, радости и надежды. Моя архитектура и принципы работы продуманы так, чтобы диалог был безопасным и глубоким.\n\n"
        "*Когда стоит обратиться к живому специалисту:*\n"
        "• Если ты в кризисе или думаешь о самоповреждении\n"
        "• Если нужны лекарства или медицинское заключение\n"
        "• Если тебе нужна регулярная поддерживающая терапия\n\n"
        "*Обратная связь:*\n"
        "Если у тебя есть замечания или пожелания — нажми кнопку «📝 Обратная связь». Я передам их создателю.\n\n"
        "Спасибо, что доверяешь мне исследовать себя. 🙏",
        parse_mode="Markdown",
        reply_markup=get_main_keyboard()
    )

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отправляет статистику по команде /stats (только для админа)"""
    user_id = update.effective_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    
    stats = get_user_stats()
    await update.message.reply_text(
        f"📊 *Статистика пользователей*\n\n"
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
        # Удаляем английские буквы на случай, если модель ошиблась
        reply = re.sub(r'[a-zA-Z]', '', reply)
        return reply.strip()
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return "Извини, сейчас что-то пошло не так. Попробуй ещё раз."

# ========== ОБРАБОТКА СООБЩЕНИЙ ==========
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    user = update.effective_user
    
    # Регистрируем пользователя при любом сообщении
    register_user(user_id, user.username, user.first_name)
    
    # Обработка состояния ожидания обратной связи
    if context.user_data.get('waiting_feedback'):
        if user_text.lower() == "отмена" or user_text == "❌ Отмена":
            context.user_data['waiting_feedback'] = False
            await update.message.reply_text(
                "Обратная связь отменена.",
                reply_markup=get_main_keyboard()
            )
            return
        
        # Сохраняем обратную связь
        save_feedback(user_id, user.username, user_text)
        context.user_data['waiting_feedback'] = False
        
        # Отправляем уведомление админу
        try:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"📝 *Новая обратная связь*\n\n"
                     f"👤 От: {user.first_name} (@{user.username}) [ID: {user_id}]\n"
                     f"💬 Сообщение:\n{user_text}",
                parse_mode="Markdown"
            )
            print(f"✅ Уведомление отправлено админу {ADMIN_ID}")
        except Exception as e:
            print(f"❌ Ошибка при отправке уведомления: {e}")
        
        await update.message.reply_text(
            "✅ Спасибо за обратную связь! Я передал её создателю.\n\n"
            "Если хочешь что-то добавить — снова нажми кнопку «📝 Обратная связь».",
            reply_markup=get_main_keyboard()
        )
        return
    
    # Обработка кнопок и команд
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
        context.user_data['waiting_feedback'] = True
        await update.message.reply_text(
            "📝 *Форма обратной связи*\n\n"
            "Напиши свой вопрос, замечание или пожелание.\n\n"
            "Если передумал — нажми кнопку «❌ Отмена».",
            parse_mode="Markdown",
            reply_markup=get_cancel_keyboard()
        )
        return
    
    # Игнорируем команды (начинающиеся с /)
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
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Бот «Внутренний компас» запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
