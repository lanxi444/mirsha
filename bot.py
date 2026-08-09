#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = "8924797159:AAHzZ1G5R6sKXPaHIOMu5xIhZtxq3ik2YFM"
ADMIN_IDS = [1497899700, 1235335612]

# URL вашего сайта
SITE_URL = "https://mirsharov-pb.ru"
ORDERS_FILE = "orders.json"  # на сайте

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

# Храним ID последнего обработанного заказа
last_order_id = None
# Храним время последней проверки
last_check_time = datetime.now() - timedelta(minutes=5)


# ==========================================
# ФУНКЦИЯ ОТПРАВКИ УВЕДОМЛЕНИЯ
# ==========================================

async def send_order_notification(order_data: dict):
    """Отправляет уведомление о заказе всем администраторам"""
    try:
        # Формируем сообщение
        order_id = order_data.get('id', 'Неизвестно')
        order_date = order_data.get('date', datetime.now().strftime("%d.%m.%Y %H:%M"))
        customer_name = order_data.get('name', 'Не указано')
        customer_phone = order_data.get('phone', 'Не указан')
        messenger = order_data.get('messenger', 'Не указан')
        delivery_type = order_data.get('delivery_type', 'delivery')
        total = order_data.get('total', 0)
        track_key = order_data.get('track_key', 'Не указан')
        cart = order_data.get('cart', [])
        comment = order_data.get('comment', '')
        
        message = f"""
🛒 *НОВЫЙ ЗАКАЗ!*

📋 *Информация о заказе:*
🔢 Номер: `{order_id}`
📅 Дата: {order_date}
🔑 Код отслеживания: `{track_key}`

👤 *Клиент:*
Имя: {customer_name}
📞 Телефон: {customer_phone}
💬 Мессенджер: {messenger}

📦 *Тип получения:*
{ '📍 Самовывоз' if delivery_type == 'pickup' else '🚚 Доставка' }
"""
        
        if delivery_type != 'pickup':
            address_parts = []
            if order_data.get('street'): address_parts.append(order_data['street'])
            if order_data.get('house'): address_parts.append(f"д. {order_data['house']}")
            if order_data.get('building'): address_parts.append(f"стр. {order_data['building']}")
            if order_data.get('apartment'): address_parts.append(f"кв. {order_data['apartment']}")
            
            address = ', '.join(address_parts) if address_parts else 'Не указан'
            
            message += f"""
📍 *Адрес доставки:*
{address}
🚪 Подъезд: {order_data.get('porch', '-')}
🏢 Этаж: {order_data.get('floor', '-')}
📞 Домофон: {order_data.get('intercom', '-')}
"""
            
            if order_data.get('leave_at_door'):
                message += "🔑 *Оставить у двери:* ✅ Да\n"
            if order_data.get('warn_delivery'):
                message += "📞 *Предупредить о доставке:* ✅ Да\n"
        
        if order_data.get('order_date'):
            message += f"""
📅 *Дата доставки:* {order_data['order_date']}
⏰ *Время:* {order_data.get('order_time', 'Не указано')}
"""
        
        if comment:
            message += f"""
💬 *Комментарий к заказу:*
{comment}
"""
        
        message += f"""
💰 *Сумма заказа:* {total} ₽

📋 *Состав заказа:*
"""
        
        if cart:
            for idx, item in enumerate(cart, 1):
                item_name = item.get('name', 'Товар')
                item_article = item.get('article', '—')
                item_price = item.get('price', 0)
                item_qty = item.get('quantity', 1)
                item_total = item_price * item_qty
                
                message += f"{idx}. {item_name} (Арт: {item_article}) — {item_price}₽ × {item_qty} = {item_total}₽\n"
        else:
            message += "❌ Состав заказа не указан\n"
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📦 Посмотреть заказ", callback_data=f"view_order_{order_id}")
            ],
            [
                InlineKeyboardButton(text="✅ Отметить как обработанный", callback_data=f"process_order_{order_id}")
            ]
        ])
        
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    chat_id=admin_id,
                    text=message,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                logger.info(f"Уведомление о заказе #{order_id} отправлено админу {admin_id}")
            except Exception as e:
                logger.error(f"Не удалось отправить уведомление админу {admin_id}: {e}")
                
    except Exception as e:
        logger.error(f"Ошибка при отправке уведомления о заказе: {e}")


# ==========================================
# ПРОВЕРКА НОВЫХ ЗАКАЗОВ
# ==========================================

