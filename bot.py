#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import logging
import json
import os
import random
import re
import html
import urllib.parse
from datetime import datetime
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    InlineKeyboardMarkup, 
    InlineKeyboardButton, 
    BotCommand, 
    BotCommandScopeDefault
)
from aiogram.fsm.storage.memory import MemoryStorage

# ==========================================
# КОНФИГУРАЦИЯ
# ==========================================
BOT_TOKEN = os.getenv().strip()
ADMIN_IDS = []

SITE_URL = "https://mirsharov-pb.ru"
API_URL = f"{SITE_URL}/api.php?action=get_orders"
SEEN_ORDERS_FILE = "seen_orders.json"
CHECK_INTERVAL = 30  # Проверка новых заказов каждые 30 секунд

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
# 1. РЕГИСТРАЦИЯ МЕНЮ КОМАНД
# ==========================================

async def setup_bot_commands(bot_instance: Bot):
    """Устанавливает кнопку «Меню» в поле ввода Telegram"""
    commands = [
        BotCommand(command="test_order", description="🧪 Тестовый заказ"),
        BotCommand(command="today", description="📊 Выручка и заказы за сегодня"),
        BotCommand(command="month", description="📅 Итоги за текущий месяц"),
        BotCommand(command="recent", description="📋 Последние 5 заказов"),
        BotCommand(command="find", description="🔍 Поиск заказа (/find 125)"),
        BotCommand(command="check", description="⚡ Проверка заказов"),
        BotCommand(command="stats", description="📈 Состояние и память бота"),
        BotCommand(command="start", description="👋 Главное меню"),
        BotCommand(command="help", description="📚 Справочник по командам"),
        BotCommand(command="ping", description="🏓 Проверка отклика")
    ]
    try:
        await bot_instance.set_my_commands(commands, scope=BotCommandScopeDefault())
        logger.info("✅ Кнопка «Меню» со списком команд зарегистрирована в Telegram")
    except Exception as e:
        logger.error(f"Ошибка регистрации команд: {e}")


# ==========================================
# 2. ВЕБ-СЕРВЕР (ДЛЯ CRON-JOB / RENDER ПИНГА)
# ==========================================

async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Запускает веб-сервер в первую очередь, чтобы Render сразу подтвердил порт"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/ping', health_check)
    
    port = int(os.environ.get('PORT', 10000))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host='0.0.0.0', port=port)
    await site.start()
    logger.info(f"🌐 Сервер пинга успешно открыт на порту {port}")


# ==========================================
# 3. РАБОТА С ПАМЯТЬЮ ЗАКАЗОВ
# ==========================================

def load_seen_orders():
    global seen_order_ids
    if os.path.exists(SEEN_ORDERS_FILE):
        try:
            with open(SEEN_ORDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                seen_order_ids = set(str(x) for x in data)
                logger.info(f"📂 Загружено {len(seen_order_ids)} сохранённых заказов.")
        except Exception as e:
            logger.error(f"Ошибка загрузки seen_orders: {e}")
            seen_order_ids = set()

def save_seen_orders():
    try:
        with open(SEEN_ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(seen_order_ids)[-300:], f, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Ошибка сохранения seen_orders: {e}")


# ==========================================
# 4. ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ==========================================

def is_truthy(val) -> bool:
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return val > 0
    if isinstance(val, str):
        val_clean = val.strip().lower()
        if val_clean in ("false", "0", "off", "no", "нет", "none", "null", ""):
            return False
        if val_clean in ("true", "1", "yes", "да", "on"):
            return True
    return False

def clean_phone_number(phone: str) -> str:
    if not phone:
        return ""
    digits = re.sub(r'\D', '', str(phone))
    if len(digits) == 11 and digits.startswith('8'):
        digits = '7' + digits[1:]
    return digits

def parse_order_date(date_str: str):
    if not date_str:
        return None
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(date_str).strip(), fmt)
        except ValueError:
            pass
    return None

async def fetch_all_orders() -> list:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Accept": "application/json"
    }
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(API_URL, ssl=False) as response:
                if response.status == 200:
                    raw_text = await response.text()
                    data = json.loads(raw_text)
                    return data if isinstance(data, list) else []
                logger.error(f"Ошибка получения заказов: HTTP {response.status}")
    except Exception as e:
        logger.error(f"Сайт временно недоступен: {e}")
    return []


