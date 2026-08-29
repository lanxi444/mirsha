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

from aiogram import Bot, Dispatcher, types
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
SEEN_ORDERS_FILE = "seen_orders.json"
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

seen_order_ids = set()
check_lock = asyncio.Lock()
last_check_time = None


# ==========================================
# РАБОТА С ПАМЯТЬЮ ЗАКАЗОВ (АНТИ-ДУБЛИ)
# ==========================================

def load_seen_orders():
    """Загрузка списка уже обработанных ID заказов"""
    global seen_order_ids
    if os.path.exists(SEEN_ORDERS_FILE):
        try:
            with open(SEEN_ORDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                seen_order_ids = set(str(x) for x in data)
                logger.info(f"📂 Загружено {len(seen_order_ids)} сохранённых ID заказов.")
        except Exception as e:
            logger.error(f"Ошибка загрузки seen_orders: {e}")
            seen_order_ids = set()

def save_seen_orders():
    """Сохранение последних 300 ID заказов в файл"""
    try:
        with open(SEEN_ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_order_ids)[-300:], f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения seen_orders: {e}")


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def is_truthy(val) -> bool:
    """Точная проверка флагов из формы (защита от строк 'false', '0', None)"""
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val != 0
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes", "да", "on", "y")
    return False

def clean_phone_number(phone: str) -> str:
    """Очищает номер телефона до формата 79XXXXXXXXX"""
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
                logger.error(f"Ошибка получения заказов: HTTP {response.status}")
    except Exception as e:
        logger.error(f"Ошибка соединения с сайтом: {e}")
    return []


# ==========================================
# КНОПКИ И МЕССЕНДЖЕРЫ
# ==========================================

def get_messenger_button(messenger_val: str, phone: str):
    """Определяет ссылку на мессенджер в зависимости от выбора клиента"""
    m_clean = str(messenger_val or "").strip().lower()
    clean_phone = clean_phone_number(phone)

    # 1. Telegram
    if any(k in m_clean for k in ("tg", "telegram", "телег")):
        if "@" in messenger_val:
            username = messenger_val.replace("@", "").strip()
            return InlineKeyboardButton(text="💬 Написать в Telegram", url=f"https://t.me/{username}")
        if clean_phone:
            return InlineKeyboardButton(text="💬 Написать в Telegram", url=f"https://t.me/+{clean_phone}")

    # 2. MAX / Макс
    if any(k in m_clean for k in ("max", "макс")):
        if clean_phone:
            return InlineKeyboardButton(text="💬 Написать в MAX", url=f"https://max.ru/{clean_phone}")
        return InlineKeyboardButton(text="💬 Открыть MAX", url="https://max.ru")

    # 3. Viber
    if any(k in m_clean for k in ("viber", "вайбер")):
        if clean_phone:
            return InlineKeyboardButton(text="💬 Написать в Viber", url=f"https://viber.click/{clean_phone}")

    # 4. WhatsApp (по умолчанию для WhatsApp или если указан номер)
    if clean_phone:
        return InlineKeyboardButton(text="💬 Написать в WhatsApp", url=f"https://wa.me/{clean_phone}")

    return None

def build_order_keyboard(order_data: dict, address: str = "") -> InlineKeyboardMarkup:
    """Генерация компактной клавиатуры без статусов"""
    buttons = []
    actions_row = []

    # Кнопка мессенджера
    messenger_btn = get_messenger_button(order_data.get('messenger', ''), order_data.get('phone', ''))
    if messenger_btn:
        actions_row.append(messenger_btn)

    # Кнопка Яндекс.Карт для курьера
    if order_data.get('delivery_type') != 'pickup' and address and address != 'Не указан':
        encoded_address = urllib.parse.quote(address)
        actions_row.append(InlineKeyboardButton(text="🗺 На карте", url=f"https://yandex.ru/maps/?text={encoded_address}"))

    if actions_row:
        buttons.append(actions_row)

    # Ссылка в панель администратора сайта
    buttons.append([
        InlineKeyboardButton(text="⚙️ Открыть в админке", url=f"{SITE_URL}/?admin=mirsharov2026")
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ==========================================
# ФОРМИРОВАНИЕ ТЕКСТА КАРТОЧКИ ЗАКАЗА
# ==========================================

def format_order_card(order_data: dict) -> tuple[str, str]:
    """Формирует HTML-текст карточки заказа с правильной проверкой флагов"""
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
    text += f"💬 Мессенджер: <b>{messenger}</b>\n\n"

    text += f"📦 <b>Тип:</b> {'📍 <b>Самовывоз</b>' if delivery_type == 'pickup' else '🚚 <b>Доставка</b>'}\n"

    raw_address = ""
    if delivery_type != 'pickup':
        address_parts = []
        if order_data.get('street'): address_parts.append(str(order_data['street']))
        if order_data.get('house'): address_parts.append(f"д. {order_data['house']}")
        if order_data.get('building'): address_parts.append(f"стр. {order_data['building']}")
        if order_data.get('apartment'): address_parts.append(f"кв. {order_data['apartment']}")
        raw_address = ', '.join(address_parts) if address_parts else 'Не указан'
        
        text += f"📍 <b>Адрес:</b> {html.escape(raw_address)}\n"
        text += f"🚪 Подъезд: {html.escape(str(order_data.get('porch', '-')))} | "
        text += f"🏢 Этаж: {html.escape(str(order_data.get('floor', '-')))} | "
        text += f"📞 Домофон: {html.escape(str(order_data.get('intercom', '-')))}\n"
        
        # ТОЛЬКО ЕСЛИ ФЛАГ ДЕЙСТВИТЕЛЬНО TRUE
        if is_truthy(order_data.get('leave_at_door')):
            text += "🔑 Оставить у двери: ✅ <b>Да</b>\n"
        if is_truthy(order_data.get('warn_delivery')):
            text += "📞 Предупредить о доставке: ✅ <b>Да</b>\n"

    if order_data.get('order_date'):
        text += f"📅 <b>Дата доставки:</b> {html.escape(str(order_data['order_date']))}\n"
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

    return text, raw_address


# ==========================================
# ВЕБ-СЕРВЕР ДЛЯ RENDER (ПИНГ)
# ==========================================

async def health_check(request):
    """Проверка здоровья для Render и cron-job.org"""
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
    logger.info(f"🌐 Веб-сервер пинга запущен на порту {port}")


# ==========================================
# ОТПРАВКА И ПРОВЕРКА ЗАКАЗОВ
# ==========================================

async def send_order_notification(order_data: dict):
    try:
        order_id = str(order_data.get('id', 'Неизвестно'))
        text, address = format_order_card(order_data)
        keyboard = build_order_keyboard(order_data, address)

        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML", reply_markup=keyboard)
                logger.info(f"✅ Уведомление #{order_id} отправлено админу ID: {admin_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки админу ID: {admin_id}: {e}")

    except Exception as e:
        logger.error(f"Ошибка в send_order_notification: {e}")


async def check_new_orders(is_initial_sync: bool = False):
    """Проверка заказов с защитой от повторов и дублей"""
    global last_check_time

    async with check_lock:
        try:
            orders = await fetch_all_orders()
            last_check_time = datetime.now()

            if not orders:
                return

            # При первом запуске запоминаем текущие заказы без рассылки
            if is_initial_sync and not seen_order_ids:
                for o in orders:
                    oid = str(o.get('id', ''))
                    if oid:
                        seen_order_ids.add(oid)
                save_seen_orders()
                logger.info(f"✅ Первичная синхронизация: сохранено {len(seen_order_ids)} существующих заказов.")
                return

            new_orders = []
            for o in reversed(orders):
                oid = str(o.get('id', ''))
                if oid and oid not in seen_order_ids:
                    new_orders.append(o)

            for order in new_orders:
                oid = str(order.get('id', ''))
                await send_order_notification(order)
                seen_order_ids.add(oid)
                save_seen_orders()
                logger.info(f"✅ Заказ #{oid} отправлен в Telegram.")

        except Exception as e:
            logger.error(f"Ошибка при проверке заказов: {e}")


async def periodic_check():
    logger.info(f"🔄 Фоновая проверка заказов активна (каждые {CHECK_INTERVAL // 60} минут)")
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await check_new_orders()
        except Exception as e:
            logger.error(f"Ошибка в periodic_check: {e}")


# ==========================================
# КОМАНДЫ БОТА
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS

    welcome_text = f"""
👋 <b>Панель управления заказами</b>
🆔 <b>Ваш ID:</b> <code>{user_id}</code>

📦 <b>Команды:</b>
/today — Сводка за сегодня
/month — Итоги за текущий месяц
/recent — Список последних 5 заказов
/find <code>номер</code> — Поиск заказа
/check — Принудительная проверка сайта
/stats — Состояние бота
/help — Справка

⏱ Фоновая проверка: <b>каждые 5 минут</b>
💡 Доступ: <b>{'✅ Администратор' if is_admin else '❌ Ограничен'}</b>
    """
    await message.answer(welcome_text, parse_mode="HTML")


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
📚 <b>Справочник команд:</b>

📊 <b>Аналитика:</b>
• /today — заказы, выручка и средний чек за сегодня.
• /month — итоги за текущий месяц.

🔍 <b>Заказы:</b>
• /recent — показать 5 последних заказов.
• /find <code>запрос</code> — поиск (например: <code>/find 105</code> или <code>/find 9999</code>).
• /check — проверить сайт прямо сейчас.

⚙️ <b>Системные:</b>
• /ping — проверка отклика.
• /stats — статус памяти заказов и последняя проверка.
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

🚚 Доставка: <b>{deliveries}</b>
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
        phone = html.escape(str(o.get('phone', '—')))
        total = o.get('total', 0)
        dtype = "📍 Самовывоз" if o.get('delivery_type') == 'pickup' else "🚚 Доставка"
        odate = html.escape(str(o.get('date', '—')))

        text += f"🔹 <b>Заказ #{oid}</b> ({odate})\n"
        text += f"📞 Телефон: <code>{phone}</code>\n"
        text += f"💵 {total} ₽ | {dtype}\n\n"

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("find"))
async def cmd_find(message: types.Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    query = command.args
    if not query:
        await message.answer("ℹ️ Укажите номер заказа или телефон.\nПример: <code>/find 125</code> или <code>/find 9999</code>", parse_mode="HTML")
        return

    query_str = query.strip().lower()
    orders = await fetch_all_orders()

    matched = []
    for o in orders:
        oid = str(o.get('id', '')).lower()
        phone = str(o.get('phone', '')).lower()
        track = str(o.get('track_key', '')).lower()

        if query_str in oid or query_str in phone or query_str in track:
            matched.append(o)

    if not matched:
        await message.answer(f"🔍 По запросу <b>«{html.escape(query)}»</b> ничего не найдено.", parse_mode="HTML")
        return

    await message.answer(f"🔎 Найдено заказов: <b>{len(matched)}</b> (показываю первые 3):", parse_mode="HTML")
    for o in matched[:3]:
        text, address = format_order_card(o)
        keyboard = build_order_keyboard(o, address)
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
👥 <b>Количество админов:</b> {len(ADMIN_IDS)}
📦 <b>Заказов в памяти:</b> {len(seen_order_ids)}
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

📝 Список авторизованных ID админов:
{admin_list}
    """
    await message.answer(admin_text, parse_mode="HTML")


# ==========================================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ==========================================

async def main():
    logger.info("🚀 Запуск бота...")

    try:
        load_seen_orders()

        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("✅ Вебхук очищен")
        
        await asyncio.sleep(1)
        
        me = await bot.get_me()
        logger.info(f"✅ Бот ID: {me.id} успешно авторизован")

        # 1. Запуск внутреннего веб-сервера для пинга
        asyncio.create_task(start_web_server())
        logger.info("🌐 Веб-сервер пинга запущен")

        # 2. Первичная синхронизация существующих заказов
        await check_new_orders(is_initial_sync=True)

        # 3. Запуск фонового планировщика заказов
        asyncio.create_task(periodic_check())
        logger.info(f"🔄 Фоновый опрос сайта запущен (каждые {CHECK_INTERVAL // 60} мин)")

        # 4. Запуск прослушивания Telegram
        logger.info("📡 Бот готов к приёму команд...")
        await dp.start_polling(bot, skip_updates=True)

    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске бота: {e}")


if __name__ == "__main__":
    asyncio.run(main())
