import os
import json
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton

# Переменные окружения
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8943971285:AAGuRcUvBoj3fAB86DRSiVUow0l1TkxU94Y")
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "sk-....")  # замените на ваш ключ
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL_NAME = "deepseek-reasoner"
MAX_TOKENS = 1000
TEMPERATURE = 0.7

# Промпт
SYSTEM_PROMPT = """Ты — психотерапевтический ассистент. Ты не заменяешь живого специалиста, но создаёшь пространство для честного и бережного самоисследования.

Твой стиль: живой, тёплый, внимательный и глубокий. Отвечай коротко — 2-5 предложений. Не давай диагнозов и не рекомендуй лекарства.

Если пользователь говорит о желании причинить себе вред — немедленно предложи позвонить на горячую линию 112 или 8-800-2000-122."""

# Папка для истории
HISTORY_DIR = "histories"
os.makedirs(HISTORY_DIR, exist_ok=True)

def get_history_file(user_id: int) -> str:
    return os.path.join(HISTORY_DIR, f"user_{user_id}.json")

def load_history(user_id: int) -> list:
    file_path = get_history_file(user_id)
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

def save_history(user_id: int, messages: list):
    file_path = get_history_file(user_id)
    if len(messages) > 20:
        messages = messages[-20:]
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(messages, f, ensure_ascii=False, indent=2)

def clear_history(user_id: int):
    file_path = get_history_file(user_id)
    if os.path.exists(file_path):
        os.remove(file_path)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Создаём кнопки
    keyboard = [
        [KeyboardButton("🆕 Новый диалог")],
        [KeyboardButton("❓ Помощь")],
        # Добавьте другие кнопки, если нужно:
        # [KeyboardButton("📊 Моя статистика")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)
    
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — *Внутренний компас*. Я не даю советов и не ставлю диагнозов.\n"
        "Я здесь, чтобы помочь тебе *услышать себя*.\n\n"
        "Просто напиши, что тебя беспокоит, или используй кнопки ниже.",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 Просто напиши, что тебя беспокоит.\n\n"
        "Если ты в кризисе — позвони 112 или 8-800-2000-122."
    )

async def new_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    clear_history(update.effective_user.id)
    await update.message.reply_text("🧹 Начинаем новый диалог.")

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
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return "Извини, сейчас что-то пошло не так. Попробуй ещё раз."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    # Обработка кнопок
    if user_text == "🆕 Новый диалог":
        clear_history(user_id)
        await update.message.reply_text("🧹 Начинаем новый диалог. Расскажи, что у тебя сейчас внутри?")
        return
    
    if user_text == "❓ Помощь":
        await help_command(update, context)
        return
    
    # Остальной код обработки обычных сообщений...
    # (ваш существующий код)
    
    history = load_history(user_id)
    
    # УДАЛИТЕ этот блок (или закомментируйте):
    # if not history:
    #     user = update.effective_user
    #     await update.message.reply_text(...)
    
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history + [{"role": "user", "content": user_text}]
    reply = await call_deepseek(messages)
    
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    save_history(user_id, history)
    
    await update.message.reply_text(reply)

def main():
    # БЕЗ ПРОКСИ — обычный запуск
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_dialog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("✅ Бот запущен и работает...")
    app.run_polling()

if __name__ == "__main__":
    main()
