#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import os
from datetime import datetime

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8924797159:AAHzZ1G5R6sKXPaHIOMu5xIhZtxq3ik2YFM"

# СПИСОК АДМИНОВ (ваши ID)
ADMIN_IDS = [1497899700, 1235335612]

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)


# --- ОБРАБОТЧИКИ КОМАНД ---

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    is_admin = user_id in ADMIN_IDS
    
    welcome_text = f"""
👋 *Здравствуйте, {user_name}!*

🤖 Я бот-уведомитель для магазина **"Мир Шаров"**.

📦 Моя задача — сообщать вам о новых заказах с сайта.

🔑 Команды:
/start — показать это сообщение
/help — список всех команд
/stats — статистика бота
/admin — информация для администраторов

💡 Статус: {'✅ Вы администратор, будете получать уведомления' if is_admin else '❌ Вы не администратор'}
    """
    
    await message.answer(welcome_text, parse_mode="Markdown")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    """Обработчик команды /help"""
    help_text = """
📚 *Список доступных команд:*

/start — приветственное сообщение
/help — этот список команд
/stats — статистика бота
/ping — проверка работоспособности
/admin — информация для администраторов

📦 *Для администраторов:*
Когда на сайте оформляется новый заказ, я автоматически присылаю уведомление в этот чат.

🔔 Уведомление содержит:
• Номер и дату заказа
• Информацию о клиенте
• Состав заказа
• Сумму
• Код отслеживания
    """
    
    await message.answer(help_text, parse_mode="Markdown")


@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    """Проверка работы бота"""
    start_time = datetime.now()
    await message.answer("🏓 Понг! Бот работает.")
    end_time = datetime.now()
    response_time = (end_time - start_time).total_seconds() * 1000
    await message.answer(f"⏱ Время ответа: {response_time:.0f} мс")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    """Статистика бота"""
    stats_text = f"""
📊 *Статистика бота:*

🔄 Статус: ✅ Работает
📅 Запущен: {datetime.now().strftime("%d.%m.%Y %H:%M")}
🤖 Версия: 1.0.0
👥 Администраторов: {len(ADMIN_IDS)}
📦 Ожидание заказов...

💡 Уведомления приходят автоматически при оформлении заказа на сайте.
    """
    
    await message.answer(stats_text, parse_mode="Markdown")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    """Информация для администраторов"""
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    
    admin_list = "\n".join([f"• `{aid}`" for aid in ADMIN_IDS]) if ADMIN_IDS else "• (пусто)"
    
    admin_text = f"""
🔐 *Информация для администраторов*

Ваш Telegram ID: `{user_id}`
Статус: {'✅ Вы администратор' if is_admin else '❌ Вы не администратор'}

{'📌 Вы будете получать уведомления о заказах.' if is_admin else ''}

💡 *Как стать администратором:*
1. Узнайте свой Telegram ID у бота @userinfobot
2. Добавьте ID в список ADMIN_IDS в файле bot.py
3. Перезапустите бота

📝 Текущий список администраторов:
{admin_list}
    """
    
    await message.answer(admin_text, parse_mode="Markdown")


# --- ОБРАБОТЧИКИ CALLBACK ---

@dp.callback_query(lambda c: c.data and c.data.startswith('view_order_'))
async def process_view_order(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки 'Посмотреть заказ'"""
    order_id = callback_query.data.replace('view_order_', '')
    await callback_query.answer(f"👀 Просмотр заказа #{order_id}")
    await callback_query.message.reply(
        f"🔍 Для просмотра заказа #{order_id} откройте админ-панель на сайте.\n\nhttps://mirsharov-pb.ru/?admin=mirsharov2026",
        parse_mode="Markdown"
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('process_order_'))
async def process_order(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки 'Отметить как обработанный'"""
    order_id = callback_query.data.replace('process_order_', '')
    
    await callback_query.answer(f"✅ Заказ #{order_id} отмечен как обработанный!")
    
    await callback_query.message.edit_text(
        text=callback_query.message.text + "\n\n✅ *Заказ отмечен как обработанный администратором.*",
        parse_mode="Markdown"
    )
    
    await callback_query.message.reply(
        f"✅ Заказ #{order_id} успешно отмечен как обработанный!",
        parse_mode="Markdown"
    )


# --- ЗАПУСК БОТА ---

async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Бот запускается...")
    
    if not BOT_TOKEN:
        logger.error("❌ Токен бота не найден!")
        return
    
    try:
        me = await bot.get_me()
        logger.info(f"✅ Бот успешно подключился к Telegram API")
        logger.info(f"📌 Имя бота: @{me.username}")
        logger.info(f"🆔 ID бота: {me.id}")
        logger.info(f"👥 Администраторы: {ADMIN_IDS}")
        
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Вебхук удален, используем polling")
        
        logger.info("📡 Начинаем прослушивание обновлений...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
