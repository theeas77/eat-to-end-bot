import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import datetime
import time
import json
import os
import uuid
import threading
import requests
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Yekaterinburg")  # UTC+5 Пермь

# --- ПОСТОЯННОЕ ХРАНЕНИЕ ДАННЫХ ---
# Все пользовательские данные лежат в одном стабильном каталоге рядом с ботом.
# Если хостинг предоставляет постоянный диск/volume, можно задать путь через
# Railway Volume смонтирован в /data. При необходимости путь можно переопределить через BOT_DATA_DIR.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("BOT_DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)

COUNTER_FILE = os.path.join(DATA_DIR, "order_counter.json")
CUSTOMERS_FILE = os.path.join(DATA_DIR, "customers.json")
ACTIVE_ORDERS_FILE = os.path.join(DATA_DIR, "active_orders.json")

# ЮКасса для Ленина и Промышленная
YUKASSA_SHOP_ID = "1378878"
YUKASSA_SECRET_KEY = "live_WocTCMSmoycyvMP8ttX9_M4w2dsBMBWugjizIPvU2do"

# ЮКасса для Советской
YUKASSA_SHOP_ID_SOVETSKAYA = "1254695"
YUKASSA_SECRET_KEY_SOVETSKAYA = "live_U_Z86aPDfocmL1uteRrfHhyXVigb4sqinsDwRD8v5Jo"

VK_TOKEN = "vk1.a.lbcUXPokTxgPCYnlF_UcqQGaHW4nbI2dkqpNUfqL2tGCrjhST6s-4yoeGf6z0xrx1B1TXjcaWMu1EAWDDrqfH9us2nT7381dpYQUaiiXbaZAwqZbpEVGQ9oxyw3Bqsu_mbdyWdFVKlhcbNZE3lybJXXGoadma1fWTdzjtADUvTTZR2bbIySqQn8_qlyj5bYTzaC1DzmOHoWGJkRH_szQsA"
ADMIN_VK_ID = 1118370233

# --- ДОСТАВКА ---
DELIVERY_TEST_MODE = False         # False — доставка доступна всем
DELIVERY_TIME_LIMITS_ENABLED = True  # Рабочий режим: доставка 12:00–01:00, заказ к времени — не раньше чем через 90 мин
DELIVERY_TEST_USER = 72534661      # VK ID для теста доставки (ivshiin)
DELIVERY_POINT = "Ленина 36/2"     # с какой точки готовят доставку
DELIVERY_MIN_ORDER = 500           # минимальная сумма заказа на доставку (только товары), ₽
DELIVERY_OPEN_H = 12               # доставка работает с 12:00
DELIVERY_CLOSE_H = 25              # до 01:00 следующего дня (24 + 1)
DELIVERY_ZONES = {
    "Чайковский": 200,
    "Новый": 350,
    "Ольховка": 350,
    "Прикамский": 350,
}

MANAGERS = {
    "Ленина 36/2": 1118370233,
    "Декабристов 4а": 1118370233,
    "Советская 2/10": 1118370233,
}

# Время закрытия > 24 означает переход через полночь (29 = 05:00 следующего дня)
HOURS = {
    "Ленина 36/2": (9, 29),
    "Декабристов 4а": (9, 23),
    "Советская 2/10": (9, 23),
}

# Категории с соусом
DELIVERY_HIDDEN_CATS = {"Кофе и чай"}  # на доставке нет кофе/чая; морсы и газировка есть
SAUCE_CATS = {"Шаурма и сэндвичи"}
# Категории с добавками
EXTRAS_CATS = {"Шаурма и сэндвичи", "Шашлык"}

MENU = {
    "Шаурма и сэндвичи": {
        "С курицей мини": 240,
        "С курицей стандарт": 300,
        "С курицей большая": 385,
        "Со свининой мини": 250,
        "Со свининой стандарт": 325,
        "Со свининой большая": 390,
        "Овощная стандарт": 245,
        "Сэндвич с курицей": 250,
        "Сэндвич с беконом": 250,
    },
    "Шашлык": {
        "Шашлык из курицы": 405,
        "Шашлык из свинины": 415,
    },
    "Кофе и чай": {
        "Эспрессо": 90,
        "Двойной эспрессо": 130,
        "Американо": 110,
        "Латте": 130,
        "Капучино": 130,
        "Чай Черный": 90,
        "Чай Зеленый": 90,
        "Чай с малиной": 130,
        "Чай Пряный": 130,
    },
    "Напитки": {
        "Морс Фруктовый": 100,
        "Морс Облепиховый": 100,
        "Морс Малина-мята": 100,
        "Морс Клубника": 100,
        "Кола 0.5л": 110,
        "Лимон-лайм 0.5л": 110,
        "Апельсин 0.5л": 110,
        "Кола 0.3л": 90,
        "Лимон-лайм 0.3л": 90,
    },
}

SAUCES = ["Фирменный", "BBQ", "Острый", "Сырный", "Медово-горчичный", "Без соуса"]

EXTRAS = {
    "Сыр тертый": 52,
    "Огурцы соленые": 42,
    "Морковка корейская": 42,
    "Красный лук": 42,
    "Лук фри": 42,
    "Халапеньо": 52,
    "Бекон": 42,
    "Ананасы": 42,
    "Оливки": 42,
    "Перец болгарский": 42,
    "Курица доп.": 89,
    "Свинина доп.": 89,
}

user_states = {}
processed_msgs = {}
pending_payments = {}  # payment_id -> данные заказа, ждущего оплаты


def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения {path}: {e}")
    return default


