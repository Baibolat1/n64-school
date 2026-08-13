import asyncio
import logging
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

# ================= НАСТРОЙКИ =================
BOT_TOKEN = "8279322641:AAHxc9bSjWLBuuvL8BIRJ5MblTZfMLqWV88"
ADMIN_IDS = [8787687661]

logging.basicConfig(level=logging.INFO)

# ================= БАЗА ДАННЫХ (В памяти) =================
db = {
    "users": {},      # user_id: {"name": ..., "balance": ..., "referrer": ...}
    "stats": {"total_calls": 0}
}

# ================= FSM (СОСТОЯНИЯ) =================
class AdminState(StatesGroup):
    broadcast_message = State()

router = Router()

# ================= КЛАВИАТУРЫ =================
def get_main_kb(user_id: int) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="👤 Мой Профиль", callback_data="profile"),
         InlineKeyboardButton(text="📊 Статистика", callback_data="stats")],
        [InlineKeyboardButton(text="🎁 Реферальная система", callback_data="ref_system")],
        [InlineKeyboardButton(text="⚙️ Помощь / Поддержка", callback_data="help")]
    ]
    if user_id in ADMIN_IDS:
        buttons.append([InlineKeyboardButton(text="👑 Админ Панель", callback_data="admin_panel")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])

def get_admin_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📢 Сделать рассылку", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📈 Полная аналитика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="⬅️ Назад в меню", callback_data="main_menu")]
    ])

# ================= ХЕНДЛЕРЫ: СТАРТ =================
@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    username = message.from_user.first_name

    # Регистрация пользователя
    if user_id not in db["users"]:
        args = message.text.split()
        referrer_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        
        db["users"][user_id] = {
            "name": username,
            "balance": 0,
            "referrer": referrer_id,
            "referrals_count": 0
        }
        
        if referrer_id and referrer_id in db["users"]:
            db["users"][referrer_id]["referrals_count"] += 1
            db["users"][referrer_id]["balance"] += 50
            try:
                await message.bot.send_message(
                    referrer_id, 
                    f"🎉 По вашей ссылке зарегистрировался **{username}**! Вам начислено 50 бонусов."
                )
            except Exception:
                pass

    text = (
        f"🚀 **Приветствую, {username}!**\n\n"
        f"Добро пожаловать в универсальный комбайн-бот.\n"
        f"Выбирай нужный раздел в меню ниже 👇"
    )
    await message.answer(text, reply_markup=get_main_kb(user_id), parse_mode="Markdown")

# ================= ХЕНДЛЕРЫ: КНОПКИ =================
@router.callback_query(F.data == "main_menu")
async def cb_main_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text(
        f"🚀 **Главное меню**\n\nВыберите нужный раздел:",
        reply_markup=get_main_kb(call.from_user.id),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "profile")
async def cb_profile(call: CallbackQuery):
    user_id = call.from_user.id
    u = db["users"].get(user_id, {"balance": 0, "referrals_count": 0})
    
    text = (
        f"👤 **Ваш профиль:**\n\n"
        f"🆔 **ID:** `{user_id}`\n"
        f"💰 **Баланс:** `{u['balance']}` бонусов\n"
        f"👥 **Приглашено друзей:** `{u['referrals_count']}`\n"
        f"👑 **Статус:** {'Администратор' if user_id in ADMIN_IDS else 'Пользователь'}"
    )
    await call.message.edit_text(text, reply_markup=get_back_kb(), parse_mode="Markdown")

@router.callback_query(F.data == "ref_system")
async def cb_ref(call: CallbackQuery):
    user_id = call.from_user.id
    bot_info = await call.bot.get_me()
    ref_link = f"https://t.me/{bot_info.username}?start={user_id}"
    
    text = (
        f"🎁 **Реферальная программа**\n\n"
        f"Приглашайте друзей и получайте **50 бонусов** за каждого!\n\n"
        f"🔗 Ваша ссылка:\n`{ref_link}`"
    )
    await call.message.edit_text(text, reply_markup=get_back_kb(), parse_mode="Markdown")

@router.callback_query(F.data == "stats")
async def cb_stats(call: CallbackQuery):
    total_users = len(db["users"])
    text = (
        f"📊 **Статистика бота:**\n\n"
        f"👥 Всего пользователей: `{total_users}`\n"
        f"⚡️ Состояние системы: `Отличное (24/7)`"
    )
    await call.message.edit_text(text, reply_markup=get_back_kb(), parse_mode="Markdown")

@router.callback_query(F.data == "help")
async def cb_help(call: CallbackQuery):
    text = (
        f"⚙️ **Поддержка и справка**\n\n"
        f"Если у вас возникли вопросы или ошибки в работе бота, "
        f"обратитесь к администратору."
    )
    await call.message.edit_text(text, reply_markup=get_back_kb(), parse_mode="Markdown")

# ================= АДМИН ПАНЕЛЬ =================
@router.callback_query(F.data == "admin_panel")
async def cb_admin(call: CallbackQuery):
    if call.from_user.id not in ADMIN_IDS:
        return await call.answer("❌ Доступ запрещен!", show_alert=True)
    
    await call.message.edit_text(
        "👑 **Панель Администратора**\n\nВыберите действие:",
        reply_markup=get_admin_kb(),
        parse_mode="Markdown"
    )

@router.callback_query(F.data == "admin_broadcast")
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    if call.from_user.id not in ADMIN_IDS:
        return
    
    await state.set_state(AdminState.broadcast_message)
    await call.message.edit_text(
        "📝 Отправьте сообщение (текст, фото или пост), которое нужно разослать всем пользователям:",
        reply_markup=get_back_kb()
    )

@router.message(AdminState.broadcast_message)
async def process_broadcast(message: Message, state: FSMContext):
    await state.clear()
    users = db["users"].keys()
    count = 0
    
    await message.answer("🚀 Рассылка запущена...")
    for uid in users:
        try:
            await message.copy_to(chat_id=uid)
            count += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
            
    await message.answer(f"✅ Рассылка завершена! Сообщение получили **{count}** пользователей.")

# ================= ЗАПУСК =================
async def main():
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    
    await bot.delete_webhook(drop_pending_updates=True)
    print("🟢 Бот успешно запущен и готов к работе!")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
