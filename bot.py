#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import json
import os
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = ""
ADMIN_IDS = [1497899700, 1235335612]

SITE_URL = "https://mirsharov-pb.ru"
ORDERS_FILE = "orders.json"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

last_order_id = None
last_check_time = datetime.now() - timedelta(minutes=5)

# ==========================================
# КОНСТАНТЫ
# ==========================================
CHECK_INTERVAL = 300  # 5 минут между проверками

# ==========================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER (чтобы не убивал процесс)
# ==========================================

async def health_check(request):
    """Проверка здоровья для Render"""
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Запускает минимальный веб-сервер"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/ping', health_check)
    
    port = int(os.environ.get('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    await site.start()
    logger.info(f"🌐 Веб-сервер запущен на порту {port}")


# ==========================================
# ФУНКЦИЯ ОТПРАВКИ УВЕДОМЛЕНИЯ
# ==========================================

async def send_order_notification(order_data: dict):
    try:
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

        def escape_md(text):
            if not text:
                return ''
            chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
            for ch in chars:
                text = text.replace(ch, '\\' + ch)
            return text

        message = f"""
🛒 *НОВЫЙ ЗАКАЗ!*

📋 *Информация о заказе:*
🔢 Номер: `{order_id}`
📅 Дата: {order_date}
🔑 Код: `{track_key}`

👤 *Клиент:*
Имя: {escape_md(customer_name)}
📞 Телефон: {escape_md(customer_phone)}
💬 Мессенджер: {escape_md(messenger)}

📦 *Тип:* { '📍 Самовывоз' if delivery_type == 'pickup' else '🚚 Доставка' }
"""

        if delivery_type != 'pickup':
            address_parts = []
            if order_data.get('street'): address_parts.append(order_data['street'])
            if order_data.get('house'): address_parts.append(f"д. {order_data['house']}")
            if order_data.get('building'): address_parts.append(f"стр. {order_data['building']}")
            if order_data.get('apartment'): address_parts.append(f"кв. {order_data['apartment']}")
            address = ', '.join(address_parts) if address_parts else 'Не указан'
            message += f"""
📍 *Адрес:* {escape_md(address)}
🚪 Подъезд: {order_data.get('porch', '-')}
🏢 Этаж: {order_data.get('floor', '-')}
📞 Домофон: {order_data.get('intercom', '-')}
"""
            if order_data.get('leave_at_door'):
                message += "🔑 Оставить у двери: ✅ Да\n"
            if order_data.get('warn_delivery'):
                message += "📞 Предупредить о доставке: ✅ Да\n"

        if order_data.get('order_date'):
            message += f"""
📅 *Дата доставки:* {order_data['order_date']}
⏰ *Время:* {order_data.get('order_time', 'Не указано')}
"""

        if comment:
            message += f"""
💬 *Комментарий:*
{escape_md(comment)}
"""

        message += f"""
💰 *Сумма заказа:* {total} ₽

📋 *Состав заказа:*
"""

        if cart:
            for idx, item in enumerate(cart, 1):
                item_name = escape_md(item.get('name', 'Товар'))
                item_article = escape_md(item.get('article', '—'))
                item_price = item.get('price', 0)
                item_qty = item.get('quantity', 1)
                item_total = item_price * item_qty
                message += f"{idx}. {item_name} (Арт: {item_article}) — {item_price}₽ × {item_qty} = {item_total}₽\n"
        else:
            message += "❌ Состав заказа не указан\n"

        message += f"\n🔗 Посмотреть в админке: {SITE_URL}/?admin=mirsharov2026"

        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📦 Посмотреть заказ", callback_data=f"view_order_{order_id}")],
            [InlineKeyboardButton(text="✅ Отметить как обработанный", callback_data=f"process_order_{order_id}")]
        ])

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=message, parse_mode="Markdown", reply_markup=keyboard)
                logger.info(f"✅ Уведомление #{order_id} отправлено админу {admin_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки админу {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка в send_order_notification: {e}")


# ==========================================
# ПРОВЕРКА НОВЫХ ЗАКАЗОВ
# ==========================================

async def check_new_orders():
    global last_order_id, last_check_time

    try:
        url = f"{SITE_URL}/{ORDERS_FILE}"

        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    orders = await response.json()
                    if not orders:
                        return
                    latest_order = orders[0]
                    if latest_order.get('id') != last_order_id and not latest_order.get('notified', False):
                        await send_order_notification(latest_order)
                        last_order_id = latest_order.get('id')
                        last_check_time = datetime.now()
                        logger.info(f"✅ Новый заказ #{last_order_id} обнаружен!")
                    else:
                        logger.debug("Новых заказов нет")
                else:
                    logger.error(f"Ошибка получения заказов: {response.status}")

    except aiohttp.ClientError as e:
        logger.error(f"Ошибка соединения с сайтом: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Ошибка парсинга JSON: {e}")
    except Exception as e:
        logger.error(f"Неизвестная ошибка при проверке заказов: {e}")