def _save_json(path, data):
    """Атомарно сохраняет JSON, чтобы файл не повредился при перезапуске."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception as e:
        print(f"Ошибка записи {path}: {e}")


customers = _load_json(CUSTOMERS_FILE, {})
active_orders = _load_json(ACTIVE_ORDERS_FILE, {})


def get_customer(user_id):
    return customers.setdefault(str(user_id), {"phone": None, "delivery": None, "last_order": None})


def save_customer(user_id, order):
    c = get_customer(user_id)
    if order.get("phone"):
        c["phone"] = order["phone"]
    if order.get("delivery"):
        d = order["delivery"]
        c["delivery"] = {k: d.get(k) for k in ("zone", "price", "street", "house", "apt", "domofon")}
    # Сохраняем только то, что нужно для повторного заказа
    c["last_order"] = {
        "items": [{**i, "qty": i.get("qty", 1)} for i in order.get("items", [])],
        "point": order.get("point"),
        "order_type": order.get("order_type", "pickup"),
        "delivery": c.get("delivery") if order.get("order_type") == "delivery" else None,
        "phone": order.get("phone"),
    }
    _save_json(CUSTOMERS_FILE, customers)


def save_delivery_address(user_id, delivery):
    """Сохраняет адрес сразу после его ввода, даже если заказ ещё не оформлен."""
    if not delivery:
        return
    if not delivery.get("street") or not delivery.get("house") or not delivery.get("zone"):
        return
    c = get_customer(user_id)
    c["delivery"] = {k: delivery.get(k) for k in ("zone", "price", "street", "house", "apt", "domofon")}
    _save_json(CUSTOMERS_FILE, customers)


def save_active_order(order_num, user_id, order, manager_id):
    active_orders[str(order_num)] = {
        "user_id": user_id,
        "order_type": order.get("order_type", "pickup"),
        "manager_id": manager_id,
        "status": "Принят",
    }
    _save_json(ACTIVE_ORDERS_FILE, active_orders)

# --- НАПОМИНАНИЕ О НЕЗАВЕРШЁННОМ ЗАКАЗЕ ---
ABANDONED_FIRST_TIMEOUT = 5 * 60     # первое напоминание через 5 минут бездействия
ABANDONED_SECOND_TIMEOUT = 30 * 60   # второе напоминание через 30 минут бездействия
ABANDONED_CHECK_INTERVAL = 30        # проверяем раз в 30 секунд


def load_counter():
    """Загружает счётчик из файла"""
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE, "r") as f:
                data = json.load(f)
            saved_date = data.get("date")
            today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
            if saved_date == today:
                return data.get("counter", 0)
    except:
        pass
    return 0


def save_counter(counter):
    """Сохраняет счётчик в файл"""
    try:
        today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
        with open(COUNTER_FILE, "w") as f:
            json.dump({"date": today, "counter": counter}, f)
    except:
        pass


def create_payment(amount, order_num, description, phone=None, items=None, shop_id=None, secret_key=None):
    """Создаёт платёж в ЮКассе и возвращает ссылку"""
    shop_id = shop_id or YUKASSA_SHOP_ID
    secret_key = secret_key or YUKASSA_SECRET_KEY
    print(f"Создаю платёж: shop_id={shop_id}, amount={amount}, order={order_num}")
    try:
        idempotence_key = str(uuid.uuid4())

        # Формируем номенклатуру для чека
        receipt_items = []
        if items:
            for item in items:
                item_amount = item["price"]
                for e in item.get("extras", []):
                    item_amount += EXTRAS.get(e, 42)
                receipt_items.append({
                    "description": item["name"][:128],
                    "quantity": f"{item.get('qty', 1)}.00",
                    "amount": {"value": f"{item_amount}.00", "currency": "RUB"},
                    "vat_code": 1,  # без НДС
                    "payment_mode": "full_payment",
                    "payment_subject": "commodity"
                })
        else:
            receipt_items.append({
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "commodity"
            })

        payload = {
            "amount": {"value": f"{amount}.00", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://vk.com"},
            "capture": True,
            "description": description,
            "metadata": {"order_num": str(order_num)},
            "receipt": {
                "items": receipt_items
            }
        }

        # Добавляем телефон покупателя для чека
        if phone:
            payload["receipt"]["customer"] = {"phone": phone}

        response = requests.post(
            "https://api.yookassa.ru/v3/payments",
            auth=(shop_id, secret_key),
            headers={"Idempotence-Key": idempotence_key, "Content-Type": "application/json"},
            json=payload,
            timeout=10
        )
        print(f"Ответ ЮКассы: {response.status_code} — {response.text[:300]}")
        data = response.json()
        if "confirmation" in data:
            return data["confirmation"]["confirmation_url"], data["id"]
        return None, None
    except Exception as e:
        print(f"Ошибка создания платежа: {e}")
        return None, None


def check_payment(payment_id, shop_id=None, secret_key=None):
    """Проверяет статус платежа"""
    shop_id = shop_id or YUKASSA_SHOP_ID
    secret_key = secret_key or YUKASSA_SECRET_KEY
    try:
        response = requests.get(
            f"https://api.yookassa.ru/v3/payments/{payment_id}",
            auth=(shop_id, secret_key),
            timeout=10
        )
        data = response.json()
        return data.get("status")
    except:
        return None


def get_order_counter():
    """Возвращает актуальный счётчик, сбрасывает если новый день"""
    try:
        if os.path.exists(COUNTER_FILE):
            with open(COUNTER_FILE, "r") as f:
                data = json.load(f)
            saved_date = data.get("date")
            today = datetime.datetime.now(TZ).strftime("%Y-%m-%d")
            if saved_date == today:
                return data.get("counter", 0)
    except:
        pass
    # Новый день — сбрасываем
    save_counter(0)
    return 0


def get_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = {
            "step": "main",
            "order": {
                "items": [],  # list of dicts: {name, price, sauce, extras}
                "point": None,
                "pickup_time": None,
                "order_type": "pickup",   # pickup | delivery
                "delivery": None,          # {zone, price, street, house, apt}
            },
            "current_item": None,  # item being configured right now
            "last_activity": time.time(),
            "abandon_reminder_stage": 0,
        }
    return user_states[user_id]


def reset_state(user_id):
    user_states[user_id] = {
        "step": "main",
        "order": {
            "items": [],
            "point": None,
            "pickup_time": None,
            "order_type": "pickup",
            "delivery": None,
        },
        "current_item": None,
        "last_activity": time.time(),
        "abandon_reminder_stage": 0,
    }


def is_point_open(point):
    now = datetime.datetime.now(TZ)
    open_h, close_h = HOURS.get(point, (9, 22))
    h = now.hour + now.minute / 60
    if close_h <= 24:
        return open_h <= h < close_h
    # Переход через полночь: открыто с open_h до 24:00 ИЛИ с 00:00 до (close_h - 24)
    return h >= open_h or h < (close_h - 24)


def is_delivery_open():
    """Проверяет часы доставки. В тестовом режиме ограничения можно отключить."""
    if not DELIVERY_TIME_LIMITS_ENABLED:
        return True
    now = datetime.datetime.now(TZ)
    h = now.hour + now.minute / 60
    open_h, close_h = DELIVERY_OPEN_H, DELIVERY_CLOSE_H
    if close_h <= 24:
        return open_h <= h < close_h
    return h >= open_h or h < (close_h - 24)


def get_time_slots(point, min_minutes=15):
    slots = []
    open_h, close_h = HOURS.get(point, (9, 22))

    now = datetime.datetime.now(TZ)

    # Первый слот = текущее время + min_minutes
    start_time = (now + datetime.timedelta(minutes=min_minutes)).replace(second=0, microsecond=0)

    # Момент закрытия. Если close_h > 24 — закрытие на следующий день
    if close_h <= 24:
        end_dt = datetime.datetime.combine(now.date(), datetime.time(close_h % 24, 0), tzinfo=TZ)
        if close_h == 24:
            end_dt = datetime.datetime.combine(now.date(), datetime.time(23, 59), tzinfo=TZ)
    else:
        # Закрытие после полуночи. Если сейчас уже после полуночи (до закрытия) — закрытие сегодня, иначе завтра
        real_close = close_h - 24
        if now.hour < real_close:
            end_dt = datetime.datetime.combine(now.date(), datetime.time(real_close, 0), tzinfo=TZ)
        else:
            end_dt = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(real_close, 0), tzinfo=TZ)

    current = start_time
    while current <= end_dt:
        slots.append(current.strftime("%H:%M"))
        current += datetime.timedelta(minutes=10)

    return slots[:9]


def format_cart(order):
    if not order["items"]:
        return "Корзина пуста"
    lines = []
    total = 0
    for idx, item in enumerate(order["items"], 1):
        name = item["name"]
        base_price = item["price"]
        sauce = item.get("sauce")
        extras = item.get("extras", [])
        qty = item.get("qty", 1)
        unit_price = base_price + sum(EXTRAS.get(e, 42) for e in extras)
        line = f"{idx}. {name} ×{qty} — {unit_price * qty}₽"
        if sauce and sauce != "Без соуса":
            line += f" (соус: {sauce})"
        if extras:
            line += f"\n   + {', '.join(extras)}"
        lines.append(line)
        total += unit_price * qty
    goods_total = total
    result = "\n".join(lines)
    if order.get("order_type") == "delivery" and order.get("delivery"):
        dprice = order["delivery"].get("price", 0)
        result += f"\n\nТовары: {goods_total}₽"
        result += f"\nДоставка: {dprice}₽"
        result += f"\n\nИтого: {goods_total + dprice}₽"
    else:
        result += f"\n\nИтого: {goods_total}₽"
    return result


def get_goods_total(order):
    """Сумма только за товары, без доставки"""
    total = 0
    for item in order["items"]:
        unit = item["price"] + sum(EXTRAS.get(e, 42) for e in item.get("extras", []))
        total += unit * item.get("qty", 1)
    return total


def get_total(order):
    """Полная сумма с доставкой"""
    total = get_goods_total(order)
    if order.get("order_type") == "delivery" and order.get("delivery"):
        total += order["delivery"].get("price", 0)
    return total


FEEDBACK_URL = "https://vk.com/app6013442_-232479429?form_id=1#form_id=1"

def kb_main():
    kb = VkKeyboard(one_time=False)
    kb.add_button("🏃 Самовывоз", color=VkKeyboardColor.POSITIVE)
    kb.add_button("🚗 Доставка", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("🔁 Повторить заказ", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🛒 Корзина", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("📍 Наши точки", color=VkKeyboardColor.SECONDARY)
    kb.add_button("ℹ️ О нас", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("💬 Обратная связь", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_final():
    """Клавиатура после оформления заказа"""
    kb = VkKeyboard(one_time=False)
    kb.add_button("🏠 Вернуться в начало", color=VkKeyboardColor.PRIMARY)
    return kb.get_keyboard()


def kb_order_type(user_id):
    """Выбор: самовывоз или доставка (доставка в тесте только для тест-юзера)"""
    kb = VkKeyboard(one_time=True)
    kb.add_button("🏃 Самовывоз", color=VkKeyboardColor.POSITIVE)
    if not DELIVERY_TEST_MODE or user_id == DELIVERY_TEST_USER:
        kb.add_line()
        kb.add_button("🚗 Доставка", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("◀️ Назад", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_delivery_zones():
    kb = VkKeyboard(one_time=True)
    for zone, price in DELIVERY_ZONES.items():
        kb.add_button(f"{zone} — {price}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_apt_skip():
    kb = VkKeyboard(one_time=True)
    kb.add_button("Без квартиры", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_domofon():
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Есть домофон", color=VkKeyboardColor.SECONDARY)
    kb.add_button("❌ Нет домофона", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_change():
    kb = VkKeyboard(one_time=True)
    kb.add_button("1000₽", color=VkKeyboardColor.SECONDARY)
    kb.add_button("1500₽", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("2000₽", color=VkKeyboardColor.SECONDARY)
    kb.add_button("Без сдачи", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_delivery_time():
    kb = VkKeyboard(one_time=True)
    kb.add_button("⚡ Побыстрее (~45 мин)", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🕒 К определённому времени", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


COMING_SOON_POINTS = set()  # все точки открыты
CLOSED_POINTS = {"Советская 2/10"}  # временно закрыты

def kb_points():
    kb = VkKeyboard(one_time=True)
    for point in MANAGERS.keys():
        if point in COMING_SOON_POINTS:
            kb.add_button(f"🔜 {point} — скоро открытие", color=VkKeyboardColor.SECONDARY)
        elif point in CLOSED_POINTS:
            kb.add_button(f"⛔ {point} — временно закрыта", color=VkKeyboardColor.SECONDARY)
        else:
            status = "✅" if is_point_open(point) else "❌"
            kb.add_button(f"{status} {point}", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_points_without_dekabristov():
    kb = VkKeyboard(one_time=True)
    for point in MANAGERS.keys():
        if point in COMING_SOON_POINTS or point in CLOSED_POINTS:
            continue
        status = "✅" if is_point_open(point) else "❌"
        kb.add_button(f"{status} {point}", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_categories(order_type="pickup"):
    kb = VkKeyboard(one_time=True)
    for cat in MENU.keys():
        if order_type == "delivery" and cat in DELIVERY_HIDDEN_CATS:
            continue
        kb.add_button(cat, color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✏️ Корзина", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_items(category):
    kb = VkKeyboard(one_time=True)
    items = list(MENU[category].items())

    # В длинных категориях показываем по одной позиции на строку,
    # чтобы название и цена полностью помещались на кнопке.
    one_per_row = category in {"Шаурма и сэндвичи", "Шашлык"}

    for i, (name, price) in enumerate(items):
        kb.add_button(f"{name} {price}₽", color=VkKeyboardColor.SECONDARY)
        if one_per_row:
            if i != len(items) - 1:
                kb.add_line()
        elif i % 2 == 1 and i != len(items) - 1:
            kb.add_line()

    kb.add_line()
    kb.add_button("◀️ К категориям", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_sauces():
    kb = VkKeyboard(one_time=True)
    sauces = ["Фирменный", "BBQ", "Острый", "Сырный", "Медово-горчичный", "Без соуса"]
    for i, s in enumerate(sauces):
        kb.add_button(s, color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1 and i != len(sauces) - 1:
            kb.add_line()
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_extras_page1():
    kb = VkKeyboard(one_time=True)
    extras = list(EXTRAS.items())[:7]
    for i, (extra, price) in enumerate(extras):
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1:
            kb.add_line()
    kb.add_line()
    kb.add_button("🥫 Доп соус +42₽", color=VkKeyboardColor.SECONDARY)
    kb.add_button("➡️ Далее", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_extra_sauces():
    kb = VkKeyboard(one_time=True)
    for sauce in SAUCES[:-1]:  # все кроме "Без соуса"
        kb.add_button(f"{sauce} +42₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("◀️ Назад к добавкам", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_extras_page2():
    kb = VkKeyboard(one_time=True)
    extras = list(EXTRAS.items())[8:]
    for i, (extra, price) in enumerate(extras):
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1 and i != len(extras) - 1:
            kb.add_line()
    kb.add_line()
    kb.add_button("➡️ Далее", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_after_item():
    kb = VkKeyboard(one_time=True)
    kb.add_button("➕ Добавить ещё", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✏️ Корзина", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_cart(order):
    kb = VkKeyboard(one_time=True)
    for idx, item in enumerate(order.get("items", []), 1):
        kb.add_button(f"➖ {idx}", color=VkKeyboardColor.SECONDARY)
        kb.add_button(f"➕ {idx}", color=VkKeyboardColor.SECONDARY)
        kb.add_button(f"🗑 {idx}", color=VkKeyboardColor.NEGATIVE)
        kb.add_line()
    kb.add_button("➕ Добавить ещё", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_repeat_order():
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Повторить этот заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✏️ Изменить заказ", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_saved_address():
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Да, сюда", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✏️ Другой адрес", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_saved_phone():
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Использовать этот номер", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✏️ Другой номер", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_upsell_extra():
    kb = VkKeyboard(one_time=True)
    kb.add_button(f"🧀 Сыр +{EXTRAS['Сыр тертый']}₽", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button(f"🥓 Бекон +{EXTRAS['Бекон']}₽", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("➡️ Без добавки", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_upsell_drink():
    kb = VkKeyboard(one_time=True)
    for name in ["Морс Фруктовый", "Морс Облепиховый", "Морс Малина-мята"]:
        price = MENU["Напитки"][name]
        kb.add_button(f"🥤 {name} +{price}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("➡️ Без напитка", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_manager_status(order_num, is_delivery):
    # INLINE-клавиатура прикрепляется к конкретному сообщению с заказом.
    # Поэтому кнопки старых заказов не исчезают, когда приходит новый заказ.
    kb = VkKeyboard(one_time=False, inline=True)
    kb.add_button(f"🔥 Готовим #{order_num}", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    if is_delivery:
        kb.add_button(f"🚗 Курьер выехал #{order_num}", color=VkKeyboardColor.PRIMARY)
        kb.add_line()
        kb.add_button(f"✅ Доставлен #{order_num}", color=VkKeyboardColor.POSITIVE)
    else:
        kb.add_button(f"✅ Готов #{order_num}", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button(f"❌ Отменить #{order_num}", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def request_phone(vk, user_id, state):
    customer = get_customer(user_id)
    saved = customer.get("phone")

    # Если отдельное поле телефона ещё пустое, пробуем восстановить его
    # из последнего оформленного заказа (полезно после обновления старой версии бота).
    if not saved and customer.get("last_order"):
        saved = customer["last_order"].get("phone")
        if saved:
            customer["phone"] = saved
            _save_json(CUSTOMERS_FILE, customers)

    if saved:
        state["step"] = "confirm_saved_phone"
        send(vk, user_id, f"📱 Использовать сохранённый номер {saved}?", kb_saved_phone())
    else:
        state["step"] = "enter_phone"
        send(vk, user_id, "📱 Укажи номер телефона для связи\n\nНапиши в формате: 89991234567")


def kb_time(slots):
    kb = VkKeyboard(one_time=True)
    slots = slots[:9]
    for i, slot in enumerate(slots):
        kb.add_button(slot, color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1 and i != len(slots) - 1:
            kb.add_line()
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_confirm():
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Подтвердить", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🔄 Начать заново", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_wait_payment(order):
    """Кнопки на экране ожидания онлайн-оплаты.
    Для доставки — запасные варианты: картой курьеру / наличными."""
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Я оплатил", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    if order.get("order_type") == "delivery":
        kb.add_button("💳 Картой курьеру", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
        kb.add_button("💵 Наличными", color=VkKeyboardColor.SECONDARY)
    else:
        kb.add_button("💵 Оплачу при получении", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def send(vk, user_id, text, keyboard=None):
    params = {"user_id": user_id, "message": text, "random_id": 0}
    if keyboard:
        params["keyboard"] = keyboard
    # Три попытки — сеть до ВК иногда обрывается
    for attempt in range(3):
        try:
            vk.messages.send(**params)
            return True
        except Exception as e:
            print(f"Не отправилось (попытка {attempt + 1}): {e}")
            time.sleep(2)
    return False


def start_checkout(vk, user_id, state):
    """Общий переход к оформлению: проверки и выбор времени.
    Возвращает True если перешли дальше."""
    order = state["order"]
    if not order["items"]:
        send(vk, user_id, "Корзина пуста! Добавь хотя бы одну позицию 😊", kb_categories(state["order"].get("order_type","pickup")))
        return

    # Если клиент нажал «Повторить заказ» -> «Изменить заказ»,
    # перед дальнейшим оформлением доставки обязательно заново уточняем адрес.
    if order.get("order_type") == "delivery" and state.get("repeat_needs_address_confirm"):
        saved_d = order.get("delivery") or get_customer(user_id).get("delivery")
        if saved_d:
            state["order"]["delivery"] = dict(saved_d)
            state["step"] = "repeat_confirm_address"
            addr = f"{saved_d.get('street')}, д. {saved_d.get('house')}"
            if saved_d.get("apt"):
                addr += f", кв. {saved_d.get('apt')}"
            send(vk, user_id,
                 f"🚗 Куда доставить заказ?\n\n"
                 f"🏠 Прошлый адрес: {addr}\n"
                 f"Зона: {saved_d.get('zone')}\n\n"
                 f"Подтверди адрес или выбери другой 👇",
                 kb_saved_address())
        else:
            state["repeat_needs_address_confirm"] = False
            state["step"] = "delivery_zone"
            send(vk, user_id, "🚗 Уточним адрес доставки. Выбери зону 👇", kb_delivery_zones())
        return

    # Ненавязчивый upsell: сначала одна популярная добавка, затем напиток.
    if not state.get("upsell_extras_shown"):
        state["upsell_extras_shown"] = True
        target_idx = next((idx for idx, i in enumerate(order["items"]) if i.get("cat") in EXTRAS_CATS and not i.get("extras")), None)
        if target_idx is not None:
            state["upsell_target_idx"] = target_idx
            state["step"] = "upsell_extra"
            send(vk, user_id, "🔥 Сделать ещё вкуснее? Добавь популярную добавку одним нажатием.", kb_upsell_extra())
            return

    if not state.get("upsell_drink_shown") and not any(i.get("cat") == "Напитки" for i in order["items"]):
        state["upsell_drink_shown"] = True
        state["step"] = "upsell_drink"
        send(vk, user_id, "🥤 Добавить морс к заказу? Один клик — и он в корзине.", kb_upsell_drink())
        return

    # Доставка — проверка минимальной суммы (только товары, без доставки)
    if order.get("order_type") == "delivery":
        goods = get_goods_total(order)
        if goods < DELIVERY_MIN_ORDER:
            need = DELIVERY_MIN_ORDER - goods
            send(vk, user_id,
                f"🛒 Минимальная сумма заказа на доставку — {DELIVERY_MIN_ORDER}₽.\n"
                f"Сейчас на {goods}₽, добавь ещё на {need}₽ 😊",
                kb_categories(state["order"].get("order_type","pickup")))
            return
        # Выбор режима времени доставки
        state["step"] = "delivery_time_mode"
        send(vk, user_id,
            "🕒 Когда доставить?\n\n"
            "⚡ Побыстрее — в течение ~45 минут (зависит от загруженности)\n" +
            ("🕒 К определённому времени — не раньше чем через 90 минут" if DELIVERY_TIME_LIMITS_ENABLED else "🕒 К определённому времени — любое время для теста"),
            kb_delivery_time())
        return

    # Самовывоз — как раньше
    has_shashlik = any("Шашлык" in i["name"] for i in order["items"])
    min_min = 30 if has_shashlik else 15
    slots = get_time_slots(order["point"], min_minutes=min_min)
    if not slots:
        send(vk, user_id, "😔 Точка скоро закрывается, не успеем приготовить.\nПриходи завтра!", kb_main())
        reset_state(user_id)
        return
    state["step"] = "choose_time"
    order["min_minutes"] = min_min
    hint = "⏰ На какое время готовить?\n\nВыбери из списка или напиши своё время в формате ЧЧ:ММ (например 14:30)"
    if has_shashlik:
        hint += "\n\n🔥 Шашлык готовится 30 минут — учли это в слотах"
    send(vk, user_id, hint, kb_time(slots))


def _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, payment_status):
    """Финализирует заказ — уведомляет менеджера и клиента"""
    is_delivery = order.get("order_type") == "delivery"
    manager_id = MANAGERS.get(order["point"], ADMIN_VK_ID)

    if is_delivery:
        d = order["delivery"]
        addr = f"{d['street']}, д. {d['house']}"
        if d.get("apt"):
            addr += f", кв. {d['apt']}"
        notif = (
            f"🚗 НОВЫЙ ЗАКАЗ #{order_num} — ДОСТАВКА\n\n"
            f"👤 {user_name} (vk.com/id{user_id})\n"
            f"📱 {order.get('phone', 'не указан')}\n"
            f"🚗 Зона: {d['zone']}\n"
            f"🏠 Адрес: {addr}\n"
            + (f"🔔 Домофон: {d['domofon']}\n" if d.get("domofon") else "")
            + f"🍳 Готовит: {order['point']}\n"
            f"🕒 Время: {order['pickup_time']}\n\n"
            f"{cart}\n\n"
            f"💰 Итого с доставкой: {total}₽\n"
            f"💳 {payment_status}"
        )
    else:
        notif = (
            f"🆕 НОВЫЙ ЗАКАЗ #{order_num}\n\n"
            f"👤 {user_name} (vk.com/id{user_id})\n"
            f"📱 {order.get('phone', 'не указан')}\n"
            f"📍 {order['point']}\n"
            f"⏰ Готовность: {order['pickup_time']}\n\n"
            f"{cart}\n\n"
            f"💰 Сумма: {total}₽\n"
            f"💳 {payment_status}"
        )
    try:
        vk.messages.send(user_id=manager_id, message=notif, random_id=0,
                         keyboard=kb_manager_status(order_num, is_delivery))
        save_active_order(order_num, user_id, order, manager_id)
    except Exception as e:
        print(f"Ошибка уведомления: {e}")
    save_customer(user_id, order)

    if is_delivery:
        d = order["delivery"]
        addr = f"{d['street']}, д. {d['house']}"
        if d.get("apt"):
            addr += f", кв. {d['apt']}"
        send(vk, user_id,
            f"🎉 Заказ #{order_num} принят!\n\n"
            f"🚗 Доставка: {d['zone']}\n"
            f"🏠 {addr}\n"
            f"🕒 {order['pickup_time']}\n"
            f"💰 Итого с доставкой: {total}₽\n"
            f"💳 {payment_status}\n\n"
            f"Спасибо, {first_name}! Уже готовим 🌯🔥",
            kb_final())
    else:
        send(vk, user_id,
            f"🎉 Заказ #{order_num} принят!\n\n"
            f"📍 {order['point']}\n"
            f"⏰ Будет готов к {order['pickup_time']}\n"
            f"💰 Сумма: {total}₽\n"
            f"💳 {payment_status}\n\n"
            f"Ждём тебя, {first_name}! До встречи 🌯🔥",
            kb_final())


def safe_listen(vk_session):
    """Слушает события ВК. При обрыве сети — переподключается, а не падает"""
    fails = 0
    while True:
        try:
            # wait=25 — держим соединение дольше; при обрыве ВК сам вернёт события
            longpoll = VkLongPoll(vk_session, wait=25)
            fails = 0
            for event in longpoll.listen():
                yield event
        except requests.exceptions.ReadTimeout:
            # Обычный таймаут долгого опроса — это норма, молча переподключаемся
            continue
        except Exception as e:
            fails += 1
            wait = min(5 * fails, 30)  # 5,10,15... но не больше 30 сек
            print(f"Сбой связи с ВК: {e} — переподключаюсь через {wait} сек")
            time.sleep(wait)


def _abandoned_keyboard(state):
    """Возвращает клавиатуру для текущего шага, чтобы человек мог продолжить заказ."""
    step = state.get("step", "main")
    order = state.get("order", {})

    if step == "choose_point":
        return kb_points()
    if step == "delivery_zone":
        return kb_delivery_zones()
    if step == "delivery_apt":
        return kb_apt_skip()
    if step == "delivery_domofon":
        return kb_domofon()
    if step == "choose_category":
        return kb_categories(order.get("order_type", "pickup"))
    if step == "choose_item":
        cat = state.get("current_category")
        return kb_items(cat) if cat in MENU else kb_categories(order.get("order_type", "pickup"))
    if step == "choose_sauce_for_item":
        return kb_sauces()
    if step == "choose_extras_for_item":
        return kb_extras_page2() if state.get("extras_page", 1) == 2 else kb_extras_page1()
    if step == "delivery_time_mode":
        return kb_delivery_time()
    if step == "choose_time":
        point = order.get("point")
        if point:
            return kb_time(get_time_slots(point, min_minutes=order.get("min_minutes", 15)))
    if step == "confirm":
        return kb_confirm()
    if step == "choose_payment":
        kb = VkKeyboard(one_time=True)
        kb.add_button("💳 Оплатить онлайн", color=VkKeyboardColor.POSITIVE)
        kb.add_line()
        if order.get("order_type") == "delivery":
            kb.add_button("💳 Картой курьеру", color=VkKeyboardColor.SECONDARY)
            kb.add_line()
            kb.add_button("💵 Наличными", color=VkKeyboardColor.SECONDARY)
        else:
            kb.add_button("💵 Оплата при получении", color=VkKeyboardColor.SECONDARY)
        return kb.get_keyboard()
    if step == "wait_payment":
        return kb_wait_payment(order)
    if step == "delivery_change":
        return kb_change()

    # Для шагов, где нужно ввести текст (улица, дом, время, телефон),
    # клавиатуру не показываем — человек просто отвечает сообщением.
    return None


def abandoned_order_watcher(vk):
    """Напоминает о незавершённом заказе через 5 и 30 минут бездействия."""
    while True:
        time.sleep(ABANDONED_CHECK_INTERVAL)
        now = time.time()

        try:
            for user_id, state in list(user_states.items()):
                # Если заказ завершён/пользователь вернулся в главное меню — не напоминаем
                if state.get("step", "main") == "main":
                    continue

                last_activity = state.get("last_activity", now)
                inactive_for = now - last_activity
                stage = state.get("abandon_reminder_stage", 0)
                step = state.get("step")

                # Подсказка зависит от того, где человек остановился
                if step == "delivery_street":
                    tail = "Напиши улицу — и продолжим 🚗"
                elif step == "delivery_house":
                    tail = "Осталось указать номер дома 👇"
                elif step == "delivery_time_custom":
                    tail = "Напиши желаемое время в формате ЧЧ:ММ 👇"
                elif step == "enter_phone":
                    tail = "Осталось указать номер телефона в формате 89991234567 👇"
                elif step == "wait_payment":
                    tail = "Если уже оплатил — нажми «Я оплатил». Можно также выбрать другой способ оплаты 👇"
                else:
                    tail = "Продолжи с того места, где остановился 👇"

                # Второе напоминание — через 30 минут общей бездеятельности
                if stage < 2 and inactive_for >= ABANDONED_SECOND_TIMEOUT:
                    message = (
                        "🌯 Твоя шаурма всё ещё ждёт тебя 😏\n\n"
                        "Заказ так и не закончен, но мы всё сохранили.\n"
                        f"{tail}"
                    )
                    if send(vk, user_id, message, _abandoned_keyboard(state)):
                        state["abandon_reminder_stage"] = 2
                    continue

                # Первое напоминание — через 5 минут
                if stage < 1 and inactive_for >= ABANDONED_FIRST_TIMEOUT:
                    message = (
                        "🌯 Кажется, ты не закончил заказ.\n\n"
                        "Мы всё сохранили — можно продолжить прямо сейчас.\n"
                        f"{tail}"
                    )
                    if send(vk, user_id, message, _abandoned_keyboard(state)):
                        state["abandon_reminder_stage"] = 1

        except Exception as e:
            print(f"Ошибка в abandoned_order_watcher: {e}")


def payment_watcher(vk):
    """Фоновая проверка оплат — раз в 15 секунд"""
    while True:
        time.sleep(15)
        try:
            for pid in list(pending_payments.keys()):
                info = pending_payments.get(pid)
                if not info:
                    continue

                # Заказ висит больше 40 минут — убираем из ожидания
                if time.time() - info["created_at"] > 2400:
                    pending_payments.pop(pid, None)
                    print(f"Платёж {pid} просрочен, убран из ожидания")
                    continue

                status = check_payment(pid,
                    shop_id=info["shop_id"],
                    secret_key=info["secret_key"])

                if status == "succeeded":
                    pending_payments.pop(pid, None)
                    print(f"Платёж {pid} оплачен — отправляю заказ #{info['order_num']}")
                    _finalize_order(vk, info["user_id"], info["user_name"],
                        info["first_name"], info["order"], info["order_num"],
                        info["cart"], info["total"], "✅ Оплачено онлайн")
                    reset_state(info["user_id"])
                elif status == "canceled":
                    pending_payments.pop(pid, None)
                    print(f"Платёж {pid} отменён")
        except Exception as e:
            print(f"Ошибка в payment_watcher: {e}")


def main():
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()

    threading.Thread(target=payment_watcher, args=(vk,), daemon=True).start()
    threading.Thread(target=abandoned_order_watcher, args=(vk,), daemon=True).start()
    print("Бот запущен!")
    print(f"Данные клиентов: {CUSTOMERS_FILE}")
    print(f"Активные заказы: {ACTIVE_ORDERS_FILE}")

    for event in safe_listen(vk_session):
        if not (event.type == VkEventType.MESSAGE_NEW and event.to_me and not event.from_me):
            continue

        msg_key = f"{event.user_id}_{event.message_id}"
        if msg_key in processed_msgs:
            continue
        processed_msgs[msg_key] = time.time()
        if len(processed_msgs) > 1000:
            processed_msgs.clear()

        try:
          user_id = event.user_id
          text = event.text.strip()
        except:
            continue

        state = get_state(user_id)
        state["last_activity"] = time.time()
        step = state["step"]

        try:
            user_info = vk.users.get(user_ids=user_id)
            user_name = f"{user_info[0]['first_name']} {user_info[0]['last_name']}"
            first_name = user_info[0]['first_name']
        except:
            user_name = "Клиент"
            first_name = "Друг"

        # СТАТУСЫ ЗАКАЗА — кнопки менеджера
        # Кнопки inline, поэтому каждый старый заказ сохраняет свои кнопки статуса.
        if any(text.startswith(prefix) for prefix in ["🔥 Готовим #", "✅ Готов #", "🚗 Курьер выехал #", "✅ Доставлен #", "❌ Отменить #"]):
            try:
                order_num = text.split("#")[-1].strip()
                info = active_orders.get(order_num)
                if not info or int(info.get("manager_id", -1)) != user_id:
                    send(vk, user_id, "⚠️ Этот заказ не найден или у тебя нет доступа к его статусу.")
                    continue
                client_id = int(info["user_id"])
                if text.startswith("🔥"):
                    status = "🔥 Готовим"
                    client_text = f"🔥 Заказ #{order_num} уже готовим! Скоро будет готов 🌯"
                elif text.startswith("✅ Готов"):
                    status = "✅ Готов"
                    client_text = f"✅ Заказ #{order_num} готов! Можно забирать 🌯🔥"
                elif text.startswith("🚗"):
                    status = "🚗 Курьер выехал"
                    client_text = f"🚗 Заказ #{order_num} передан курьеру. Уже едет к тебе!"
                elif text.startswith("✅ Доставлен"):
                    status = "✅ Доставлен"
                    client_text = f"✅ Заказ #{order_num} доставлен. Приятного аппетита! 🌯🔥"
                else:
                    status = "❌ Отменён"
                    client_text = f"❌ Заказ #{order_num} отменён. Если это неожиданно — напиши нам, пожалуйста."
                info["status"] = status
                _save_json(ACTIVE_ORDERS_FILE, active_orders)
                send(vk, client_id, client_text, kb_main())
                send(vk, user_id, f"Статус заказа #{order_num}: {status}")
            except Exception as e:
                print(f"Ошибка статуса: {e}")
            continue

        # СТАРТ
        if text.lower() in ["начать", "start", "/start", "сначала", "❌ отмена",
                            "🔄 начать заново", "◀️ назад",
                            "🏠 вернуться в начало", "🏠 в начало"]:
            reset_state(user_id)
            send(vk, user_id,
                f"Привет, {first_name}! 👋\n\n"
                f"Добро пожаловать в Eat to End — шаурма из шашлыка 🌯🔥\n\n"
                f"🚗 Отличная новость — заработала доставка! Ежедневно с 12:00 до 01:00.\n\n"
                f"Выбери, как хочешь получить заказ 👇",
                kb_main())
            continue

        # ПОВТОР ПРОШЛОГО ЗАКАЗА
        if text == "🔁 Повторить заказ":
            last = get_customer(user_id).get("last_order")
            if not last or not last.get("items"):
                send(vk, user_id, "Пока нет прошлого заказа, который можно повторить 😊", kb_main())
                continue
            reset_state(user_id)
            state = get_state(user_id)
            import copy
            state["order"] = copy.deepcopy(last)
            state["order"]["pickup_time"] = None
            state["step"] = "repeat_order_confirm"
            send(vk, user_id, "🔁 Твой прошлый заказ:\n\n" + format_cart(state["order"]) + "\n\nПовторяем?", kb_repeat_order())
            continue

        if text in ["🛒 Корзина", "✏️ Корзина"]:
            if not state["order"].get("items"):
                send(vk, user_id, "🛒 Корзина пока пуста.", kb_main())
            else:
                state["step"] = "cart_edit"
                send(vk, user_id, "🛒 Твой заказ:\n\n" + format_cart(state["order"]) + "\n\nМожно изменить количество или удалить позицию 👇", kb_cart(state["order"]))
            continue

        if text == "💬 Обратная связь":
            send(vk, user_id,
                "💬 Хочешь оставить отзыв или пожелание? Нам важно твоё мнение!\n\n"
                "👉 Напиши нам напрямую: vk.com/id1118370233\n\n"
                "Мы читаем каждое сообщение и стараемся стать лучше 🙏")
            continue

        if text == "ℹ️ О нас":
            send(vk, user_id,
                "🌯 Eat to End — шаурма из шашлыка\n\n"
                "Мы готовим из качественных продуктов "
                "в стильном заведении. Мясо на углях — наша фишка.\n\n"
                "📍 Точки и режим работы:\n"
                "• Ленина 36/2 с 2 — ⏰ 09:00 — 05:00\n"
                "• Декабристов 4а — ⏰ 09:00 — 23:00\n"
                "• Советская 2/10 с 1 — ⛔ временно закрыта\n\n"
                "🚗 Доставка — ежедневно с 12:00 до 01:00\n\n"
                "💳 Оплата при получении, онлайн или картой курьеру\n"
                "📦 Заказ без очереди\n\n"
                "— Дружелюбно\n— Честно\n— Вкусно",
                kb_main())
            continue

        if text == "📍 Наши точки":
            send(vk, user_id,
                "📍 Наши точки:\n\n"
                "1. Ленина 36/2 с 2\n   ⏰ 09:00 — 05:00\n\n"
                "2. Декабристов 4а\n   ⏰ 09:00 — 23:00\n\n"
                "3. Советская 2/10 с 1\n   ⛔ Временно закрыта",
                kb_main())
            continue

        # САМОВЫВОЗ — сразу из главного меню
        if text == "🏃 Самовывоз":
            reset_state(user_id)
            state = get_state(user_id)
            state["order"]["order_type"] = "pickup"
            state["step"] = "choose_point"
            send(vk, user_id,
                "📍 Выбери точку самовывоза:\n\n✅ — открыто  ❌ — закрыто",
                kb_points())
            continue

        # ДОСТАВКА — сразу из главного меню
        if text == "🚗 Доставка":
            if DELIVERY_TEST_MODE and user_id != DELIVERY_TEST_USER:
                send(vk, user_id, "Доставка скоро будет доступна 🚗", kb_main())
                continue
            if not is_delivery_open():
                send(vk, user_id,
                    "😔 Доставка работает с 12:00 до 01:00.\nСейчас недоступна — попробуй позже или закажи самовывоз.",
                    kb_main())
                continue
            reset_state(user_id)
            state = get_state(user_id)
            state["order"]["order_type"] = "delivery"
            state["order"]["point"] = DELIVERY_POINT
            customer = get_customer(user_id)
            saved_d = customer.get("delivery")
            # На всякий случай берём адрес и из прошлого заказа, если отдельное поле ещё не было сохранено.
            if not saved_d and customer.get("last_order"):
                saved_d = customer["last_order"].get("delivery")
                if saved_d:
                    customer["delivery"] = dict(saved_d)
                    _save_json(CUSTOMERS_FILE, customers)
            if saved_d:
                state["step"] = "confirm_saved_address"
                addr = f"{saved_d.get('street')}, д. {saved_d.get('house')}"
                if saved_d.get("apt"):
                    addr += f", кв. {saved_d.get('apt')}"
                send(vk, user_id, f"🚗 Доставить снова сюда?\n\n🏠 {addr}\nЗона: {saved_d.get('zone')}", kb_saved_address())
            else:
                state["step"] = "delivery_zone"
                zones_txt = "\n".join(f"• {z} — {p}₽" for z, p in DELIVERY_ZONES.items())
                send(vk, user_id,
                    f"🚗 Доставка по зонам:\n{zones_txt}\n\n"
                    f"Минимальная сумма заказа — {DELIVERY_MIN_ORDER}₽\n\n"
                    f"Куда везём? Выбери зону 👇",
                    kb_delivery_zones())
            continue

        # ПОВТОР: подтверждение прошлого заказа
        if step == "repeat_order_confirm":
            if text == "✅ Повторить этот заказ":
                o = state["order"]
                if o.get("order_type") == "delivery":
                    if not is_delivery_open():
                        send(vk, user_id, "😔 Доставка сейчас закрыта. Можно выбрать самовывоз.", kb_main())
                        continue

                    # При повторе доставки ОБЯЗАТЕЛЬНО подтверждаем адрес,
                    # даже если он сохранён в прошлом заказе.
                    saved_d = o.get("delivery") or get_customer(user_id).get("delivery")
                    if saved_d:
                        state["order"]["delivery"] = dict(saved_d)
                        state["step"] = "repeat_confirm_address"
                        addr = f"{saved_d.get('street')}, д. {saved_d.get('house')}"
                        if saved_d.get("apt"):
                            addr += f", кв. {saved_d.get('apt')}"
                        send(vk, user_id,
                             f"🚗 Куда доставить повторный заказ?\n\n"
                             f"🏠 Прошлый адрес: {addr}\n"
                             f"Зона: {saved_d.get('zone')}\n\n"
                             f"Подтверди адрес 👇",
                             kb_saved_address())
                    else:
                        state["step"] = "delivery_zone"
                        send(vk, user_id, "🚗 Уточним адрес доставки. Выбери зону 👇", kb_delivery_zones())
                else:
                    if not o.get("point") or not is_point_open(o["point"]):
                        send(vk, user_id, "Эта точка сейчас закрыта. Выбери другую точку самовывоза 👇", kb_points())
                        state["step"] = "choose_point"
                        continue
                    state["upsell_extras_shown"] = True
                    state["upsell_drink_shown"] = True
                    start_checkout(vk, user_id, state)
            elif text == "✏️ Изменить заказ":
                # Состав можно менять, но адрес доставки после изменений
                # обязательно уточним ещё раз перед оформлением.
                if state["order"].get("order_type") == "delivery":
                    state["repeat_needs_address_confirm"] = True
                state["step"] = "cart_edit"
                send(vk, user_id, "🛒 Измени заказ 👇\n\n" + format_cart(state["order"]), kb_cart(state["order"]))
            else:
                send(vk, user_id, "Выбери действие 👇", kb_repeat_order())
            continue

        # ПОВТОР ДОСТАВКИ: подтверждение адреса
        if step == "repeat_confirm_address":
            if text == "✅ Да, сюда":
                # Адрес подтверждён — больше повторно его не спрашиваем.
                state["repeat_needs_address_confirm"] = False
                # Состав заказа уже загружен из прошлого заказа.
                # После подтверждения адреса продолжаем оформление.
                start_checkout(vk, user_id, state)
            elif text == "✏️ Другой адрес":
                # Клиент явно выбрал ввод нового адреса, поэтому старый
                # больше подтверждать не нужно — ведём по полному сценарию адреса.
                state["repeat_needs_address_confirm"] = False
                state["order"]["delivery"] = None
                state["step"] = "delivery_zone"
                zones_txt = "\n".join(f"• {z} — {p}₽" for z, p in DELIVERY_ZONES.items())
                send(vk, user_id,
                     f"🚗 Хорошо, укажем другой адрес.\n\n{zones_txt}\n\nВыбери зону 👇",
                     kb_delivery_zones())
            else:
                send(vk, user_id, "Подтверди прошлый адрес или выбери другой 👇", kb_saved_address())
            continue

        # РЕДАКТИРОВАНИЕ КОРЗИНЫ И КОЛИЧЕСТВА
        if step == "cart_edit":
            if text == "➕ Добавить ещё":
                state["step"] = "choose_category"
                send(vk, user_id, "Выбери категорию:", kb_categories(state["order"].get("order_type", "pickup")))
                continue
            if text == "🛒 Оформить заказ":
                start_checkout(vk, user_id, state)
                continue
            parts = text.split()
            if len(parts) == 2 and parts[1].isdigit() and parts[0] in ["➕", "➖", "🗑"]:
                idx = int(parts[1]) - 1
                items = state["order"].get("items", [])
                if 0 <= idx < len(items):
                    if parts[0] == "➕":
                        items[idx]["qty"] = items[idx].get("qty", 1) + 1
                    elif parts[0] == "➖":
                        qty = items[idx].get("qty", 1)
                        if qty > 1:
                            items[idx]["qty"] = qty - 1
                        else:
                            items.pop(idx)
                    else:
                        items.pop(idx)
                    if items:
                        send(vk, user_id, "🛒 Корзина обновлена:\n\n" + format_cart(state["order"]), kb_cart(state["order"]))
                    else:
                        state["step"] = "choose_category"
                        send(vk, user_id, "Корзина пуста. Добавим что-нибудь? 👇", kb_categories(state["order"].get("order_type", "pickup")))
                continue
            send(vk, user_id, "Выбери действие с корзиной 👇", kb_cart(state["order"]))
            continue

        # UPSELL ДОБАВКИ
        if step == "upsell_extra":
            idx = state.get("upsell_target_idx")
            if text == "➡️ Без добавки":
                start_checkout(vk, user_id, state)
                continue
            extra = None
            if text == f"🧀 Сыр +{EXTRAS['Сыр тертый']}₽":
                extra = "Сыр тертый"
            elif text == f"🥓 Бекон +{EXTRAS['Бекон']}₽":
                extra = "Бекон"
            if extra is not None and isinstance(idx, int) and 0 <= idx < len(state["order"]["items"]):
                if extra not in state["order"]["items"][idx].setdefault("extras", []):
                    state["order"]["items"][idx]["extras"].append(extra)
                send(vk, user_id, f"✅ {extra} добавлен.")
                start_checkout(vk, user_id, state)
            else:
                send(vk, user_id, "Выбери добавку или нажми «Без добавки» 👇", kb_upsell_extra())
            continue

        # UPSELL НАПИТКА
        if step == "upsell_drink":
            if text == "➡️ Без напитка":
                start_checkout(vk, user_id, state)
                continue
            selected = None
            for name, price in MENU["Напитки"].items():
                if text == f"🥤 {name} +{price}₽":
                    selected = (name, price)
                    break
            if selected:
                name, price = selected
                state["order"]["items"].append({"name": name, "price": price, "sauce": None, "extras": [], "cat": "Напитки", "qty": 1})
                send(vk, user_id, f"✅ {name} добавлен в заказ.")
                start_checkout(vk, user_id, state)
            else:
                send(vk, user_id, "Выбери напиток или нажми «Без напитка» 👇", kb_upsell_drink())
            continue

        # СОХРАНЁННЫЙ АДРЕС
        if step == "confirm_saved_address":
            if text == "✅ Да, сюда":
                saved_d = get_customer(user_id).get("delivery")
                if not saved_d:
                    state["step"] = "delivery_zone"
                    send(vk, user_id, "Выбери зону доставки 👇", kb_delivery_zones())
                    continue
                state["order"]["delivery"] = dict(saved_d)
                state["step"] = "choose_category"
                send(vk, user_id, "✅ Адрес подставили. Теперь собери заказ 👇", kb_categories("delivery"))
            elif text == "✏️ Другой адрес":
                state["step"] = "delivery_zone"
                send(vk, user_id, "Выбери новую зону доставки 👇", kb_delivery_zones())
            else:
                send(vk, user_id, "Выбери: прошлый адрес или новый 👇", kb_saved_address())
            continue

        # ДОСТАВКА: выбор зоны
        if step == "delivery_zone":
            matched_zone = None
            for zone, price in DELIVERY_ZONES.items():
                if text.startswith(zone) or zone in text:
                    matched_zone = (zone, price)
                    break
            if matched_zone:
                state["order"]["delivery"] = {
                    "zone": matched_zone[0],
                    "price": matched_zone[1],
                    "street": None, "house": None, "apt": None, "domofon": None,
                }
                state["step"] = "delivery_street"
                send(vk, user_id, "🏠 Напиши улицу:", None)
            else:
                send(vk, user_id, "Выбери зону из списка 👇", kb_delivery_zones())
            continue

        # ДОСТАВКА: улица
        if step == "delivery_street":
            street = text.strip()
            if len(street) < 2:
                send(vk, user_id, "Напиши название улицы 👇", None)
                continue
            state["order"]["delivery"]["street"] = street
            state["step"] = "delivery_house"
            send(vk, user_id, "🔢 Номер дома:", None)
            continue

        # ДОСТАВКА: дом
        if step == "delivery_house":
            house = text.strip()
            if len(house) < 1:
                send(vk, user_id, "Напиши номер дома 👇", None)
                continue
            state["order"]["delivery"]["house"] = house
            state["step"] = "delivery_apt"
            send(vk, user_id,
                "🚪 Номер квартиры (если есть)\n\nИли нажми «Без квартиры»:",
                kb_apt_skip())
            continue

        # ДОСТАВКА: квартира
        if step == "delivery_apt":
            if text == "Без квартиры":
                state["order"]["delivery"]["apt"] = None
                # Без квартиры домофон не спрашиваем — сразу в меню
                state["step"] = "choose_category"
                d = state["order"]["delivery"]
                save_delivery_address(user_id, d)
                addr = f"{d['street']}, д. {d['house']}"
                send(vk, user_id,
                    f"✅ Адрес: {addr}\n"
                    f"🚗 Зона: {d['zone']} (+{d['price']}₽)\n\n"
                    f"Теперь собери заказ. Минимум на доставку — {DELIVERY_MIN_ORDER}₽.\n\n"
                    f"Выбери категорию:",
                    kb_categories("delivery"))
            else:
                state["order"]["delivery"]["apt"] = text.strip()
                state["step"] = "delivery_domofon"
                send(vk, user_id, "🔔 Есть ли домофон?", kb_domofon())
            continue

        # ДОСТАВКА: домофон
        if step == "delivery_domofon":
            if text == "✅ Есть домофон":
                state["order"]["delivery"]["domofon"] = "есть"
            elif text == "❌ Нет домофона":
                state["order"]["delivery"]["domofon"] = "нет"
            else:
                send(vk, user_id, "Выбери вариант 👇", kb_domofon())
                continue
            state["step"] = "choose_category"
            d = state["order"]["delivery"]
            save_delivery_address(user_id, d)
            addr = f"{d['street']}, д. {d['house']}, кв. {d['apt']}"
            send(vk, user_id,
                f"✅ Адрес: {addr}\n"
                f"🔔 Домофон: {d['domofon']}\n"
                f"🚗 Зона: {d['zone']} (+{d['price']}₽)\n\n"
                f"Теперь собери заказ. Минимум на доставку — {DELIVERY_MIN_ORDER}₽.\n\n"
                f"Выбери категорию:",
                kb_categories("delivery"))
            continue

        # ВЫБОР ТОЧКИ
        if step == "choose_point":
            matched = None
            for point in MANAGERS.keys():
                if point in text:
                    matched = point
                    break
            if matched:
                if matched in COMING_SOON_POINTS:
                    send(vk, user_id,
                        f"🔜 Точка на {matched} откроется на этой неделе!\n\n"
                        f"Пока можешь сделать заказ на другой точке 👇",
                        kb_points_without_dekabristov())
                elif matched in CLOSED_POINTS:
                    send(vk, user_id,
                        f"⛔ Точка {matched} временно закрыта.\n\n"
                        f"Пока можешь сделать заказ на другой точке 👇",
                        kb_points_without_dekabristov())
                elif not is_point_open(matched):
                    open_h, close_h = HOURS[matched]
                    close_str = f"{close_h % 24:02d}:00" if close_h != 24 else "00:00"
                    send(vk, user_id,
                        f"😔 Точка {matched} сейчас закрыта.\n"
                        f"Режим работы: {open_h:02d}:00 — {close_str}\n\n"
                        f"Выбери другую точку или приходи в рабочее время!",
                        kb_points())
                else:
                    state["order"]["point"] = matched
                    state["step"] = "choose_category"
                    send(vk, user_id,
                        f"✅ Точка: {matched}\n\nЧто будешь? Выбери категорию:",
                        kb_categories(state["order"].get("order_type","pickup")))
            else:
                send(vk, user_id, "Выбери точку из списка 👇", kb_points())
            continue

        # ВЫБОР КАТЕГОРИИ
        if step == "choose_category":
            if text == "🛒 Оформить заказ":
                start_checkout(vk, user_id, state)
                continue

            matched_cat = None
            for cat in MENU.keys():
                if cat in text:
                    matched_cat = cat
                    break
            otype = state["order"].get("order_type", "pickup")
            # На доставке напитки недоступны
            if matched_cat and otype == "delivery" and matched_cat in DELIVERY_HIDDEN_CATS:
                send(vk, user_id, "🚗 На доставке напитки пока недоступны 😔\nВыбери из меню:", kb_categories(otype))
                continue
            if matched_cat:
                state["step"] = "choose_item"
                state["current_category"] = matched_cat
                send(vk, user_id, f"Выбери позицию из «{matched_cat}»:", kb_items(matched_cat))
            else:
                send(vk, user_id, "Выбери категорию 👇", kb_categories(otype))
            continue

        # ВЫБОР БЛЮДА
        if step == "choose_item":
            if text == "◀️ К категориям":
                state["step"] = "choose_category"
                cart = format_cart(state["order"])
                send(vk, user_id, f"🛒 Корзина:\n{cart}\n\nВыбери категорию:", kb_categories(state["order"].get("order_type","pickup")))
                continue

            cat = state.get("current_category", "")
            found = False
            for name, price in MENU.get(cat, {}).items():
                # Точное совпадение: кнопка содержит имя + цену вида "Название 350₽"
                expected = f"{name} {price}₽"
                if text == expected or text == name:
                    found = True
                    state["current_item"] = {"name": name, "price": price, "sauce": None, "extras": [], "cat": cat, "qty": 1}

                    if cat in SAUCE_CATS:
                        state["step"] = "choose_sauce_for_item"
                        send(vk, user_id,
                            f"✅ {name}\n\nВыбери соус:",
                            kb_sauces())
                    elif cat in EXTRAS_CATS:
                        state["step"] = "choose_extras_for_item"
                        state["extras_page"] = 1
                        send(vk, user_id,
                            f"✅ {name}\n\nХочешь добавки?",
                            kb_extras_page1())
                    else:
                        # Напитки — сразу добавляем
                        state["order"]["items"].append(state["current_item"])
                        state["current_item"] = None
                        state["step"] = "choose_category"
                        cart = format_cart(state["order"])
                        send(vk, user_id,
                            f"✅ {name} добавлен!\n\n🛒 Корзина:\n{cart}\n\nДобавить ещё или оформить?",
                            kb_after_item())
                    break

            if not found:
                send(vk, user_id, "Выбери позицию из списка 👇", kb_items(cat))
            continue

        # СОУС ДЛЯ ПОЗИЦИИ
        if step == "choose_sauce_for_item":
            if text in SAUCES:
                state["current_item"]["sauce"] = text
                state["step"] = "choose_extras_for_item"
                state["extras_page"] = 1
                send(vk, user_id, "➕ Хочешь добавки?", kb_extras_page1())
            else:
                send(vk, user_id, "Выбери соус 👇", kb_sauces())
            continue

        # ДОБАВКИ ДЛЯ ПОЗИЦИИ
        if step == "choose_extras_for_item":
            def _finish_item():
                state["order"]["items"].append(state["current_item"])
                nm = state["current_item"]["name"]
                state["current_item"] = None
                state["step"] = "choose_category"
                cart2 = format_cart(state["order"])
                send(vk, user_id,
                    f"✅ {nm} добавлен в корзину!\n\n🛒 Корзина:\n{cart2}\n\nДобавить ещё или оформить?",
                    kb_after_item())

            if text == "➡️ Далее":
                # На 1-й странице «Далее» ведёт на 2-ю, на 2-й — завершает
                if state.get("extras_page", 1) == 1:
                    state["extras_page"] = 2
                    send(vk, user_id, "➕ Ещё добавки:", kb_extras_page2())
                else:
                    _finish_item()
                continue

            if text == "✅ Без добавок":
                _finish_item()
                continue

            if text == "🥫 Доп соус +42₽":
                send(vk, user_id, "Выбери соус:", kb_extra_sauces())
                continue

            if text == "◀️ Назад к добавкам":
                state["extras_page"] = 1
                send(vk, user_id, "➕ Добавки:", kb_extras_page1())
                continue

            # Доп соус выбран
            for sauce in SAUCES[:-1]:
                if f"{sauce} +42₽" == text:
                    extra_name = f"Соус {sauce}"
                    if extra_name not in state["current_item"]["extras"]:
                        state["current_item"]["extras"].append(extra_name)
                    send(vk, user_id,
                        f"✅ {extra_name} добавлен\nЕщё добавки или «Без добавок»:",
                        kb_extras_page1())
                    break
            else:
                matched_extra = None
                for extra_name in EXTRAS.keys():
                    if extra_name in text:
                        matched_extra = extra_name
                        break
                if matched_extra:
                    if matched_extra not in state["current_item"]["extras"]:
                        state["current_item"]["extras"].append(matched_extra)
                    send(vk, user_id,
                        f"✅ {matched_extra} добавлен\nЕщё добавки или «Без добавок»:",
                        kb_extras_page1())
                else:
                    send(vk, user_id, "Выбери добавку 👇", kb_extras_page1())
            continue

        # ПОСЛЕ ДОБАВЛЕНИЯ ПОЗИЦИИ
        if step == "choose_category" and text == "➕ Добавить ещё":
            send(vk, user_id, "Выбери категорию:", kb_categories(state["order"].get("order_type","pickup")))
            continue

        if step == "choose_category" and text == "🛒 Оформить заказ":
            start_checkout(vk, user_id, state)
            continue

        # ДОСТАВКА: режим времени
        if step == "delivery_time_mode":
            if text == "⚡ Побыстрее (~45 мин)":
                state["order"]["pickup_time"] = "Побыстрее (~45 мин)"
                state["order"]["delivery_asap"] = True
                request_phone(vk, user_id, state)
                continue
            if text == "🕒 К определённому времени":
                state["step"] = "delivery_time_custom"
                now = datetime.datetime.now(TZ)
                earliest = (now + datetime.timedelta(minutes=90)).strftime("%H:%M")
                hint = f"🕒 Напиши желаемое время в формате ЧЧ:ММ"
                if DELIVERY_TIME_LIMITS_ENABLED:
                    hint += f"\n\nНе раньше чем {earliest} (через 90 минут)"
                else:
                    hint += "\n\n🧪 Тестовый режим: ограничений по времени нет"
                send(vk, user_id, hint, None)
                continue
            send(vk, user_id, "Выбери вариант 👇", kb_delivery_time())
            continue

        # ДОСТАВКА: ввод точного времени
        if step == "delivery_time_custom":
            if len(text) == 5 and ":" in text:
                try:
                    now = datetime.datetime.now(TZ)
                    h, m = map(int, text.split(":"))
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError
                    input_dt = datetime.datetime.combine(now.date(), datetime.time(h, m), tzinfo=TZ)
                    if input_dt < now:
                        input_dt += datetime.timedelta(days=1)
                    min_time = now + datetime.timedelta(minutes=90)
                    # Проверка ограничений доставки; для теста их можно полностью отключить
                    if DELIVERY_TIME_LIMITS_ENABLED:
                        open_h, close_h = DELIVERY_OPEN_H, DELIVERY_CLOSE_H
                        hh = input_dt.hour + input_dt.minute / 60
                        if close_h <= 24:
                            delivery_ok = open_h <= hh < close_h
                        else:
                            delivery_ok = hh >= open_h or hh < (close_h - 24)
                    else:
                        delivery_ok = True

                    if DELIVERY_TIME_LIMITS_ENABLED and input_dt < min_time:
                        send(vk, user_id,
                            f"⚠️ Слишком рано! Доставка не раньше чем через 90 минут "
                            f"(с {min_time.strftime('%H:%M')}). Напиши другое время:", None)
                    elif DELIVERY_TIME_LIMITS_ENABLED and not delivery_ok:
                        send(vk, user_id,
                            "⚠️ Доставка работает с 12:00 до 01:00. Выбери время в этом окне:", None)
                    else:
                        state["order"]["pickup_time"] = text
                        state["order"]["delivery_asap"] = False
                        request_phone(vk, user_id, state)
                except:
                    send(vk, user_id, "⚠️ Неверный формат. Напиши как 19:30:", None)
            else:
                send(vk, user_id, "⚠️ Напиши время в формате ЧЧ:ММ (например 19:30):", None)
            continue

        # ВРЕМЯ
        if step == "choose_time":
            min_min = state["order"].get("min_minutes", 15)
            slots = get_time_slots(state["order"]["point"], min_minutes=min_min)

            chosen_time = None

            if text in slots:
                chosen_time = text
            elif len(text) == 5 and ":" in text:
                try:
                    now = datetime.datetime.now(TZ)
                    h, m = map(int, text.split(":"))
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError
                    open_h, close_h = HOURS.get(state["order"]["point"], (9, 22))
                    min_time = now + datetime.timedelta(minutes=min_min)

                    # Определяем дату заказа с учётом ночных точек
                    input_dt = datetime.datetime.combine(now.date(), datetime.time(h, m), tzinfo=TZ)
                    # Если точка работает через полночь и введён час до времени закрытия — это следующий день
                    if close_h > 24 and h < (close_h - 24):
                        input_dt += datetime.timedelta(days=1)
                    # Если введённое время уже прошло сегодня — считаем на завтра
                    if input_dt < now:
                        input_dt += datetime.timedelta(days=1)

                    # Момент закрытия
                    if close_h <= 24:
                        close_dt = datetime.datetime.combine(input_dt.date(), datetime.time(close_h % 24, 0), tzinfo=TZ)
                        if close_h == 24:
                            close_dt = datetime.datetime.combine(input_dt.date(), datetime.time(23, 59), tzinfo=TZ)
                    else:
                        real_close = close_h - 24
                        base = input_dt.date() if h < real_close else input_dt.date() + datetime.timedelta(days=1)
                        close_dt = datetime.datetime.combine(base, datetime.time(real_close, 0), tzinfo=TZ)

                    # Момент открытия в дату заказа
                    open_dt = datetime.datetime.combine(input_dt.date(), datetime.time(open_h, 0), tzinfo=TZ)

                    if input_dt < min_time:
                        send(vk, user_id,
                            f"⚠️ Слишком рано! Минимум через {min_min} мин.\nВведи другое время:",
                            kb_time(slots))
                    elif input_dt > close_dt:
                        send(vk, user_id, "⚠️ Точка уже будет закрыта.\nВыбери другое время:", kb_time(slots))
                    elif close_h <= 24 and input_dt < open_dt:
                        send(vk, user_id, f"⚠️ Точка открывается в {open_h:02d}:00.", kb_time(slots))
                    else:
                        chosen_time = text
                except:
                    send(vk, user_id, "⚠️ Неверный формат. Напиши как 14:30:", kb_time(slots))
            else:
                send(vk, user_id, "Выбери время или напиши в формате ЧЧ:ММ 👇", kb_time(slots))

            if chosen_time:
                state["order"]["pickup_time"] = chosen_time
                request_phone(vk, user_id, state)
            continue

        # СОХРАНЁННЫЙ ТЕЛЕФОН
        if step == "confirm_saved_phone":
            if text == "✅ Использовать этот номер":
                phone = get_customer(user_id).get("phone")
                if phone:
                    state["order"]["phone"] = phone
                    state["step"] = "confirm"
                    order = state["order"]
                    cart = format_cart(order)
                    if order.get("order_type") == "delivery":
                        d = order["delivery"]
                        addr = f"{d['street']}, д. {d['house']}" + (f", кв. {d['apt']}" if d.get('apt') else "")
                        summary = f"🚗 Проверь заказ:\n\n🏠 {addr}\n🕒 {order['pickup_time']}\n📱 {phone}\n\n{cart}\n\nВсё верно? 👇"
                    else:
                        summary = f"📋 Проверь заказ:\n\n📍 {order['point']}\n⏰ {order['pickup_time']}\n📱 {phone}\n\n{cart}\n\nВсё верно? 👇"
                    send(vk, user_id, summary, kb_confirm())
                else:
                    state["step"] = "enter_phone"
                    send(vk, user_id, "📱 Напиши номер в формате: 89991234567")
            elif text == "✏️ Другой номер":
                state["step"] = "enter_phone"
                send(vk, user_id, "📱 Напиши новый номер в формате: 89991234567")
            else:
                send(vk, user_id, "Выбери сохранённый номер или введи новый 👇", kb_saved_phone())
            continue

        # ТЕЛЕФОН
        if step == "enter_phone":
            phone = text.strip().replace(" ", "").replace("-", "").replace("+", "")
            # Принимаем и 8XXXXXXXXXX, и +7XXXXXXXXXX / 7XXXXXXXXXX.
            if phone.startswith("7") and len(phone) == 11 and phone.isdigit():
                phone = "8" + phone[1:]

            if phone.startswith("8") and len(phone) == 11 and phone.isdigit():
                state["order"]["phone"] = phone

                # Сохраняем сразу после корректного ввода, а не только после
                # полного завершения заказа. Поэтому при следующей доставке
                # бот уже предложит использовать этот номер.
                customer = get_customer(user_id)
                customer["phone"] = phone
                _save_json(CUSTOMERS_FILE, customers)

                state["step"] = "confirm"
                order = state["order"]
                cart = format_cart(order)
                if order.get("order_type") == "delivery":
                    d = order["delivery"]
                    addr = f"{d['street']}, д. {d['house']}"
                    if d.get("apt"):
                        addr += f", кв. {d['apt']}"
                    summary = (
                        f"📋 Твой заказ:\n\n"
                        f"🚗 Доставка: {d['zone']}\n"
                        f"🏠 Адрес: {addr}\n"
                        f"🕒 Время: {order['pickup_time']}\n"
                        f"📱 Телефон: {phone}\n\n"
                        f"{cart}\n\n"
                        f"Всё верно? 👇"
                    )
                else:
                    summary = (
                        f"📋 Твой заказ:\n\n"
                        f"📍 {order['point']}\n"
                        f"⏰ Время готовности: {order['pickup_time']}\n"
                        f"📱 Телефон: {phone}\n\n"
                        f"{cart}\n\n"
                        f"Всё верно? 👇"
                    )
                send(vk, user_id, summary, kb_confirm())
            else:
                send(vk, user_id,
                    "⚠️ Неверный формат номера.\n\n"
                    "Напиши в формате: 89991234567\n"
                    "(11 цифр, начиная с 8)")
            continue

        # ПОДТВЕРЖДЕНИЕ
        if step == "confirm":
            if text == "✅ Подтвердить":
                order_counter = get_order_counter() + 1
                save_counter(order_counter)
                order = state["order"]
                total = get_total(order)
                state["order"]["order_num"] = order_counter
                cart = format_cart(order)

                state["step"] = "choose_payment"
                kb = VkKeyboard(one_time=True)
                kb.add_button("💳 Оплатить онлайн", color=VkKeyboardColor.POSITIVE)
                kb.add_line()
                if order.get("order_type") == "delivery":
                    kb.add_button("💳 Картой курьеру", color=VkKeyboardColor.SECONDARY)
                    kb.add_line()
                    kb.add_button("💵 Наличными", color=VkKeyboardColor.SECONDARY)
                else:
                    kb.add_button("💵 Оплата при получении", color=VkKeyboardColor.SECONDARY)
                send(vk, user_id,
                    f"✅ Заказ #{order_counter} оформлен!\n\n"
                    f"💰 Сумма: {total}₽\n\n"
                    f"Как будешь оплачивать?",
                    kb.get_keyboard())

            elif text == "🔄 Начать заново":
                reset_state(user_id)
                send(vk, user_id, "Хорошо, начнём заново 😊", kb_main())
            else:
                send(vk, user_id, "Нажми «Подтвердить» или «Начать заново» 👇", kb_confirm())
            continue

        # ВЫБОР ОПЛАТЫ
        if step == "choose_payment":
            order = state["order"]
            total = get_total(order)
            order_num = order.get("order_num", 0)
            cart = format_cart(order)

            if text == "💳 Оплатить онлайн":
                if order.get("order_type") == "delivery":
                    description = f"Заказ #{order_num} Eat to End — доставка {order['delivery']['zone']}"
                else:
                    description = f"Заказ #{order_num} Eat to End — {order['point']}"
                # Выбираем ключи в зависимости от точки
                phone = order.get("phone", "")
                items = order.get("items", [])
                if order["point"] == "Советская 2/10":
                    pay_url, pay_id = create_payment(total, order_num, description,
                        phone=phone, items=items,
                        shop_id=YUKASSA_SHOP_ID_SOVETSKAYA,
                        secret_key=YUKASSA_SECRET_KEY_SOVETSKAYA)
                else:
                    pay_url, pay_id = create_payment(total, order_num, description,
                        phone=phone, items=items)

                if pay_url:
                    state["order"]["payment_id"] = pay_id
                    state["step"] = "wait_payment"

                    # Регистрируем платёж для фоновой проверки
                    if order["point"] == "Советская 2/10":
                        w_shop, w_key = YUKASSA_SHOP_ID_SOVETSKAYA, YUKASSA_SECRET_KEY_SOVETSKAYA
                    else:
                        w_shop, w_key = YUKASSA_SHOP_ID, YUKASSA_SECRET_KEY

                    pending_payments[pay_id] = {
                        "user_id": user_id,
                        "user_name": user_name,
                        "first_name": first_name,
                        "order": dict(order),
                        "order_num": order_num,
                        "cart": cart,
                        "total": total,
                        "created_at": time.time(),
                        "shop_id": w_shop,
                        "secret_key": w_key,
                    }

                    send(vk, user_id,
                        f"💳 Ссылка для оплаты заказа #{order_num}:\n\n"
                        f"{pay_url}\n\n"
                        f"После оплаты заказ уйдёт на кухню автоматически 👌",
                        kb_wait_payment(order))
                else:
                    send(vk, user_id,
                        "⚠️ Не удалось создать ссылку на оплату.\nОплатишь при получении?",
                        kb_main())
                    # Всё равно принимаем заказ
                    _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Оплата при получении")
                    reset_state(user_id)
                continue

            if text == "💵 Оплата при получении":
                _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Оплата при получении")
                reset_state(user_id)
                continue

            if text == "💳 Картой курьеру":
                _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Картой курьеру")
                reset_state(user_id)
                continue

            if text == "💵 Наличными":
                state["step"] = "delivery_change"
                send(vk, user_id,
                    f"💵 С какой суммы подготовить сдачу?\n\n"
                    f"Итого к оплате: {total}₽\n\n"
                    f"Выбери или напиши свою сумму 👇",
                    kb_change())
                continue

        # ОЖИДАНИЕ ОПЛАТЫ
        if step == "wait_payment":
            order = state["order"]
            total = get_total(order)
            order_num = order.get("order_num", 0)
            cart = format_cart(order)

            if text == "✅ Я оплатил":
                payment_id = order.get("payment_id")
                status = check_payment(payment_id) if payment_id else None

                # Выбираем ключи для проверки
                if order.get("point") == "Советская 2/10":
                    status = check_payment(payment_id,
                        shop_id=YUKASSA_SHOP_ID_SOVETSKAYA,
                        secret_key=YUKASSA_SECRET_KEY_SOVETSKAYA) if payment_id else None
                else:
                    status = check_payment(payment_id) if payment_id else None

                if status == "succeeded":
                    if payment_id in pending_payments:
                        pending_payments.pop(payment_id, None)
                        _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "✅ Оплачено онлайн")
                    else:
                        send(vk, user_id, "✅ Оплата уже получена, заказ на кухне!", kb_main())
                    reset_state(user_id)
                elif status == "pending":
                    send(vk, user_id,
                        "⏳ Платёж ещё обрабатывается. Подожди минуту и нажми «Я оплатил» снова.",
                        kb_wait_payment(order))
                else:
                    if order.get("order_type") == "delivery":
                        hint = "⚠️ Оплата не найдена. Оплати онлайн ещё раз или выбери оплату курьеру 👇"
                    else:
                        hint = "⚠️ Оплата не найдена. Попробуй ещё раз или выбери оплату при получении."
                    send(vk, user_id, hint, kb_wait_payment(order))
                continue

            if text == "💵 Оплачу при получении":
                pending_payments.pop(order.get("payment_id"), None)
                _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Оплата при получении")
                reset_state(user_id)
                continue

            if text == "💳 Картой курьеру":
                pending_payments.pop(order.get("payment_id"), None)
                _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Картой курьеру")
                reset_state(user_id)
                continue

            if text == "💵 Наличными":
                pending_payments.pop(order.get("payment_id"), None)
                state["step"] = "delivery_change"
                send(vk, user_id,
                    f"💵 С какой суммы подготовить сдачу?\n\n"
                    f"Итого к оплате: {total}₽\n\n"
                    f"Выбери или напиши свою сумму 👇",
                    kb_change())
                continue

        # ДОСТАВКА: сдача с наличных
        if step == "delivery_change":
            order = state["order"]
            total = get_total(order)
            order_num = order.get("order_num", 0)
            cart = format_cart(order)
            pay_status = None
            if text == "Без сдачи":
                pay_status = "Наличными курьеру (без сдачи)"
            else:
                # Сумма из кнопки (1000₽) или введённая вручную
                digits = "".join(ch for ch in text if ch.isdigit())
                if digits:
                    change_from = int(digits)
                    if change_from < total:
                        send(vk, user_id,
                            f"⚠️ Сумма меньше стоимости заказа ({total}₽).\n"
                            f"Напиши сумму не меньше {total}₽ или выбери «Без сдачи»:",
                            kb_change())
                        continue
                    pay_status = f"Наличными, сдача с {change_from}₽"
                else:
                    send(vk, user_id, "Выбери сумму или напиши число 👇", kb_change())
                    continue
            pending_payments.pop(order.get("payment_id"), None)
            _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, pay_status)
            reset_state(user_id)
            continue

        # Дефолт
        send(vk, user_id, f"Привет, {first_name}! 👋\nВыбери действие:", kb_main())


if __name__ == "__main__":
    main()
