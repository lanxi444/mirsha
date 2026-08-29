#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import json
import os
import re
import html
import urllib.parse
from datetime import datetime, timedelta
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, CommandObject
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = [1497899700, 1235335612]

SITE_URL = "https://mirsharov-pb.ru"
ORDERS_FILE = "orders.json"
CHECK_INTERVAL = 300  # 5 минут между фоновыми проверками

if not BOT_TOKEN:
    raise ValueError("Переменная окружения BOT_TOKEN не найдена или пуста! Укажите её в панели Render (Environment).")

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

# Словари статусов
STATUS_NAMES = {
    "work": "⏳ В работе",
    "build": "🎈 Собирается (надув)",
    "delivery": "🚚 Передан курьеру",
    "done": "✅ Выполнен",
    "cancel": "❌ Отменён"
}


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def clean_phone_number(phone: str) -> str:
    """Очищает номер телефона для WhatsApp"""
    if not phone:
        return ""
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits

def parse_order_date(date_str: str):
    """Парсинг даты заказа из различных форматов"""
    if not date_str:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt)
        except ValueError:
            pass
    return None

async def fetch_all_orders() -> list:
    """Загрузка списка всех заказов с сайта"""
    url = f"{SITE_URL}/{ORDERS_FILE}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=15) as response:
                if response.status == 200:
                    return await response.json()
                logger.error(f"Ошибка получения заказов: {response.status}")
    except Exception as e:
        logger.error(f"Ошибка соединения с сайтом при загрузке заказов: {e}")
    return []


# ==========================================
# КЛАВИАТУРЫ И ФОРМАТИРОВАНИЕ ЗАКАЗА
# ==========================================