# ==========================================
# ФОНОВАЯ ПРОВЕРКА (каждые 5 минут)
# ==========================================

async def periodic_check():
    logger.info(f"🔄 Запущена фоновая проверка заказов (каждые {CHECK_INTERVAL // 60} минут)")
    while True:
        try:
            await check_new_orders()
        except Exception as e:
            logger.error(f"Ошибка в periodic_check: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


# ==========================================
# КОМАНДЫ БОТА
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    is_admin = user_id in ADMIN_IDS

    welcome_text = f"""
👋 *Здравствуйте, {user_name}!*

🤖 Я бот-уведомитель для магазина *Мир Шаров*.

📦 Я автоматически проверяю новые заказы на сайте и присылаю уведомления.

🔑 Команды:
/start — показать это сообщение
/help — список всех команд
/stats — статистика бота
/check — принудительная проверка заказов
/admin — информация для администраторов

⏱ Проверка заказов: каждые 5 минут

💡 Статус: {'✅ Вы администратор' if is_admin else '❌ Вы не администратор'}
    """

    await message.answer(welcome_text, parse_mode="Markdown")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
📚 *Список команд:*

/start — приветственное сообщение
/help — этот список
/stats — статистика бота
/ping — проверка работы
/check — проверить новые заказы
/admin — информация об админах

📦 Бот автоматически проверяет заказы каждые 5 минут.
    """
    await message.answer(help_text, parse_mode="Markdown")


@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("🏓 Понг! Бот работает.")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    stats_text = f"""
📊 *Статистика:*

🔄 Статус: ✅ Работает
📅 Запущен: {datetime.now().strftime("%d.%m.%Y %H:%M")}
👥 Админов: {len(ADMIN_IDS)}
📦 Проверка: каждые {CHECK_INTERVAL // 60} минут
🕐 Последняя: {last_check_time.strftime("%H:%M:%S") if last_check_time else "—"}
    """
    await message.answer(stats_text, parse_mode="Markdown")


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    user_id = message.from_user.id
    if user_id not in ADMIN_IDS:
        await message.answer("❌ Только для админов.")
        return

    await message.answer("🔍 Проверяю заказы...")
    await check_new_orders()
    await message.answer("✅ Проверка завершена!")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS

    admin_list = "\n".join([f"• `{aid}`" for aid in ADMIN_IDS]) if ADMIN_IDS else "• (пусто)"

    admin_text = f"""
🔐 *Администраторы*

Ваш ID: `{user_id}`
Статус: {'✅ Администратор' if is_admin else '❌ Не админ'}

📝 Список админов:
{admin_list}
    """
    await message.answer(admin_text, parse_mode="Markdown")


# ==========================================
# CALLBACK
# ==========================================

@dp.callback_query(lambda c: c.data and c.data.startswith('view_order_'))
async def process_view_order(callback_query: types.CallbackQuery):
    order_id = callback_query.data.replace('view_order_', '')
    await callback_query.answer(f"👀 Заказ #{order_id}")
    await callback_query.message.reply(
        f"🔍 Просмотр: {SITE_URL}/?admin=mirsharov2026"
    )


@dp.callback_query(lambda c: c.data and c.data.startswith('process_order_'))
async def process_order(callback_query: types.CallbackQuery):
    order_id = callback_query.data.replace('process_order_', '')
    await callback_query.answer(f"✅ Заказ #{order_id} обработан!")
    await callback_query.message.edit_text(
        text=callback_query.message.text + "\n\n✅ *Обработан администратором.*",
        parse_mode="Markdown"
    )


# ==========================================
# ЗАПУСК
# ==========================================

async def main():
    logger.info("🚀 Бот запускается...")

    try:
        # Удаляем вебхук
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Вебхук удалён")
        
        await asyncio.sleep(2)
        
        me = await bot.get_me()
        logger.info(f"✅ Бот @{me.username} запущен")
        logger.info(f"👥 Админы: {ADMIN_IDS}")

        # ========== ВАЖНО: Запускаем веб-сервер ==========
        asyncio.create_task(start_web_server())
        logger.info("🌐 Веб-сервер запущен")

        # Запускаем фоновую проверку
        asyncio.create_task(periodic_check())
        logger.info(f"🔄 Проверка заказов запущена (каждые {CHECK_INTERVAL // 60} минут)")

        await check_new_orders()

        logger.info("📡 Начинаем прослушивание обновлений...")
        await dp.start_polling(bot, skip_updates=True)

    except Exception as e:
        logger.error(f"❌ Ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