async def check_new_orders():
    """Проверяет наличие новых заказов на сайте"""
    global last_order_id, last_check_time
    
    try:
        # Получаем файл с заказами с сайта
        url = f"{SITE_URL}/{ORDERS_FILE}"
        
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    orders = await response.json()
                    
                    if not orders:
                        return
                    
                    # Сортируем заказы по времени (новые сверху)
                    # В вашем orders.json заказы хранятся в порядке добавления
                    # (новые в начале массива)
                    
                    # Проверяем первый заказ (самый новый)
                    latest_order = orders[0]
                    
                    # Если это новый заказ (не отправляли уведомление)
                    if latest_order.get('id') != last_order_id and not latest_order.get('notified', False):
                        # Отправляем уведомление
                        await send_order_notification(latest_order)
                        
                        # Отмечаем заказ как уведомлённый (сохраняем на сайте?)
                        # Вариант: отправляем запрос на сайт, чтобы отметить заказ
                        # Или просто запоминаем ID в памяти бота
                        last_order_id = latest_order.get('id')
                        
                        # Отмечаем заказ как обработанный в памяти бота
                        logger.info(f"✅ Новый заказ #{last_order_id} обнаружен и уведомление отправлено")
                        
                        # Обновляем время проверки
                        last_check_time = datetime.now()
                        
                        # Попробуем отметить заказ на сайте (через API)
                        try:
                            mark_url = f"{SITE_URL}/api.php?action=mark_order_notified&id={last_order_id}"
                            async with session.get(mark_url, timeout=5) as mark_response:
                                if mark_response.status == 200:
                                    logger.info(f"✅ Заказ #{last_order_id} отмечен как уведомлённый на сайте")
                        except Exception as e:
                            logger.warning(f"Не удалось отметить заказ на сайте: {e}")
                    else:
                        logger.debug(f"Новых заказов нет. Последний ID: {last_order_id}")
                else:
                    logger.error(f"Ошибка получения заказов: {response.status}")
                    
    except aiohttp.ClientError as e:
        logger.error(f"Ошибка соединения с сайтом: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при проверке заказов: {e}")


async def periodic_check():
    """Запускает проверку заказов каждые 15 секунд"""
    logger.info("🔄 Запущена фоновая проверка заказов (каждые 15 секунд)")
    while True:
        try:
            await check_new_orders()
        except Exception as e:
            logger.error(f"Ошибка в periodic_check: {e}")
        await asyncio.sleep(15)  # Проверяем каждые 15 секунд


# ==========================================
# КОМАНДЫ БОТА
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    is_admin = user_id in ADMIN_IDS
    
    welcome_text = f"""
👋 *Здравствуйте, {user_name}!*

🤖 Я бот-уведомитель для магазина **"Мир Шаров"**.

📦 Я автоматически проверяю новые заказы на сайте и присылаю уведомления.

🔑 Команды:
/start — показать это сообщение
/help — список всех команд
/stats — статистика бота
/check — принудительная проверка заказов
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
/check — принудительно проверить новые заказы
/admin — информация для администраторов

📦 *Для администраторов:*
Бот автоматически проверяет новые заказы каждые 15 секунд.
При появлении нового заказа вы получаете уведомление.
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
🤖 Версия: 2.0.0 (с авто-проверкой)
👥 Администраторов: {len(ADMIN_IDS)}
📦 Проверка заказов: каждые 15 секунд
🕐 Последняя проверка: {last_check_time.strftime("%H:%M:%S") if last_check_time else "—"}

💡 Уведомления приходят автоматически при появлении новых заказов.
    """
    
    await message.answer(stats_text, parse_mode="Markdown")


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    """Принудительная проверка заказов"""
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Эта команда доступна только администраторам.")
        return
    
    await message.answer("🔍 Выполняю проверку заказов...")
    await check_new_orders()
    await message.answer("✅ Проверка завершена!")


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


# ==========================================
# ОБРАБОТЧИКИ CALLBACK
# ==========================================

@dp.callback_query(lambda c: c.data and c.data.startswith('view_order_'))
async def process_view_order(callback_query: types.CallbackQuery):
    """Обработчик нажатия кнопки 'Посмотреть заказ'"""
    order_id = callback_query.data.replace('view_order_', '')
    await callback_query.answer(f"👀 Просмотр заказа #{order_id}")
    await callback_query.message.reply(
        f"🔍 Для просмотра заказа #{order_id} откройте админ-панель на сайте.\n\n{SITE_URL}/?admin=mirsharov2026",
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


# ==========================================
# ЗАПУСК БОТА
# ==========================================

async def main():
    """Главная функция запуска бота"""
    global last_order_id, last_check_time
    
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
        logger.info(f"🌐 Сайт: {SITE_URL}")
        
        # Удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Вебхук удален, используем polling")
        
        # Запускаем фоновую проверку заказов
        asyncio.create_task(periodic_check())
        logger.info("🔄 Фоновая проверка заказов запущена")
        
        # Инициализируем last_order_id — получаем последний заказ
        logger.info("📦 Загружаем последний заказ...")
        await check_new_orders()
        
        logger.info("📡 Начинаем прослушивание обновлений...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