def build_order_keyboard(order_id: str, phone: str = "", address: str = "", delivery_type: str = "delivery") -> InlineKeyboardMarkup:
    """Генерация интерактивной клавиатуры для заказа"""
    buttons = []
    
    # 1-я строка: Быстрые действия
    row_actions = []
    clean_phone = clean_phone_number(phone)
    if clean_phone:
        row_actions.append(InlineKeyboardButton(text="💬 WhatsApp", url=f"https://wa.me/{clean_phone}"))
    
    if delivery_type != 'pickup' and address and address != 'Не указан':
        encoded_address = urllib.parse.quote(address)
        maps_url = f"https://yandex.ru/maps/?text={encoded_address}"
        row_actions.append(InlineKeyboardButton(text="🗺 На карте", url=maps_url))
        
    row_actions.append(InlineKeyboardButton(text="⚙️ В админку", url=f"{SITE_URL}/?admin=mirsharov2026"))
    buttons.append(row_actions)

    # 2-я и 3-я строки: Управление статусами
    buttons.append([
        InlineKeyboardButton(text="⏳ В работе", callback_data=f"st_{order_id}_work"),
        InlineKeyboardButton(text="🎈 Собирается", callback_data=f"st_{order_id}_build"),
        InlineKeyboardButton(text="🚚 У курьера", callback_data=f"st_{order_id}_delivery"),
    ])
    buttons.append([
        InlineKeyboardButton(text="✅ Выполнен", callback_data=f"st_{order_id}_done"),
        InlineKeyboardButton(text="❌ Отменён", callback_data=f"st_{order_id}_cancel"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def format_order_card(order_data: dict, status_info: str = None) -> tuple[str, str, str, str]:
    """Формирует HTML-текст карточки заказа"""
    order_id = html.escape(str(order_data.get('id', 'Неизвестно')))
    order_date = html.escape(str(order_data.get('date', datetime.now().strftime("%d.%m.%Y %H:%M"))))
    customer_name = html.escape(str(order_data.get('name', 'Не указано')))
    customer_phone = html.escape(str(order_data.get('phone', 'Не указан')))
    messenger = html.escape(str(order_data.get('messenger', 'Не указан')))
    delivery_type = order_data.get('delivery_type', 'delivery')
    total = order_data.get('total', 0)
    track_key = html.escape(str(order_data.get('track_key', '—')))
    cart = order_data.get('cart', [])
    comment = html.escape(str(order_data.get('comment', '')))

    text = f"🛒 <b>НОВЫЙ ЗАКАЗ!</b>\n\n"
    text += f"📋 <b>Информация о заказе:</b>\n"
    text += f"🔢 Номер: <code>{order_id}</code>\n"
    text += f"📅 Дата: {order_date}\n"
    text += f"🔑 Код: <code>{track_key}</code>\n\n"

    text += f"👤 <b>Клиент:</b>\n"
    text += f"Имя: <b>{customer_name}</b>\n"
    text += f"📞 Телефон: <code>{customer_phone}</code>\n"
    text += f"💬 Мессенджер: {messenger}\n\n"

    text += f"📦 <b>Тип:</b> {'📍 <b>Самовывоз</b>' if delivery_type == 'pickup' else '🚚 <b>Доставка</b>'}\n"

    raw_address = ""
    if delivery_type != 'pickup':
        address_parts = []
        if order_data.get('street'): address_parts.append(order_data['street'])
        if order_data.get('house'): address_parts.append(f"д. {order_data['house']}")
        if order_data.get('building'): address_parts.append(f"стр. {order_data['building']}")
        if order_data.get('apartment'): address_parts.append(f"кв. {order_data['apartment']}")
        raw_address = ', '.join(address_parts) if address_parts else 'Не указан'
        
        text += f"📍 <b>Адрес:</b> {html.escape(raw_address)}\n"
        text += f"🚪 Подъезд: {html.escape(str(order_data.get('porch', '-')))} | "
        text += f"🏢 Этаж: {html.escape(str(order_data.get('floor', '-')))} | "
        text += f"📞 Домофон: {html.escape(str(order_data.get('intercom', '-')))}\n"
        
        if order_data.get('leave_at_door'):
            text += "🔑 Оставить у двери: ✅ Да\n"
        if order_data.get('warn_delivery'):
            text += "📞 Предупредить о доставке: ✅ Да\n"

    if order_data.get('order_date'):
        text += f"📅 <b>Желаемая дата:</b> {html.escape(str(order_data['order_date']))}\n"
        text += f"⏰ <b>Время:</b> {html.escape(str(order_data.get('order_time', 'Не указано')))}\n"

    if comment:
        text += f"\n💬 <b>Комментарий:</b>\n<i>{comment}</i>\n"

    text += f"\n💰 <b>Сумма заказа:</b> <b>{total} ₽</b>\n\n"
    text += "📋 <b>Состав заказа:</b>\n"

    if cart:
        for idx, item in enumerate(cart, 1):
            item_name = html.escape(str(item.get('name', 'Товар')))
            item_article = html.escape(str(item.get('article', '—')))
            item_price = item.get('price', 0)
            item_qty = item.get('quantity', 1)
            item_total = item_price * item_qty
            text += f"{idx}. {item_name} (Арт: <code>{item_article}</code>) — {item_price}₽ × {item_qty} = <b>{item_total}₽</b>\n"
    else:
        text += "❌ Состав заказа не указан\n"

    if status_info:
        text += f"\n➖➖➖➖➖➖➖➖\n{status_info}"

    return text, customer_phone, raw_address, delivery_type


# ==========================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER
# ==========================================

async def health_check(request):
    """Проверка доступности для Render и cron-job.org"""
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Запускает веб-сервер для поддержания активности"""
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
# ОТПРАВКА И ПРОВЕРКА ЗАКАЗОВ
# ==========================================

async def send_order_notification(order_data: dict):
    try:
        order_id = str(order_data.get('id', 'Неизвестно'))
        text, phone, address, delivery_type = format_order_card(order_data)
        keyboard = build_order_keyboard(order_id, phone, address, delivery_type)

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML", reply_markup=keyboard)
                logger.info(f"✅ Уведомление #{order_id} отправлено админу {admin_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки админу {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка в send_order_notification: {e}")


async def check_new_orders():
    global last_order_id, last_check_time

    try:
        orders = await fetch_all_orders()
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

    except Exception as e:
        logger.error(f"Ошибка при проверке заказов: {e}")


async def periodic_check():
    logger.info(f"🔄 Запущена фоновая проверка заказов (каждые {CHECK_INTERVAL // 60} минут)")
    while True:
        try:
            await check_new_orders()
        except Exception as e:
            logger.error(f"Ошибка в periodic_check: {e}")
        await asyncio.sleep(CHECK_INTERVAL)


# ==========================================
# ОБРАБОТКА ИЗМЕНЕНИЯ СТАТУСА
# ==========================================

@dp.callback_query(F.data.startswith("st_"))
async def process_status_change(callback: types.CallbackQuery):
    try:
        parts = callback.data.split("_")
        if len(parts) < 3:
            await callback.answer("Ошибка формата статуса")
            return
        
        order_id = parts[1]
        status_key = parts[2]
        status_title = STATUS_NAMES.get(status_key, "Обновлён")
        admin_name = callback.from_user.full_name
        current_time = datetime.now().strftime("%H:%M")

        status_text = f"📌 <b>Статус:</b> {status_title}\n👤 <b>Изменил:</b> {html.escape(admin_name)} (в {current_time})"

        # Сохраняем исходный текст и обновляем блок статуса
        current_msg = callback.message.text or callback.message.caption or ""
        base_text = current_msg.split("➖➖➖➖➖➖➖➖")[0].strip()
        
        # Переводим базовый текст в безопасный HTML вид
        new_text = f"{html.escape(base_text)}\n\n➖➖➖➖➖➖➖➖\n{status_text}"

        await callback.message.edit_text(
            text=new_text,
            parse_mode="HTML",
            reply_markup=callback.message.reply_markup
        )
        await callback.answer(f"Статус заказа #{order_id}: {status_title}")
        logger.info(f"Админ {admin_name} сменил статус #{order_id} на {status_title}")

    except Exception as e:
        logger.error(f"Ошибка смены статуса: {e}")
        await callback.answer("Статус обновлён!")


# ==========================================
# КОМАНДЫ БОТА
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    user_name = message.from_user.full_name
    is_admin = user_id in ADMIN_IDS

    welcome_text = f"""
👋 <b>Здравствуйте, {html.escape(user_name)}!</b>

🤖 Я бот управления заказами магазина <b>Мир Шаров</b>.

📦 <b>Доступные команды:</b>
/today — Сводка продаж и выручки за сегодня
/month — Статистика за текущий месяц
/recent — Список последних 5 заказов
/find <code>номер</code> — Поиск заказа по номеру или телефону
/check — Принудительная проверка новых заказов
/stats — Техническое состояние бота
/help — Справка

⏱ Фоновая проверка сайта: <b>каждые 5 минут</b>
💡 Статус доступа: <b>{'✅ Администратор' if is_admin else '❌ Доступ ограничен'}</b>
    """
    await message.answer(welcome_text, parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
📚 <b>Справочник команд:</b>

📊 <b>Аналитика:</b>
• /today — заказы, выручка и средний чек за сегодня.
• /month — общие итоги за текущий месяц.

🔍 <b>Заказы:</b>
• /recent — показать 5 последних заказов.
• /find <code>запрос</code> — поиск (например: <code>/find 105</code> или <code>/find 9999</code>).
• /check — проверить сайт прямо сейчас.

⚙️ <b>Системные:</b>
• /ping — проверка отклика.
• /stats — статус подключения и последняя проверка.
• /admin — список ID администраторов.
    """
    await message.answer(help_text, parse_mode="HTML")


@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("🏓 <b>Понг!</b> Бот на Render работает стабильно.", parse_mode="HTML")


@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    orders = await fetch_all_orders()
    today_date = datetime.now().date()

    today_orders = []
    for o in orders:
        dt = parse_order_date(o.get('date'))
        if dt and dt.date() == today_date:
            today_orders.append(o)

    count = len(today_orders)
    total_sum = sum(o.get('total', 0) for o in today_orders)
    avg_check = int(total_sum / count) if count > 0 else 0
    deliveries = sum(1 for o in today_orders if o.get('delivery_type') != 'pickup')
    pickups = count - deliveries

    report = f"""
📊 <b>Сводка за сегодня ({today_date.strftime("%d.%m.%Y")}):</b>

🛍 <b>Всего заказов:</b> {count} шт.
💰 <b>Общая выручка:</b> <b>{total_sum:,} ₽</b>
📈 <b>Средний чек:</b> {avg_check:,} ₽

🚚 Доставка курьером: <b>{deliveries}</b>
📍 Самовывоз: <b>{pickups}</b>
    """
    await message.answer(report, parse_mode="HTML")


@dp.message(Command("month"))
async def cmd_month(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    orders = await fetch_all_orders()
    now = datetime.now()

    month_orders = []
    for o in orders:
        dt = parse_order_date(o.get('date'))
        if dt and dt.month == now.month and dt.year == now.year:
            month_orders.append(o)

    count = len(month_orders)
    total_sum = sum(o.get('total', 0) for o in month_orders)
    avg_check = int(total_sum / count) if count > 0 else 0

    report = f"""
📅 <b>Итоги месяца ({now.strftime("%B %Y")}):</b>

🛍 <b>Заказов за месяц:</b> {count} шт.
💰 <b>Суммарная выручка:</b> <b>{total_sum:,} ₽</b>
📈 <b>Средний чек:</b> {avg_check:,} ₽
    """
    await message.answer(report, parse_mode="HTML")


@dp.message(Command("recent"))
async def cmd_recent(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    orders = await fetch_all_orders()
    if not orders:
        await message.answer("📭 Список заказов пуст.")
        return

    recent_5 = orders[:5]
    text = "📋 <b>Последние 5 заказов:</b>\n\n"

    for o in recent_5:
        oid = html.escape(str(o.get('id', '—')))
        name = html.escape(str(o.get('name', 'Клиент')))
        phone = html.escape(str(o.get('phone', '—')))
        total = o.get('total', 0)
        dtype = "📍 Самовывоз" if o.get('delivery_type') == 'pickup' else "🚚 Доставка"
        odate = html.escape(str(o.get('date', '—')))

        text += f"🔹 <b>#{oid}</b> ({odate})\n"
        text += f"👤 {name} | 📞 <code>{phone}</code>\n"
        text += f"💵 {total} ₽ | {dtype}\n\n"

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("find"))
async def cmd_find(message: types.Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    query = command.args
    if not query:
        await message.answer("ℹ️ Укажите номер заказа, имя или телефон.\nПример: <code>/find 125</code> или <code>/find 9999</code>", parse_mode="HTML")
        return

    query_str = query.strip().lower()
    orders = await fetch_all_orders()

    matched = []
    for o in orders:
        oid = str(o.get('id', '')).lower()
        name = str(o.get('name', '')).lower()
        phone = str(o.get('phone', '')).lower()
        track = str(o.get('track_key', '')).lower()

        if query_str in oid or query_str in name or query_str in phone or query_str in track:
            matched.append(o)

    if not matched:
        await message.answer(f"🔍 По запросу <b>«{html.escape(query)}»</b> ничего не найдено.", parse_mode="HTML")
        return

    await message.answer(f"🔎 Найдено заказов: <b>{len(matched)}</b> (показываю первые 3):", parse_mode="HTML")
    for o in matched[:3]:
        text, phone, address, dtype = format_order_card(o)
        keyboard = build_order_keyboard(str(o.get('id', '')), phone, address, dtype)
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)


@dp.message(Command("check"))
async def cmd_check(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    await message.answer("🔍 Проверяю заказы на сайте...")
    await check_new_orders()
    await message.answer("✅ Проверка завершена!")


@dp.message(Command("stats"))
async def cmd_stats(message: types.Message):
    stats_text = f"""
📊 <b>Состояние бота:</b>

🔄 <b>Статус:</b> ✅ Активен (Render Live)
📅 <b>Время сервера:</b> {datetime.now().strftime("%d.%m.%Y %H:%M:%S")}
👥 <b>Администраторов:</b> {len(ADMIN_IDS)}
⏱ <b>Интервал проверки:</b> каждые {CHECK_INTERVAL // 60} мин
🕐 <b>Последняя проверка:</b> {last_check_time.strftime("%H:%M:%S") if last_check_time else "—"}
    """
    await message.answer(stats_text, parse_mode="HTML")


@dp.message(Command("admin"))
async def cmd_admin(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    admin_list = "\n".join([f"• <code>{aid}</code>" for aid in ADMIN_IDS])

    admin_text = f"""
🔐 <b>Панель администратора</b>

Ваш ID: <code>{user_id}</code>
Статус: {'✅ Администратор' if is_admin else '❌ Доступ ограничен'}

📝 Список авторизованных админов:
{admin_list}
    """
    await message.answer(admin_text, parse_mode="HTML")


# ==========================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ==========================================

async def main():
    logger.info("🚀 Запуск бота...")

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Вебхук очищен")
        
        await asyncio.sleep(1)
        
        me = await bot.get_me()
        logger.info(f"✅ Бот @{me.username} успешно авторизован")

        # 1. Запуск внутреннего веб-сервера для пинга
        asyncio.create_task(start_web_server())
        logger.info("🌐 Веб-сервер пинга запущен")

        # 2. Запуск фонового планировщика заказов
        asyncio.create_task(periodic_check())
        logger.info(f"🔄 Фоновый опрос сайта запущен (каждые {CHECK_INTERVAL // 60} мин)")

        # 3. Первичная проверка при старте
        await check_new_orders()

        # 4. Запуск прослушивания Telegram
        logger.info("📡 Бот готов к приёму команд...")
        await dp.start_polling(bot, skip_updates=True)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
