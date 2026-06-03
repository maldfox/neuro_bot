# bot.py
import json
import os
import requests
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from telegram.request import HTTPXRequest

from config import TELEGRAM_TOKEN, DEEPSEEK_API_KEY, DEEPSEEK_API_URL, MODEL_NAME, MAX_TOKENS, TEMPERATURE
from prompt import SYSTEM_PROMPT

# Папка для хранения истории диалогов
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
    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я — твой психотерапевтический ассистент. Я здесь, чтобы помочь тебе исследовать себя, "
        "а не давать быстрые советы или диагнозы.\n\n"
        "📝 *Как я работаю:*\n"
        "• Отвечаю коротко и по делу\n"
        "• Задаю глубокие вопросы\n"
        "• Помогаю увидеть то, что ты можешь не замечать\n\n"
        "🎮 *Режимы:*\n"
        "• `просто побудь рядом` — просто поддержка\n"
        "• `разбери глубоко` — глубокий разбор\n"
        "• `дай взгляд со стороны` — структурированный анализ\n"
        "• `помоги увидеть слепые зоны` — мягкие указания\n"
        "• `мне нужен только вопрос` — только один вопрос\n\n"
        "⚡ *Команды:*\n"
        "/new — начать новый диалог (очистить историю)\n"
        "/help — помощь\n\n"
        "Голосовые сообщения тоже поддерживаются 🎤\n\n"
        "С чего хочешь начать?"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Помощь*\n\n"
        "Просто напиши мне, что тебя беспокоит.\n\n"
        "*Режимы:*\n"
        "• `просто побудь рядом` — поддержка без анализа\n"
        "• `разбери глубоко` — глубокое исследование\n"
        "• `дай взгляд со стороны` — структура и факты\n"
        "• `помоги увидеть слепые зоны` — мягкие указания\n"
        "• `мне нужен только вопрос` — один сильный вопрос\n\n"
        "*Команды:*\n"
        "/new — начать новый диалог\n"
        "/help — это сообщение\n\n"
        "*Безопасность:*\n"
        "Если ты в кризисе — позвони 112 или 8-800-2000-122."
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")

async def new_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    clear_history(user_id)
    await update.message.reply_text("🧹 Начинаем новый диалог. Расскажи, что у тебя сейчас внутри?")

async def call_deepseek(messages: list) -> str:
    """Вызов DeepSeek-R1 через официальный API DeepSeek"""
    headers = {
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
        "Content-Type": "application/json",
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        "temperature": TEMPERATURE,
        "stream": False
    }
    
    try:
        response = requests.post(
            DEEPSEEK_API_URL,
            headers=headers,
            json=payload,
            timeout=45
        )
        response.raise_for_status()
        result = response.json()
        return result["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"DeepSeek API error: {e}")
        return "Извини, сейчас что-то пошло не так. Попробуй ещё раз или напиши позже."

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    
    if user_text.startswith('/'):
        return
    
    history = load_history(user_id)
    
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    
    await update.message.chat.send_action(action="typing")
    
    reply = await call_deepseek(messages)
    
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": reply})
    save_history(user_id, history)
    
    await update.message.reply_text(reply)

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    
    await update.message.chat.send_action(action="typing")
    
    voice = update.message.voice
    file = await context.bot.get_file(voice.file_id)
    
    ogg_path = f"temp_voice_{user_id}.ogg"
    await file.download_to_drive(ogg_path)
    
    await update.message.chat.send_action(action="typing")
    
    import openai
    
    openai.api_key = DEEPSEEK_API_KEY
    openai.base_url = "https://api.deepseek.com/v1"
    
    try:
        with open(ogg_path, "rb") as audio_file:
            transcription = openai.Audio.transcribe(
                model="whisper-1",
                file=audio_file,
                language="ru"
            )
        recognized_text = transcription.get("text", "")
        
        os.remove(ogg_path)
        
        if not recognized_text:
            await update.message.reply_text("Не удалось распознать голос. Попробуй записать чётче или напиши текстом.")
            return
        
        history = load_history(user_id)
        
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        messages.extend(history)
        messages.append({"role": "user", "content": f"[Голосовое сообщение]: {recognized_text}"})
        
        reply = await call_deepseek(messages)
        
        history.append({"role": "user", "content": f"[голос]: {recognized_text}"})
        history.append({"role": "assistant", "content": reply})
        save_history(user_id, history)
        
        await update.message.reply_text(f"🎤 *Вы сказали:* {recognized_text}\n\n{reply}", parse_mode="Markdown")
        
    except Exception as e:
        print(f"Voice error: {e}")
        await update.message.reply_text("Не получилось распознать голос. Попробуй написать текстом.")
        if os.path.exists(ogg_path):
            os.remove(ogg_path)

def main():
    # ПРАВИЛЬНЫЙ СПОСОБ ДЛЯ ВАШЕЙ ВЕРСИИ PTB
    # Используем proxy (не proxy_url) — это параметр HTTPXRequest
 #   PROXY = "socks5://192.252.208.71:14282"
 #   PROXY = "socks5://51.89.239.48:1080"
 #   PROXY = "socks5://45.140.166.115:1080"  
 #   PROXY = "socks5://185.211.238.144:1080"
 #   PROXY = "socks5://192.252.208.71:14282"
 #   PROXY = "socks5://91.214.109.181:1080"
 #   PROXY = "http://45.140.166.115:1080"
 #   PROXY = "http://185.211.238.144:1080"
    
    # Создаём объект HTTPXRequest с прокси
    request = HTTPXRequest(proxy=PROXY)
    
    # Создаём приложение с этим request
    app = Application.builder().token(TELEGRAM_TOKEN).request(request).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("new", new_dialog))
    
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    
    print("✅ Бот Внутренний Компас запущен с прокси...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()