# ==========================================
# 5. ФОРМИРОВАНИЕ КАРТОЧКИ ЗАКАЗА И КНОПОК
# ==========================================

def get_messenger_button(messenger_val: str, phone: str):
    m_clean = str(messenger_val or "").strip().lower()
    clean_phone = clean_phone_number(phone)

    if any(k in m_clean for k in ("tg", "telegram", "телег")):
        if "@" in messenger_val:
            username = messenger_val.replace("@", "").strip()
            return InlineKeyboardButton(text="💬 Написать в Telegram", url=f"https://t.me/{username}")
        if clean_phone:
            return InlineKeyboardButton(text="💬 Написать в Telegram", url=f"https://t.me/+{clean_phone}")

    if any(k in m_clean for k in ("max", "макс")):
        if clean_phone:
            return InlineKeyboardButton(text="💬 Написать в MAX", url=f"https://max.ru/{clean_phone}")
        return InlineKeyboardButton(text="💬 Открыть MAX", url="https://max.ru")

    if any(k in m_clean for k in ("viber", "вайбер")):
        if clean_phone:
            return InlineKeyboardButton(text="💬 Написать в Viber", url=f"https://viber.click/{clean_phone}")

    if clean_phone:
        return InlineKeyboardButton(text="💬 Написать в WhatsApp", url=f"https://wa.me/{clean_phone}")

    return None

