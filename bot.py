import asyncio
import os
import re
from aiohttp import web
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "7724227364:AAHR-xL-r_qX_0Xn9s2nJ5Q7k3K1v4m8L9o"  # Твой токен
ADMIN_ID = 5212520692  # Твой Telegram ID
# =============================================

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- ФЕЙКОВЫЙ ВЕБ-СЕРВЕР ДЛЯ RENDER ---
async def handle(request):
    return web.Response(text="Bot is running smoothly 24/7!")

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# --- ОБРАБОТЧИКИ БОТА ---
@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "👋 Привет! Отправь мне номер телефона и желаемый объем ГБ для покупки.\n\n"
        "Формат: `87001234567 5GB` или просто отправь текст/скриншот."
    )

@dp.message(F.text)
async def process_text(message: types.Message):
    # Уведомляем администратора о новой заявке
    text_to_admin = (
        f"📥 **Новая заявка!**\n\n"
        f"От: @{message.from_user.username or 'без_юзернейма'} (ID: `{message.from_user.id}`)\n"
        f"Текст:\n{message.text}"
    )
    await bot.send_message(chat_id=ADMIN_ID, text=text_to_admin, parse_mode="Markdown")
    await message.answer("✅ Ваша заявка принята и отправлена оператору!")

@dp.message(F.photo)
async def process_photo(message: types.Message):
    caption = message.caption or "Без описания"
    text_to_admin = (
        f"📸 **Новый скриншот/заявка!**\n\n"
        f"От: @{message.from_user.username or 'без_юзернейма'} (ID: `{message.from_user.id}`)\n"
        f"Подпись: {caption}"
    )
    # Пересылаем фото админу
    await bot.send_photo(
        chat_id=ADMIN_ID,
        photo=message.photo[-1].file_id,
        caption=text_to_admin,
        parse_mode="Markdown"
    )
    await message.answer("✅ Скриншот получен и передан оператору!")

# --- ГЛАВНАЯ ФУНКЦИЯ ЗАПУСКА ---
async def main():
    # 1. Запускаем веб-сервер, чтобы Render прошел Port Scan
    await start_web_server()
    print("🟢 Веб-сервер для Render запущен!")
    
    # 2. Запускаем бота
    print("🟢 Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