def build_order_keyboard(order_data: dict, address: str = "") -> InlineKeyboardMarkup:
    buttons = []
    actions_row = []

    messenger_btn = get_messenger_button(order_data.get('messenger', ''), order_data.get('phone', ''))
    if messenger_btn:
        actions_row.append(messenger_btn)

    if order_data.get('delivery_type') != 'pickup' and address and address != 'Не указан':
        encoded_address = urllib.parse.quote(address)
        actions_row.append(InlineKeyboardButton(text="🗺 На карте", url=f"https://yandex.ru/maps/?text={encoded_address}"))

    if actions_row:
        buttons.append(actions_row)

    buttons.append([
        InlineKeyboardButton(text="⚙️ Открыть в админке", url=f"{SITE_URL}/?admin=mirsharov2026")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def format_order_card(order_data: dict) -> tuple[str, str]:
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
            item_product_id = str(item.get('id') or item.get('product_id') or item.get('article') or '').strip()
            item_price = item.get('price', 0)
            item_qty = item.get('quantity', 1)
            item_total = item_price * item_qty

            if item_product_id:
                product_url = f"{SITE_URL}/?product={urllib.parse.quote(item_product_id)}"
                name_display = f'<a href="{product_url}"><b>{item_name}</b></a>'
            else:
                name_display = f'<b>{item_name}</b>'

            text += f"{idx}. {name_display} (Арт: <code>{item_article}</code>) — {item_price}₽ × {item_qty} = <b>{item_total}₽</b>\n"
    else:
        text += "❌ Состав заказа не указан\n"

    return text, raw_address


# ==========================================
# 6. ПРОВЕРКА И ОТПРАВКА ЗАКАЗОВ
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
    global last_check_time
    async with check_lock:
        try:
            orders = await fetch_all_orders()
            last_check_time = datetime.now()

            if not orders:
                return

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
    logger.info(f"🔄 Фоновая проверка заказов активна (каждые {CHECK_INTERVAL} сек)")
    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            await check_new_orders()
        except Exception as e:
            logger.error(f"Ошибка в periodic_check: {e}")


# ==========================================
# 7. КОМАНДЫ БОТА
# ==========================================

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user_id = message.from_user.id
    is_admin = user_id in ADMIN_IDS
    welcome_text = f"""
👋 <b>Панель управления заказами «Мир Шаров»</b>
🆔 <b>Ваш ID:</b> <code>{user_id}</code>

📦 <b>Команды:</b>
/test_order — Сгенерировать тестовый заказ
/today — Сводка за сегодня
/month — Итоги за текущий месяц
/recent — Список последних 5 заказов
/find <code>номер</code> — Поиск заказа
/check — Проверка заказов
/stats — Состояние бота
/help — Справка

💡 Доступ: <b>{'✅ Администратор' if is_admin else '❌ Ограничен'}</b>
    """
    await message.answer(welcome_text, parse_mode="HTML")

@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    help_text = """
📚 <b>Справочник команд:</b>
• /test_order (или /test) — генерация реалистичного тестового заказа.
• /today — выручка и заказы за сегодня.
• /month — итоги за текущий месяц.
• /recent — показать 5 последних заказов.
• /find <code>запрос</code> — поиск заказа по номеру или телефону.
• /check — проверка заказов прямо сейчас.
• /ping — проверка отклика бота.
• /stats — статистика памяти и состояния.
• /admin — список администраторов.
    """
    await message.answer(help_text, parse_mode="HTML")

@dp.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("🏓 <b>Понг!</b> Бот на Render работает штатно.", parse_mode="HTML")

@dp.message(Command("test_order", "test"))
async def cmd_test_order(message: types.Message):
    """Генерация и отправка реалистичного тестового заказа"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    sample_names = ["Алексей Смирнов", "Елена Васильева", "Дмитрий Кузнецов", "Анна Морозова", "Сергей Попов", "Мария Соколова"]
    sample_messengers = ["Telegram", "WhatsApp", "MAX", "Viber"]
    sample_streets = ["ул. Самуила Маршака", "ул. Бориса Пастернака", "ул. Корнея Чуковского", "ул. Анны Ахматовой", "ул. Федосьино"]
    
    sample_items = [
        {"id": "latex_confetti_01", "name": "Шар с конфетти золото", "article": "ЛТ-101", "price": 180, "quantity": random.randint(3, 7)},
        {"id": "foil_figure_bear", "name": "Фигура Мишка с сердечком", "article": "ФГ-045", "price": 950, "quantity": 1},
        {"id": "big_bubble_feathers", "name": "Баблс с перьями и надписью", "article": "ББ-012", "price": 1600, "quantity": 1},
        {"id": "foil_digit_silver", "name": "Цифра 5 серебро (102 см)", "article": "ЦФ-005", "price": 850, "quantity": random.randint(1, 2)},
        {"id": "latex_pastel_pink", "name": "Латекс пастель Розовый", "article": "ЛТ-022", "price": 130, "quantity": random.randint(5, 15)},
        {"id": "comp_kids_hero", "name": "Сет «Супергерои»", "article": "КС-303", "price": 2400, "quantity": 1}
    ]

    selected_cart = random.sample(sample_items, k=random.randint(2, 3))
    total_sum = sum(item["price"] * item["quantity"] for item in selected_cart)

    is_delivery = random.choice([True, True, False])
    order_time_now = datetime.now().strftime("%d.%m.%Y %H:%M")
    test_id = f"TEST-{random.randint(1000, 9999)}"
    test_track = ''.join(random.choices('ABCDEFGHJKLMNPQRSTUVWXYZ23456789', k=8))

    sample_comments = [
        "Позвоните за 15 минут до приезда, пожалуйста!",
        "Домофон временно не работает, наберите по номеру.",
        "Сделайте красивую композицию на день рождения 🎉",
        "Не звонить в дверь, спит ребёнок!",
        ""
    ]

    mock_order = {
        "id": test_id,
        "date": order_time_now,
        "name": random.choice(sample_names),
        "phone": f"+7 (9{random.randint(10, 99)}) {random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}",
        "messenger": random.choice(sample_messengers),
        "delivery_type": "delivery" if is_delivery else "pickup",
        "street": random.choice(sample_streets) if is_delivery else "",
        "house": str(random.randint(1, 35)) if is_delivery else "",
        "building": str(random.randint(1, 3)) if (is_delivery and random.choice([True, False])) else "",
        "apartment": str(random.randint(1, 180)) if is_delivery else "",
        "porch": str(random.randint(1, 6)) if is_delivery else "",
        "floor": str(random.randint(1, 17)) if is_delivery else "",
        "intercom": str(random.randint(1, 180)) if is_delivery else "",
        "leave_at_door": random.choice([True, False]) if is_delivery else False,
        "warn_delivery": True if is_delivery else False,
        "order_date": datetime.now().strftime("%d.%m.%Y"),
        "order_time": random.choice(["10:00 - 12:00", "13:00 - 15:00", "17:00 - 19:00", "20:00 - 21:00"]),
        "comment": random.choice(sample_comments),
        "total": total_sum,
        "track_key": test_track,
        "cart": selected_cart
    }

    text, address = format_order_card(mock_order)
    keyboard = build_order_keyboard(mock_order, address)

    await message.answer("🧪 <b>Сгенерирован тестовый заказ:</b>", parse_mode="HTML")
    await message.answer(text, parse_mode="HTML", reply_markup=keyboard)

@dp.message(Command("today"))
async def cmd_today(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    orders = await fetch_all_orders()
    today_date = datetime.now().date()
    today_orders = [o for o in orders if parse_order_date(o.get('date')) and parse_order_date(o.get('date')).date() == today_date]

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
    month_orders = [o for o in orders if parse_order_date(o.get('date')) and parse_order_date(o.get('date')).month == now.month and parse_order_date(o.get('date')).year == now.year]

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
        text += f"🔹 <b>Заказ #{oid}</b> ({odate})\n📞 Телефон: <code>{phone}</code>\n💵 {total} ₽ | {dtype}\n\n"

    await message.answer(text, parse_mode="HTML")

@dp.message(Command("find"))
async def cmd_find(message: types.Message, command: CommandObject):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("❌ Доступно только администраторам.")
        return

    query = command.args
    if not query:
        await message.answer("ℹ️ Укажите номер заказа или телефон. Пример: <code>/find 125</code>", parse_mode="HTML")
        return

    query_str = query.strip().lower()
    orders = await fetch_all_orders()
    matched = [o for o in orders if query_str in str(o.get('id', '')).lower() or query_str in str(o.get('phone', '')).lower() or query_str in str(o.get('track_key', '')).lower()]

    if not matched:
        await message.answer(f"🔍 По запросу <b>«{html.escape(query)}»</b> ничего не найдено.", parse_mode="HTML")
        return

    await message.answer(f"🔎 Найдено заказов: <b>{len(matched)}</b> (первые 3):", parse_mode="HTML")
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
🔄 <b>Статус:</b> ✅ Активен
📦 <b>Заказов в памяти:</b> {len(seen_order_ids)}
⏱ <b>Интервал проверки:</b> {CHECK_INTERVAL} сек
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
# 8. ГЛАВНЫЙ ЦИКЛ ЗАПУСКА
# ==========================================

async def main():
    logger.info("🚀 Старт инициализации приложения...")

    # 1. Открытие порта для Render
    await start_web_server()

    # 2. Загрузка памяти заказов
    load_seen_orders()

    # 3. Первичная синхронизация существующих заказов
    await check_new_orders(is_initial_sync=True)

    # 4. Фоновый опрос сайта
    asyncio.create_task(periodic_check())

    # 5. Цикл подключения к Telegram с авто-восстановлением
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            me = await bot.get_me()
            logger.info(f"✅ Бот @{me.username} (ID: {me.id}) успешно подключен к Telegram")
            
            await setup_bot_commands(bot)
            
            logger.info("📡 Приём сообщений запущен...")
            await dp.start_polling(bot)

        except Exception as e:
            logger.error(f"⚠️ Ошибка соединения с Telegram: {e}. Переподключение через 5 секунд...")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
