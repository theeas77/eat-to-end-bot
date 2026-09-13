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
import copy
import signal
import atexit
from zoneinfo import ZoneInfo
import dashboard  # веб-панель кассира (тот же процесс, тот же /data)

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
KITCHEN_LOAD_FILE = os.path.join(DATA_DIR, "kitchen_load.json")
STOP_LIST_FILE = os.path.join(DATA_DIR, "stop_list.json")
PENDING_PAYMENTS_FILE = os.path.join(DATA_DIR, "pending_payments.json")
PAYMENT_RECORDS_FILE = os.path.join(DATA_DIR, "payment_records.json")
USER_STATES_FILE = os.path.join(DATA_DIR, "unfinished_orders.json")

ORDER_PREFIX = "59"              # номера заказов: 59-0001, 59-0002 ...
PENDING_PAYMENT_TTL = 24 * 3600   # ждём позднюю онлайн-оплату до суток
PAYMENT_SLOW_AFTER = 40 * 60      # после 40 минут проверяем реже
PAYMENT_SLOW_INTERVAL = 5 * 60    # раз в 5 минут
PAYMENT_RECORD_TTL = 30 * 24 * 3600  # служебные записи оплат храним 30 дней
UNFINISHED_STATE_TTL = 12 * 3600  # незавершённый заказ восстанавливаем до 12 часов

# ЮКасса для Ленина и Промышленная
YUKASSA_SHOP_ID = "1378878"
YUKASSA_SECRET_KEY = "live_WocTCMSmoycyvMP8ttX9_M4w2dsBMBWugjizIPvU2do"

# ЮКасса для Советской
YUKASSA_SHOP_ID_SOVETSKAYA = "1254695"
YUKASSA_SECRET_KEY_SOVETSKAYA = "live_U_Z86aPDfocmL1uteRrfHhyXVigb4sqinsDwRD8v5Jo"

VK_TOKEN = "vk1.a.lbcUXPokTxgPCYnlF_UcqQGaHW4nbI2dkqpNUfqL2tGCrjhST6s-4yoeGf6z0xrx1B1TXjcaWMu1EAWDDrqfH9us2nT7381dpYQUaiiXbaZAwqZbpEVGQ9oxyw3Bqsu_mbdyWdFVKlhcbNZE3lybJXXGoadma1fWTdzjtADUvTTZR2bbIySqQn8_qlyj5bYTzaC1DzmOHoWGJkRH_szQsA"
ADMIN_VK_ID = 1118370233
# Префиксы текстов inline-кнопок статуса заказа (у менеджера и курьера).
STATUS_PREFIXES = [
    "🔥 Готовим #", "✅ Готов #", "🚗 Курьер выехал #", "✅ Доставлен #",
    "❌ Отменить #", "↩️ Возврат и отмена #", "🚫 Не отменять #", "⏱ Задержка +15 мин #",
]
COURIER_VK_ID = 72534661   # VK ID курьера: карточка доставки + кнопки статуса
ERROR_ALERT_VK_ID = 72534661  # Только сюда отправляются аварийные уведомления бота
STAFF = {1118370233}   # VK ID сотрудников: пульт (загрузка кухни + стоп-лист)
STOP_POINTS = ["Ленина 36/2", "Декабристов 4а"]   # точки со стоп-листом

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

# --- ДИНАМИЧЕСКОЕ ВРЕМЯ ДОСТАВКИ "ПОБЫСТРЕЕ" ---
# В часы пик кухня загружена сильнее, поэтому оценка времени больше.
DELIVERY_PEAK_HOURS = [(12, 14), (17, 19)]  # окна пика: [начало, конец)
DELIVERY_ASAP_NORMAL = 45   # минут в обычное время
DELIVERY_ASAP_PEAK = 60     # минут в часы пик


def get_asap_minutes():
    """Оценка «Побыстрее»: максимум из ручной загрузки кухни и авто-часов-пик.
    Возвращает (минут, подпись_о_загрузке)."""
    h = datetime.datetime.now(TZ).hour
    auto = DELIVERY_ASAP_NORMAL
    for start_h, end_h in DELIVERY_PEAK_HOURS:
        if start_h <= h < end_h:
            auto = DELIVERY_ASAP_PEAK
            break
    manual = load_kitchen_load()
    minutes = max(auto, manual)
    if minutes >= 75:
        note = "🔴 Кухня сильно загружена\n\n"
    elif minutes >= 60:
        note = "🟡 Кухня средне загружена\n\n"
    else:
        note = ""
    return minutes, note


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

# Плоский список стоп-листа формируем после объявления EXTRAS.
# В него входят и блюда/напитки, и добавки.
ALL_ITEMS = []

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

# Стоп-лист покрывает всё, что реально может закончиться на точке:
# блюда, напитки, кофе/чай, добавки и соусы.
# «Без соуса» не является товарным остатком, поэтому в стоп-лист не включаем.
ALL_ITEMS = (
    [(cat, name) for cat, items in MENU.items() for name in items]
    + [("Добавки", name) for name in EXTRAS.keys()]
    + [("Соусы", name) for name in SAUCES if name != "Без соуса"]
)

user_states = {}
processed_msgs = {}
pending_payments = {}  # payment_id -> данные заказа, ждущего оплаты
payment_records = {}   # payment_id -> состояние финализации
ORDER_COUNTER_LOCK = threading.Lock()
PAYMENT_FINALIZE_LOCK = threading.Lock()
JSON_SAVE_LOCK = threading.RLock()


def _load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"Ошибка чтения {path}: {e}")
    return default


def _save_json(path, data):
    """Атомарно сохраняет JSON; общий lock не даёт фоновым потокам писать один файл одновременно."""
    try:
        with JSON_SAVE_LOCK:
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
kitchen_load = _load_json(KITCHEN_LOAD_FILE, {"minutes": DELIVERY_ASAP_NORMAL})
stop_list = _load_json(STOP_LIST_FILE, {})   # {точка: [названия позиций в стопе]}
pending_payments = _load_json(PENDING_PAYMENTS_FILE, {})
payment_records = _load_json(PAYMENT_RECORDS_FILE, {})

# Восстанавливаем незавершённые сценарии после Restart/Deploy Railway.
_raw_states = _load_json(USER_STATES_FILE, {})
_now_ts = time.time()
for _uid, _state in _raw_states.items():
    try:
        _uid_int = int(_uid)
        _last = float(_state.get("last_activity", 0))
        _step = _state.get("step", "main")
        # Пульт сотрудника — служебный экран, его не восстанавливаем после Railway Restart/Deploy.
        if _step != "main" and not str(_step).startswith("staff_") and _now_ts - _last <= UNFINISHED_STATE_TTL:
            user_states[_uid_int] = _state
    except Exception:
        pass


def save_pending_payments():
    _save_json(PENDING_PAYMENTS_FILE, pending_payments)


def save_payment_records():
    _save_json(PAYMENT_RECORDS_FILE, payment_records)


def persist_user_states():
    """Сохраняет только незавершённые сценарии; главное меню хранить незачем."""
    try:
        snapshot = {
            str(uid): copy.deepcopy(st)
            for uid, st in list(user_states.items())
            if st.get("step", "main") != "main"
            and not str(st.get("step", "")).startswith("staff_")
        }
        _save_json(USER_STATES_FILE, snapshot)
    except Exception as e:
        print(f"Ошибка сохранения незавершённых заказов: {e}")


def runtime_state_saver():
    while True:
        time.sleep(5)
        persist_user_states()


def persist_runtime_state():
    persist_user_states()
    save_pending_payments()
    save_payment_records()


def install_shutdown_handlers():
    atexit.register(persist_runtime_state)

    def _handler(signum, frame):
        persist_runtime_state()
        raise SystemExit(0)

    try:
        signal.signal(signal.SIGTERM, _handler)
        signal.signal(signal.SIGINT, _handler)
    except Exception:
        pass


def get_customer(user_id):
    return customers.setdefault(str(user_id), {"phone": None, "delivery": None, "last_order": None})


def save_customer(user_id, order):
    c = get_customer(user_id)
    if order.get("phone"):
        c["phone"] = order["phone"]
    if order.get("delivery"):
        d = order["delivery"]
        c["delivery"] = {k: d.get(k) for k in ("zone", "price", "street", "house", "apt", "entrance", "floor", "domofon")}
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
    c["delivery"] = {k: delivery.get(k) for k in ("zone", "price", "street", "house", "apt", "entrance", "floor", "domofon")}
    _save_json(CUSTOMERS_FILE, customers)


def save_active_order(order_num, user_id, order, manager_id, total=None, payment_status=None,
                      manager_notification=None, client_notification=None):
    """Сначала надёжно сохраняет заказ в /data, затем уведомления можно дослать повторно."""
    key = str(order_num)
    existing = active_orders.get(key)
    if existing:
        # Не сбрасываем уже выставленный статус при повторном вызове финализации.
        entry = existing
        entry.update({
            "user_id": user_id,
            "order_type": order.get("order_type", "pickup"),
            "point": order.get("point"),
            "shop_kind": shop_kind_for_order(order),
            "manager_id": manager_id,
            "total": total,
            "payment_status": payment_status,
            "comment": order.get("comment", ""),
            "pickup_time": order.get("pickup_time"),
        })
    else:
        entry = {
            "user_id": user_id,
            "order_type": order.get("order_type", "pickup"),
            "point": order.get("point"),
            "shop_kind": shop_kind_for_order(order),
            "manager_id": manager_id,
            "status": "Принят",
            "created_at": time.time(),
            "total": total,
            "payment_status": payment_status,
            "comment": order.get("comment", ""),
            "pickup_time": order.get("pickup_time"),
            "manager_notified": False,
            "client_notified": False,
        }

    entry["manager_notification"] = manager_notification or entry.get("manager_notification")
    entry["client_notification"] = client_notification or entry.get("client_notification")
    entry["payment_id"] = order.get("payment_id") or entry.get("payment_id")

    if order.get("order_type") == "delivery":
        d = order.get("delivery", {})
        addr = f"{d.get('street', '')}, д. {d.get('house', '')}"
        if d.get("apt"):
            addr += f", кв. {d['apt']}"
        if d.get("entrance"):
            addr += f", подъезд {d['entrance']}"
        if d.get("floor"):
            addr += f", этаж {d['floor']}"
        if d.get("domofon"):
            addr += f" (домофон {d['domofon']})"
        entry["address"] = addr
        entry["zone"] = d.get("zone", "")
        entry["phone"] = order.get("phone", "")

    active_orders[key] = entry
    _save_json(ACTIVE_ORDERS_FILE, active_orders)
    return entry



def load_kitchen_load():
    try:
        return int(kitchen_load.get("minutes", DELIVERY_ASAP_NORMAL))
    except Exception:
        return DELIVERY_ASAP_NORMAL


def save_kitchen_load(minutes):
    kitchen_load["minutes"] = int(minutes)
    _save_json(KITCHEN_LOAD_FILE, kitchen_load)


def refresh_order_prices(order):
    """Обновляет повторный/сохранённый заказ по текущему MENU и текущим тарифам доставки.
    Возвращает (позиции_которых_больше_нет, зона_недоступна)."""
    missing = []
    for item in order.get("items", []):
        name = item.get("name")
        cat = item.get("cat")
        found_cat = None
        if cat in MENU and name in MENU[cat]:
            found_cat = cat
        else:
            for c, menu_items in MENU.items():
                if name in menu_items:
                    found_cat = c
                    break
        if found_cat is None:
            missing.append(name or "Неизвестная позиция")
            continue
        item["cat"] = found_cat
        item["price"] = MENU[found_cat][name]
        try:
            item["qty"] = max(1, int(item.get("qty", 1)))
        except Exception:
            item["qty"] = 1

    invalid_zone = False
    if order.get("order_type") == "delivery" and order.get("delivery"):
        d = order["delivery"]
        zone = d.get("zone")
        if zone in DELIVERY_ZONES:
            d["price"] = DELIVERY_ZONES[zone]
        elif zone:
            invalid_zone = True
    return missing, invalid_zone


def stopped_for_order(order):
    """Позиции в стопе для точки заказа. Доставка наследует стоп точки-кухни."""
    if order.get("order_type") == "delivery":
        point = DELIVERY_POINT
    else:
        point = order.get("point", "")
    return set(stop_list.get(point, []))


def render_stop_list(point):
    stopped = set(stop_list.get(point, []))
    lines = [f"⛔ Стоп-лист — {point}", "",
             "Отправь номер, чтобы переключить позицию (можно несколько через пробел):", ""]
    last_cat = None
    for idx, (cat, name) in enumerate(ALL_ITEMS, start=1):
        if cat != last_cat:
            lines.append(f"— {cat} —")
            last_cat = cat
        mark = "⛔" if name in stopped else "✅"
        lines.append(f"{idx}. {mark} {name}")
    lines.append("")
    lines.append("✅ в продаже · ⛔ в стопе")
    return "\n".join(lines)


def toggle_stop(point, idx):
    if not (1 <= idx <= len(ALL_ITEMS)):
        return None
    name = ALL_ITEMS[idx - 1][1]
    lst = stop_list.setdefault(point, [])
    if name in lst:
        lst.remove(name)
        res = f"✅ «{name}» — снова в продаже"
    else:
        lst.append(name)
        res = f"⛔ «{name}» — в стоп"
    _save_json(STOP_LIST_FILE, stop_list)
    return res


ACTIVE_ORDER_TTL = 24 * 3600  # заказы старше суток считаем завершёнными


def cleanup_active_orders():
    """Храним и активные, и финальные статусы 24 часа.
    Это не даёт старой inline-кнопке вернуть завершённый заказ назад в работу."""
    now = time.time()
    to_delete = []
    for onum, info in list(active_orders.items()):
        created = info.get("created_at")
        is_old = (created is not None) and (now - created > ACTIVE_ORDER_TTL)
        # Pending refund важнее 24-часового TTL заказа: держим запись до финального статуса возврата.
        if is_old and info.get("refund_status") != "pending":
            to_delete.append(onum)
    if to_delete:
        for onum in to_delete:
            active_orders.pop(onum, None)
        _save_json(ACTIVE_ORDERS_FILE, active_orders)
        print(f"Очистка active_orders: удалено {len(to_delete)}")



def cleanup_payment_records():
    """Удаляет служебные payment_records старше 30 дней, чтобы файл не рос бесконечно."""
    now = time.time()
    removed = []
    for pid, info in list(payment_records.items()):
        try:
            updated = float(info.get("updated_at", 0))
        except Exception:
            updated = 0
        # Пока платёж реально ещё ожидается, его запись не трогаем.
        if pid in pending_payments:
            continue
        if not updated or now - updated > PAYMENT_RECORD_TTL:
            removed.append(pid)
    if removed:
        for pid in removed:
            payment_records.pop(pid, None)
        save_payment_records()
        print(f"Очистка payment_records: удалено {len(removed)}")


def active_orders_cleaner():
    """Фоновая очистка заказов и служебных записей — раз в 30 минут."""
    while True:
        try:
            cleanup_active_orders()
            cleanup_payment_records()
        except Exception as e:
            print(f"Ошибка фоновой очистки: {e}")
        time.sleep(1800)


def notification_retry_watcher(vk):
    """Досылает все важные VK-уведомления, если первая отправка не удалась.

    Помимо первоначальной карточки заказа повторяет:
    - новый статус клиенту;
    - карточку курьеру после «Курьер выехал».
    Поэтому краткий сбой VK больше не приводит к потере статуса/доставки.
    """
    while True:
        time.sleep(30)
        changed = False
        try:
            for order_num, info in list(active_orders.items()):
                if not info.get("manager_notified") and info.get("manager_notification"):
                    manager_id = int(info.get("manager_id", ADMIN_VK_ID))
                    ok = send(vk, manager_id, info["manager_notification"],
                              kb_manager_status(order_num, info.get("order_type") == "delivery"))
                    if ok:
                        info["manager_notified"] = True
                        info["manager_notify_failures"] = 0
                        changed = True
                    else:
                        info["manager_notify_failures"] = int(info.get("manager_notify_failures", 0)) + 1
                        if info["manager_notify_failures"] == 3:
                            send_emergency_alert(vk,
                                "Повторно не удаётся уведомить кассира",
                                f"Заказ #{order_num}, менеджер VK ID {manager_id}. Уже 3 фоновые попытки.")
                        changed = True

                if not info.get("client_notified") and info.get("client_notification"):
                    client_id = int(info.get("user_id"))
                    if send(vk, client_id, info["client_notification"], kb_final()):
                        info["client_notified"] = True
                        info["client_notify_failures"] = 0
                        changed = True
                    else:
                        info["client_notify_failures"] = int(info.get("client_notify_failures", 0)) + 1
                        if info["client_notify_failures"] == 3:
                            send_emergency_alert(vk,
                                "Повторно не удаётся уведомить клиента",
                                f"Заказ #{order_num}, клиент VK ID {client_id}. Уже 3 фоновые попытки.")
                        changed = True

                # Статус заказа клиенту. Храним последнее актуальное сообщение,
                # поэтому даже после краткого падения VK клиент получит текущий статус.
                status_msg = info.get("pending_client_status_notification")
                if status_msg:
                    client_id = int(info.get("user_id"))
                    if send(vk, client_id, status_msg, kb_main()):
                        info.pop("pending_client_status_notification", None)
                        info["status_notify_failures"] = 0
                        changed = True
                        print(f"STATUS RETRY OK #{order_num} -> client {client_id}")
                    else:
                        info["status_notify_failures"] = int(info.get("status_notify_failures", 0)) + 1
                        if info["status_notify_failures"] == 3:
                            send_emergency_alert(vk,
                                "Не удаётся отправить статус клиенту",
                                f"Заказ #{order_num}, клиент VK ID {client_id}. Уже 3 фоновые попытки.")
                        changed = True

            if changed:
                _save_json(ACTIVE_ORDERS_FILE, active_orders)
        except Exception as e:
            print(f"Ошибка повторной отправки уведомлений: {e}")
            send_emergency_alert(vk, "Ошибка фоновой досылки уведомлений", str(e)[:500])

# --- НАПОМИНАНИЕ О НЕЗАВЕРШЁННОМ ЗАКАЗЕ ---
ABANDONED_FIRST_TIMEOUT = 5 * 60     # первое напоминание через 5 минут бездействия
ABANDONED_SECOND_TIMEOUT = 30 * 60   # второе напоминание через 30 минут бездействия
ABANDONED_CHECK_INTERVAL = 30        # проверяем раз в 30 секунд


def load_counter():
    """Глобальный счётчик заказов. При переходе со старой дневной нумерации начинает с 59-0001."""
    try:
        data = _load_json(COUNTER_FILE, {})
        if data.get("format") != "59-v1":
            return 0
        return int(data.get("counter", 0))
    except Exception:
        return 0




def save_counter(counter):
    """Атомарно сохраняет глобальный счётчик."""
    _save_json(COUNTER_FILE, {"format": "59-v1", "counter": int(counter)})




def create_payment(amount, order_num, description, phone=None, items=None, delivery_price=0,
                   shop_id=None, secret_key=None, idempotence_key=None):
    """Создаёт платёж ЮKassa. Сумма чека строго совпадает с суммой платежа."""
    shop_id = shop_id or YUKASSA_SHOP_ID
    secret_key = secret_key or YUKASSA_SECRET_KEY
    print(f"Создаю платёж: shop_id={shop_id}, amount={amount}, order={order_num}")
    try:
        # Ключ создаётся один раз на попытку оплаты заказа и сохраняется в state.
        # Если Railway не получил ответ ЮKassa из-за таймаута, повторный запрос
        # с тем же ключом не создаст второй платёж.
        idempotence_key = idempotence_key or str(uuid.uuid4())
        receipt_items = []
        receipt_total = 0

        if items:
            for item in items:
                qty = max(1, int(item.get("qty", 1)))
                unit_amount = int(item["price"])
                for e in item.get("extras", []):
                    unit_amount += int(EXTRAS.get(e, 42))
                receipt_total += unit_amount * qty
                receipt_items.append({
                    "description": item["name"][:128],
                    "quantity": f"{qty}.00",
                    "amount": {"value": f"{unit_amount:.2f}", "currency": "RUB"},
                    "vat_code": 1,
                    "payment_mode": "full_payment",
                    "payment_subject": "commodity"
                })
        else:
            receipt_total = int(amount) - int(delivery_price or 0)
            receipt_items.append({
                "description": description[:128],
                "quantity": "1.00",
                "amount": {"value": f"{receipt_total:.2f}", "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "commodity"
            })

        delivery_price = int(delivery_price or 0)
        if delivery_price > 0:
            receipt_items.append({
                "description": "Доставка",
                "quantity": "1.00",
                "amount": {"value": f"{delivery_price:.2f}", "currency": "RUB"},
                "vat_code": 1,
                "payment_mode": "full_payment",
                "payment_subject": "service"
            })
            receipt_total += delivery_price

        if receipt_total != int(amount):
            print(f"ОШИБКА ЧЕКА: позиции={receipt_total}₽, платёж={amount}₽")
            return None, None

        payload = {
            "amount": {"value": f"{int(amount):.2f}", "currency": "RUB"},
            "confirmation": {"type": "redirect", "return_url": "https://vk.com"},
            "capture": True,
            "description": description,
            "metadata": {"order_num": str(order_num)},
            "receipt": {"items": receipt_items}
        }

        # Для чека переводим 8XXXXXXXXXX в международный +7XXXXXXXXXX.
        if phone:
            clean = "".join(ch for ch in str(phone) if ch.isdigit())
            if len(clean) == 11 and clean.startswith("8"):
                clean = "7" + clean[1:]
            if len(clean) == 11 and clean.startswith("7"):
                payload["receipt"]["customer"] = {"phone": "+" + clean}

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


def cancel_payment(payment_id, shop_id=None, secret_key=None, idempotence_key=None):
    """Пытается отменить платёж ЮKassa. Для обычной одностадийной оплаты
    pending-платёж ЮKassa может не позволить отменить; такой платёж мы помечаем
    заброшенным и автоматически вернём деньги, если старая ссылка всё же оплатится.
    """
    shop_id = shop_id or YUKASSA_SHOP_ID
    secret_key = secret_key or YUKASSA_SECRET_KEY
    try:
        response = requests.post(
            f"https://api.yookassa.ru/v3/payments/{payment_id}/cancel",
            auth=(shop_id, secret_key),
            headers={
                "Idempotence-Key": idempotence_key or str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            json={}, timeout=10
        )
        try:
            data = response.json()
        except Exception:
            data = {}
        return response.status_code, data
    except Exception as e:
        return None, {"error": str(e)}


def create_refund(payment_id, amount, order_num, shop_id=None, secret_key=None,
                  idempotence_key=None, description=None):
    """Создаёт полный возврат успешного платежа ЮKassa."""
    shop_id = shop_id or YUKASSA_SHOP_ID
    secret_key = secret_key or YUKASSA_SECRET_KEY
    try:
        payload = {
            "amount": {"value": f"{int(amount):.2f}", "currency": "RUB"},
            "payment_id": payment_id,
            "description": (description or f"Возврат заказа #{order_num}")[:128],
        }
        response = requests.post(
            "https://api.yookassa.ru/v3/refunds",
            auth=(shop_id, secret_key),
            headers={
                "Idempotence-Key": idempotence_key or str(uuid.uuid4()),
                "Content-Type": "application/json",
            },
            json=payload, timeout=10
        )
        try:
            data = response.json()
        except Exception:
            data = {}
        if 200 <= response.status_code < 300 and data.get("id"):
            return data
        return None
    except Exception:
        return None


def check_refund(refund_id, shop_id=None, secret_key=None):
    """Проверяет фактический статус возврата ЮKassa."""
    shop_id = shop_id or YUKASSA_SHOP_ID
    secret_key = secret_key or YUKASSA_SECRET_KEY
    try:
        response = requests.get(
            f"https://api.yookassa.ru/v3/refunds/{refund_id}",
            auth=(shop_id, secret_key), timeout=10
        )
        data = response.json()
        return data.get("status"), data
    except Exception as e:
        return None, {"error": str(e)}


def shop_kind_for_order(order):
    return "sovetskaya" if order.get("point") == "Советская 2/10" else "default"


def credentials_for_shop_kind(kind):
    if kind == "sovetskaya":
        return YUKASSA_SHOP_ID_SOVETSKAYA, YUKASSA_SECRET_KEY_SOVETSKAYA
    return YUKASSA_SHOP_ID, YUKASSA_SECRET_KEY


def finalize_paid_payment(vk, payment_id, fallback_info=None):
    """Единая точка финализации онлайн-платежа.
    Защищает от дубля и повторно проверяет стоп-лист/время в момент succeeded.
    Если заказ уже нельзя выполнить, на кухню он не уходит — создаётся полный возврат.
    """
    with PAYMENT_FINALIZE_LOCK:
        record = payment_records.get(payment_id, {})
        if record.get("status") == "finalized":
            pending_payments.pop(payment_id, None)
            save_pending_payments()
            return "already"
        if record.get("status") in {"unfulfillable_refunded", "unfulfillable_refund_pending"}:
            pending_payments.pop(payment_id, None)
            save_pending_payments()
            return "refunded"

        info = pending_payments.get(payment_id) or fallback_info
        if not info:
            return "missing"

        order_num = str(info["order_num"])
        order = info["order"]

        # Если заказ уже устойчиво записан, повторно кухне его не шлём.
        if order_num in active_orders:
            payment_records[payment_id] = {
                "status": "finalized", "order_num": order_num, "updated_at": time.time()
            }
            pending_payments.pop(payment_id, None)
            save_payment_records()
            save_pending_payments()
            return "already"

        # Самая поздняя проверка перед кухней: товар/добавка/соус могли попасть
        # в стоп, а выбранное точное время могло уже пройти, пока клиент платил.
        blockers = paid_order_blockers(order, reference_ts=info.get("created_at"))
        if blockers:
            kind = info.get("shop_kind") or shop_kind_for_order(order)
            shop_id, secret_key = credentials_for_shop_kind(kind)
            refund_key = info.get("unfulfillable_refund_idempotence_key") or str(uuid.uuid4())
            info["unfulfillable_refund_idempotence_key"] = refund_key
            info["paid_blockers"] = blockers
            pending_payments[payment_id] = info
            save_pending_payments()

            refund = create_refund(
                payment_id, int(info.get("total", 0)), order_num,
                shop_id=shop_id, secret_key=secret_key,
                idempotence_key=refund_key,
                description=f"Автовозврат недоступного заказа #{order_num}"
            )

            if refund and refund.get("status") in ("succeeded", "pending"):
                refund_status = refund.get("status")
                payment_records[payment_id] = {
                    "status": "unfulfillable_refunded" if refund_status == "succeeded" else "unfulfillable_refund_pending",
                    "order_num": order_num,
                    "refund_id": refund.get("id"),
                    "refund_status": refund_status,
                    "shop_kind": kind,
                    "user_id": info.get("user_id"),
                    "total": info.get("total"),
                    "reason": blockers,
                    "updated_at": time.time(),
                }
                pending_payments.pop(payment_id, None)
                save_payment_records()
                save_pending_payments()

                client_id = info.get("user_id")
                if client_id:
                    reason_text = "; ".join(blockers[:3])
                    refund_text = (
                        "Возврат уже выполнен." if refund_status == "succeeded"
                        else "Возврат создан и сейчас обрабатывается ЮKassa."
                    )
                    send(vk, int(client_id),
                         f"⚠️ Оплата заказа #{order_num} прошла, но заказ уже нельзя отправить на кухню: {reason_text}.\n\n"
                         f"{refund_text} Сумма {info.get('total')}₽ вернётся тем же способом оплаты. "
                         "Пожалуйста, оформи новый заказ с актуальным временем/позициями.",
                         kb_main())
                send_emergency_alert(vk,
                    "Оплаченный заказ не отправлен на кухню — создан возврат",
                    f"Заказ #{order_num}. Причины: {'; '.join(blockers)}. Refund {refund.get('id')} ({refund_status}).")
                if info.get("user_id"):
                    reset_state(int(info["user_id"]))
                return "refunded"

            if refund and refund.get("status") == "canceled":
                payment_records[payment_id] = {
                    "status": "unfulfillable_refund_failed",
                    "order_num": order_num, "refund_id": refund.get("id"),
                    "refund_status": "canceled", "shop_kind": kind,
                    "user_id": info.get("user_id"), "total": info.get("total"),
                    "reason": blockers, "updated_at": time.time(),
                }
                pending_payments.pop(payment_id, None)
                save_payment_records(); save_pending_payments()
                send_emergency_alert(vk,
                    "КРИТИЧНО: оплата прошла, но автовозврат недоступного заказа отменён ЮKassa",
                    f"Заказ #{order_num}, payment_id {payment_id}, сумма {info.get('total')}₽. Нужен ручной возврат.")
                return "refund_failed"

            # Сетевой сбой/непонятный ответ: заказ на кухню НЕ отправляем.
            # pending оставляем, чтобы payment_watcher повторил возврат тем же idempotence key.
            if not info.get("unfulfillable_refund_fail_alerted"):
                info["unfulfillable_refund_fail_alerted"] = True
                pending_payments[payment_id] = info
                save_pending_payments()
                send_emergency_alert(vk,
                    "КРИТИЧНО: оплаченный заказ заблокирован, возврат пока не создан",
                    f"Заказ #{order_num}, payment_id {payment_id}. Причины: {'; '.join(blockers)}. Бот повторит возврат автоматически.")
            return "refund_retry"

        refresh_asap_label(order)
        info["cart"] = format_cart(order)

        payment_records[payment_id] = {
            "status": "processing", "order_num": order_num, "updated_at": time.time()
        }
        save_payment_records()

        _finalize_order(vk, int(info["user_id"]), info.get("user_name", "Клиент"),
                        info.get("first_name", "Друг"), order, order_num,
                        info["cart"], info["total"], "✅ Оплачено онлайн")

        payment_records[payment_id] = {
            "status": "finalized", "order_num": order_num, "updated_at": time.time()
        }
        pending_payments.pop(payment_id, None)
        save_payment_records()
        save_pending_payments()
        return "finalized"


def get_order_counter():
    return load_counter()


def next_order_num():
    """Выдаёт уникальный номер вида 59-0001, 59-0002 ..."""
    with ORDER_COUNTER_LOCK:
        counter = load_counter() + 1
        save_counter(counter)
    return f"{ORDER_PREFIX}-{counter:04d}"



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
    persist_user_states()


def register_user_activity(state):
    """Фиксирует действие пользователя и запускает новый цикл напоминаний 5/30 минут."""
    state["last_activity"] = time.time()
    if not str(state.get("step", "main")).startswith("staff_"):
        state["abandon_reminder_stage"] = 0


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
        return open_h <= h <= close_h
    # Ровно 01:00 считаем допустимой границей; после 01:00 доставка закрыта.
    return h >= open_h or h <= (close_h - 24)


def delivery_window_bounds(service_date):
    """Окно доставки для одной смены: service_date 12:00 -> следующий день 01:00."""
    open_dt = datetime.datetime.combine(service_date, datetime.time(DELIVERY_OPEN_H, 0), tzinfo=TZ)
    close_dt = datetime.datetime.combine(
        service_date + datetime.timedelta(days=1),
        datetime.time(DELIVERY_CLOSE_H - 24, 0),
        tzinfo=TZ,
    )
    return open_dt, close_dt


def current_delivery_close_datetime(now=None):
    """Закрытие текущей работающей смены доставки; None, если сейчас доставка закрыта."""
    now = now or datetime.datetime.now(TZ)
    hh = now.hour + now.minute / 60
    if hh >= DELIVERY_OPEN_H:
        _, close_dt = delivery_window_bounds(now.date())
        return close_dt
    if hh <= (DELIVERY_CLOSE_H - 24):
        _, close_dt = delivery_window_bounds(now.date() - datetime.timedelta(days=1))
        return close_dt
    return None


def next_delivery_open_datetime(now=None):
    """Ближайшее следующее открытие доставки в 12:00."""
    now = now or datetime.datetime.now(TZ)
    today_open = datetime.datetime.combine(now.date(), datetime.time(DELIVERY_OPEN_H, 0), tzinfo=TZ)
    if now < today_open:
        return today_open
    return today_open + datetime.timedelta(days=1)


def day_word(dt, now=None):
    now = now or datetime.datetime.now(TZ)
    if dt.date() == now.date():
        return "сегодня"
    if dt.date() == now.date() + datetime.timedelta(days=1):
        return "завтра"
    return dt.strftime("%d.%m")


def resolve_preorder_datetime(order, h, m, now=None):
    """Привязывает предзаказ к тому рабочему окну, которое было предложено клиенту.
    Это не позволяет после паузы принять уже прошедшее время как время следующего дня."""
    now = now or datetime.datetime.now(TZ)
    raw_date = order.get("preorder_service_date")
    try:
        service_date = datetime.date.fromisoformat(raw_date) if raw_date else now.date()
    except Exception:
        service_date = now.date()
    open_dt, close_dt = delivery_window_bounds(service_date)
    hh = h + m / 60
    if hh >= DELIVERY_OPEN_H:
        candidate = datetime.datetime.combine(service_date, datetime.time(h, m), tzinfo=TZ)
    elif hh <= (DELIVERY_CLOSE_H - 24):
        candidate = datetime.datetime.combine(service_date + datetime.timedelta(days=1), datetime.time(h, m), tzinfo=TZ)
    else:
        return None, open_dt, close_dt
    return candidate, open_dt, close_dt


def resolve_pickup_datetime(point, h, m, now=None):
    """Правильно трактует ручное время для точек, работающих после полуночи.
    Например в 02:00 ввод 04:00 означает сегодня 04:00, а не завтра."""
    now = now or datetime.datetime.now(TZ)
    open_h, close_h = HOURS.get(point, (9, 22))
    candidate = datetime.datetime.combine(now.date(), datetime.time(h, m), tzinfo=TZ)

    if close_h > 24:
        real_close = close_h - 24
        if now.hour < real_close:
            # Мы уже после полуночи, но ещё внутри вчерашней смены.
            close_dt = datetime.datetime.combine(now.date(), datetime.time(real_close, 0), tzinfo=TZ)
            # 00:00..закрытие — сегодня; вечернее время сегодня уже после текущего закрытия.
            candidate = datetime.datetime.combine(now.date(), datetime.time(h, m), tzinfo=TZ)
        else:
            close_dt = datetime.datetime.combine(now.date() + datetime.timedelta(days=1),
                                                 datetime.time(real_close, 0), tzinfo=TZ)
            if h < real_close:
                candidate += datetime.timedelta(days=1)
        valid_open = (candidate.hour >= open_h) or (candidate.hour < real_close)
        return candidate, close_dt, valid_open

    # Обычная точка без перехода через полночь — сохраняем прежнюю логику.
    if candidate < now:
        candidate += datetime.timedelta(days=1)
    close_dt = datetime.datetime.combine(candidate.date(), datetime.time(close_h % 24, 0), tzinfo=TZ)
    if close_h == 24:
        close_dt = datetime.datetime.combine(candidate.date(), datetime.time(23, 59), tzinfo=TZ)
    open_dt = datetime.datetime.combine(candidate.date(), datetime.time(open_h, 0), tzinfo=TZ)
    valid_open = open_dt <= candidate <= close_dt
    return candidate, close_dt, valid_open


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TZ)
        return dt.astimezone(TZ)
    except Exception:
        return None


def order_scheduled_datetime(order, reference_ts=None):
    """Возвращает конкретный datetime выбранного времени.
    Новые заказы хранят pickup_at; fallback нужен для незавершённых заказов
    старой версии, переживших Deploy.
    """
    pickup_at = _parse_iso_datetime(order.get("pickup_at"))
    if pickup_at:
        return pickup_at

    label = str(order.get("pickup_time") or "").strip()
    if not label or label.startswith("Побыстрее") or order.get("delivery_asap"):
        return None

    raw = label[:5]
    if len(raw) != 5 or raw[2] != ":":
        return None
    try:
        h, m = map(int, raw.split(":"))
        if not (0 <= h <= 23 and 0 <= m <= 59):
            return None
    except Exception:
        return None

    if reference_ts:
        try:
            ref = datetime.datetime.fromtimestamp(float(reference_ts), TZ)
        except Exception:
            ref = datetime.datetime.now(TZ)
    else:
        ref = datetime.datetime.now(TZ)

    if order.get("order_type") == "delivery":
        if order.get("is_preorder") and order.get("preorder_service_date"):
            candidate, _, _ = resolve_preorder_datetime(order, h, m, ref)
            return candidate
        day = ref.date() + (datetime.timedelta(days=1) if "завтра" in label.lower() else datetime.timedelta())
        return datetime.datetime.combine(day, datetime.time(h, m), tzinfo=TZ)

    point = order.get("point")
    if point:
        candidate, _, _ = resolve_pickup_datetime(point, h, m, ref)
        return candidate
    return datetime.datetime.combine(ref.date(), datetime.time(h, m), tzinfo=TZ)


def order_time_issue(order, now=None, reference_ts=None):
    """Проверяет, не устарело ли выбранное клиентом время.
    Для «Побыстрее» время считается от момента фактического принятия заказа.
    """
    now = now or datetime.datetime.now(TZ)
    if order.get("order_type") == "delivery" and order.get("delivery_asap"):
        hh = now.hour + now.minute / 60
        if DELIVERY_CLOSE_H <= 24:
            open_now = DELIVERY_OPEN_H <= hh <= DELIVERY_CLOSE_H
        else:
            open_now = hh >= DELIVERY_OPEN_H or hh <= (DELIVERY_CLOSE_H - 24)
        if not open_now:
            return "приём заказов на доставку уже завершён (после 01:00)"
        return None
    scheduled = order_scheduled_datetime(order, reference_ts=reference_ts)
    if scheduled is None:
        return None
    if scheduled <= now:
        return f"выбранное время {scheduled.strftime('%H:%M')} уже прошло"
    return None


def refresh_asap_label(order):
    """Обновляет ETA «Побыстрее» непосредственно перед отправкой заказа на кухню."""
    if order.get("order_type") == "delivery" and order.get("delivery_asap"):
        asap_min, _ = get_asap_minutes()
        order["pickup_time"] = f"Побыстрее (~{asap_min} мин)"
        order.pop("pickup_at", None)


def ensure_order_time_current(vk, user_id, state):
    """Не даёт отправить неоплаченный заказ на уже прошедшее время.
    Возвращает клиента к свежему выбору времени, сохраняя корзину.
    """
    order = state.get("order", {})
    refresh_asap_label(order)
    issue = order_time_issue(order)
    if not issue:
        return True

    order["pickup_time"] = None
    order.pop("pickup_at", None)
    order["delivery_asap"] = False

    if order.get("order_type") == "delivery":
        if is_delivery_open():
            order["is_preorder"] = False
            state["step"] = "delivery_time_mode"
            asap_min, load_note = get_asap_minutes()
            send(vk, user_id,
                 f"⚠️ {issue.capitalize()}. Выбери новое время доставки 👇\n\n"
                 f"{load_note}⚡ Побыстрее — примерно {asap_min} минут\n"
                 "🕒 К определённому времени — не раньше чем через 90 минут",
                 kb_delivery_time())
        else:
            order["is_preorder"] = True
            order["preorder_service_date"] = datetime.datetime.now(TZ).date().isoformat()
            state["step"] = "delivery_time_custom"
            send(vk, user_id,
                 f"⚠️ {issue.capitalize()}. Сейчас доставка закрыта, поэтому выбери новое время предзаказа.\n\n"
                 "Доступное окно: с 12:00 до 01:00. Напиши время в формате ЧЧ:ММ:", None)
    else:
        point = order.get("point")
        if not point or not is_point_open(point):
            state["step"] = "choose_point"
            send(vk, user_id,
                 f"⚠️ {issue.capitalize()}. Точка сейчас закрыта — выбери открытую точку самовывоза 👇",
                 kb_points())
        else:
            min_min = int(order.get("min_minutes", 15))
            slots = get_time_slots(point, min_minutes=min_min)
            state["step"] = "choose_time"
            send(vk, user_id,
                 f"⚠️ {issue.capitalize()}. Выбери новое время самовывоза 👇",
                 kb_time(slots))
    persist_user_states()
    return False


def paid_order_blockers(order, reference_ts=None):
    """Причины, по которым уже оплаченную ссылку нельзя отправлять на кухню.
    После оплаты цены не пересчитываем: проверяем только доступность и время.
    """
    reasons = []
    stopped = stopped_for_order(order)

    if order.get("order_type") == "delivery":
        d = order.get("delivery") or {}
        if d.get("zone") not in DELIVERY_ZONES:
            reasons.append("зона доставки больше недоступна")

    for item in order.get("items", []):
        name = item.get("name")
        cat = item.get("cat")
        if not name or cat not in MENU or name not in MENU.get(cat, {}):
            reasons.append(f"позиция «{name or 'неизвестная'}» больше недоступна")
            continue
        if name in stopped:
            reasons.append(f"«{name}» сейчас в стоп-листе")
        sauce = item.get("sauce")
        if sauce and sauce != "Без соуса" and sauce in stopped:
            reasons.append(f"соус «{sauce}» сейчас в стоп-листе")
        for extra in item.get("extras", []):
            if extra in EXTRAS:
                if extra in stopped:
                    reasons.append(f"добавка «{extra}» сейчас в стоп-листе")
            elif isinstance(extra, str) and extra.startswith("Соус "):
                sauce_name = extra[5:]
                if sauce_name not in SAUCES:
                    reasons.append(f"доп. соус «{sauce_name}» больше недоступен")
                elif sauce_name in stopped:
                    reasons.append(f"доп. соус «{sauce_name}» сейчас в стоп-листе")
            else:
                reasons.append(f"добавка «{extra}» больше недоступна")

    issue = order_time_issue(order, reference_ts=reference_ts)
    if issue:
        reasons.append(issue)

    # Убираем повторы, сохраняя порядок.
    return list(dict.fromkeys(reasons))


def get_time_slots(point, min_minutes=15):
    """Ближайшие слоты самовывоза с учётом открытия/закрытия точки.
    Даже восстановленный после Restart заказ не получит слот раньше открытия.
    Если точка ещё закрыта, первый слот = открытие + время приготовления.
    """
    slots = []
    open_h, close_h = HOURS.get(point, (9, 22))
    now = datetime.datetime.now(TZ)
    prep = datetime.timedelta(minutes=min_minutes)

    if close_h <= 24:
        today_open = datetime.datetime.combine(now.date(), datetime.time(open_h, 0), tzinfo=TZ)
        today_close = datetime.datetime.combine(now.date(), datetime.time(close_h % 24, 0), tzinfo=TZ)
        if close_h == 24:
            today_close = datetime.datetime.combine(now.date(), datetime.time(23, 59), tzinfo=TZ)

        if now < today_open:
            open_dt, end_dt = today_open, today_close
            start_time = open_dt + prep
        elif now <= today_close:
            open_dt, end_dt = today_open, today_close
            start_time = now + prep
        else:
            next_date = now.date() + datetime.timedelta(days=1)
            open_dt = datetime.datetime.combine(next_date, datetime.time(open_h, 0), tzinfo=TZ)
            end_dt = datetime.datetime.combine(next_date, datetime.time(close_h % 24, 0), tzinfo=TZ)
            if close_h == 24:
                end_dt = datetime.datetime.combine(next_date, datetime.time(23, 59), tzinfo=TZ)
            start_time = open_dt + prep
    else:
        real_close = close_h - 24
        today_open = datetime.datetime.combine(now.date(), datetime.time(open_h, 0), tzinfo=TZ)
        today_close = datetime.datetime.combine(now.date() + datetime.timedelta(days=1), datetime.time(real_close, 0), tzinfo=TZ)

        if now.hour < real_close:
            # После полуночи, но ещё идёт смена, начавшаяся вчера.
            open_dt = datetime.datetime.combine(now.date() - datetime.timedelta(days=1), datetime.time(open_h, 0), tzinfo=TZ)
            end_dt = datetime.datetime.combine(now.date(), datetime.time(real_close, 0), tzinfo=TZ)
            start_time = now + prep
        elif now < today_open:
            # Между ночным закрытием и сегодняшним открытием.
            open_dt, end_dt = today_open, today_close
            start_time = open_dt + prep
        else:
            # Внутри сегодняшней смены.
            open_dt, end_dt = today_open, today_close
            start_time = now + prep

    start_time = start_time.replace(second=0, microsecond=0)
    if start_time > end_dt:
        return []

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


def kb_skip():
    kb = VkKeyboard(one_time=True)
    kb.add_button("Пропустить", color=VkKeyboardColor.SECONDARY)
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
    kb.add_button("⚡ Побыстрее", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🕒 К определённому времени", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_delivery_custom_late():
    """Когда до закрытия уже меньше 90 минут — даём понятный путь назад к «Побыстрее»."""
    kb = VkKeyboard(one_time=True)
    kb.add_button("⚡ Побыстрее", color=VkKeyboardColor.POSITIVE)
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


def kb_categories(order_type="pickup", stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = set(stopped or set())
    for cat, items in MENU.items():
        if order_type == "delivery" and cat in DELIVERY_HIDDEN_CATS:
            continue
        # Если в категории вообще ничего нельзя заказать — не показываем пустой экран.
        if items and all(name in stopped for name in items.keys()):
            continue
        kb.add_button(cat, color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✏️ Корзина", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_categories_for_order(order):
    return kb_categories(order.get("order_type", "pickup"), stopped_for_order(order))


def kb_items(category, stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = stopped or set()
    items = [(n, p) for n, p in MENU[category].items() if n not in stopped]

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


def kb_sauces(stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = set(stopped or set())
    sauces = [s for s in SAUCES if s == "Без соуса" or s not in stopped]
    for i, sauce in enumerate(sauces):
        kb.add_button(sauce, color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1 and i != len(sauces) - 1:
            kb.add_line()
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_extras_page1(stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = set(stopped or set())
    extras = [(e, p) for e, p in list(EXTRAS.items())[:8] if e not in stopped]
    for i, (extra, price) in enumerate(extras):
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1:
            kb.add_line()
    if any(s not in stopped for s in SAUCES[:-1]):
        kb.add_button("🥫 Доп соус +42₽", color=VkKeyboardColor.SECONDARY)
    kb.add_button("➡️ Далее", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("✅ Готово", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_extra_sauces(stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = set(stopped or set())
    for sauce in SAUCES[:-1]:  # все кроме "Без соуса"
        if sauce in stopped:
            continue
        kb.add_button(f"{sauce} +42₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("◀️ Назад к добавкам", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_extras_page2(stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = set(stopped or set())
    extras = [(e, p) for e, p in list(EXTRAS.items())[8:] if e not in stopped]
    for i, (extra, price) in enumerate(extras):
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        if i % 2 == 1:
            kb.add_line()
    kb.add_button("✅ Готово", color=VkKeyboardColor.POSITIVE)
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


CART_PAGE_SIZE = 5


def cart_page_count(order):
    total = len(order.get("items", []))
    return max(1, (total + CART_PAGE_SIZE - 1) // CART_PAGE_SIZE)


def normalize_cart_page(order, page):
    pages = cart_page_count(order)
    try:
        page = int(page)
    except Exception:
        page = 0
    return max(0, min(page, pages - 1))


def kb_cart(order, page=0):
    """Редактирование корзины с пагинацией, чтобы не превышать лимит строк VK."""
    kb = VkKeyboard(one_time=True)
    items = order.get("items", [])
    page = normalize_cart_page(order, page)
    start = page * CART_PAGE_SIZE
    end = min(start + CART_PAGE_SIZE, len(items))

    for idx in range(start, end):
        visible_idx = idx + 1
        kb.add_button(f"➖ {visible_idx}", color=VkKeyboardColor.SECONDARY)
        kb.add_button(f"➕ {visible_idx}", color=VkKeyboardColor.SECONDARY)
        kb.add_button(f"🗑 {visible_idx}", color=VkKeyboardColor.NEGATIVE)
        kb.add_line()

    pages = cart_page_count(order)
    if pages > 1:
        if page > 0:
            kb.add_button("⬅️ Корзина", color=VkKeyboardColor.SECONDARY)
        if page < pages - 1:
            kb.add_button("Корзина ➡️", color=VkKeyboardColor.SECONDARY)
        kb.add_line()

    kb.add_button("➕ Добавить ещё", color=VkKeyboardColor.SECONDARY)
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def cart_keyboard_for_state(state):
    order = state.get("order", {})
    state["cart_page"] = normalize_cart_page(order, state.get("cart_page", 0))
    return kb_cart(order, state["cart_page"])


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


def kb_upsell_extra(stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = set(stopped or set())
    if "Сыр тертый" not in stopped:
        kb.add_button(f"🧀 Сыр +{EXTRAS['Сыр тертый']}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    if "Бекон" not in stopped:
        kb.add_button(f"🥓 Бекон +{EXTRAS['Бекон']}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("➡️ Без добавки", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_upsell_drink(stopped=None):
    kb = VkKeyboard(one_time=True)
    stopped = stopped or set()
    for name in ["Морс Фруктовый", "Морс Облепиховый", "Морс Малина-мята"]:
        if name in stopped:
            continue
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


def kb_refund_confirm(order_num):
    kb = VkKeyboard(one_time=False, inline=True)
    kb.add_button(f"↩️ Возврат и отмена #{order_num}", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button(f"🚫 Не отменять #{order_num}", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_courier(order_num):
    # INLINE-кнопки на карточке курьера — привязаны к конкретному заказу.
    kb = VkKeyboard(one_time=False, inline=True)
    kb.add_button(f"✅ Доставлен #{order_num}", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button(f"⏱ Задержка +15 мин #{order_num}", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def _build_courier_card_text(order_num, info):
    """Текст карточки доставки для курьера."""
    addr = info.get("address") or "—"
    phone = info.get("phone") or "—"
    comment = info.get("comment") or ""
    zone = info.get("zone") or ""
    total = info.get("total")
    pay = info.get("payment_status") or "—"
    if "Оплачено онлайн" in pay:
        payment_block = "✅ Оплачено онлайн\n💰 С клиента: 0₽"
    elif "Картой курьеру" in pay:
        payment_block = (f"💳 Оплата: Картой курьеру\n💰 Получить: {total}₽"
                         if total is not None else "💳 Оплата: Картой курьеру")
    else:
        payment_block = ((f"💰 Получить: {total}₽\n" if total is not None else "")
                         + f"💵 Оплата: {pay}")

    return (
        f"🚗 Доставка #{order_num}\n\n"
        f"🏠 Адрес: {addr}\n"
        + (f"🗺 Зона: {zone}\n" if zone else "")
        + f"📱 Телефон: {phone}\n"
        + (f"💬 Комментарий: {comment}\n" if comment else "")
        + f"\n{payment_block}"
    )


def _send_courier_card(vk, order_num, info):
    """Надёжно отправляет карточку курьеру.

    Если VK не принял сообщение сейчас, карточка остаётся в active_orders
    и notification_retry_watcher будет досылать её каждые 30 секунд.
    """
    txt = _build_courier_card_text(order_num, info)
    # Сначала сохраняем, потом пытаемся отправить — так Deploy/Restart не потеряет карточку.
    info["pending_courier_notification"] = txt
    info["courier_notify_failures"] = 0
    _save_json(ACTIVE_ORDERS_FILE, active_orders)

    ok = send(vk, COURIER_VK_ID, txt, kb_courier(order_num))
    print(f"COURIER SEND #{order_num} -> {COURIER_VK_ID}: {'OK' if ok else 'FAIL'}")
    if ok:
        info.pop("pending_courier_notification", None)
        info["courier_notified_at"] = time.time()
        _save_json(ACTIVE_ORDERS_FILE, active_orders)
        return True

    send_emergency_alert(vk,
        "Не удалось отправить карточку курьеру",
        f"Заказ #{order_num}, курьер VK ID {COURIER_VK_ID}. Карточка сохранена и будет досылаться автоматически. "
        "Проверь, что курьер разрешил сообщения сообщества и хотя бы один раз написал в группу.")
    return False


def _send_client_status(vk, order_num, info, client_text):
    """Надёжно отправляет новый статус клиенту с фоновой досылкой при сбое VK."""
    client_id = int(info["user_id"])
    # Сохраняем ДО отправки, чтобы статус не потерялся при Restart между save/send.
    info["pending_client_status_notification"] = client_text
    info["status_notify_failures"] = 0
    _save_json(ACTIVE_ORDERS_FILE, active_orders)

    ok = send(vk, client_id, client_text, kb_main())
    print(f"STATUS SEND #{order_num} -> client {client_id}: {'OK' if ok else 'FAIL'}")
    if ok:
        info.pop("pending_client_status_notification", None)
        info["last_client_status_notified_at"] = time.time()
        _save_json(ACTIVE_ORDERS_FILE, active_orders)
        return True

    send_emergency_alert(vk,
        "Не удалось отправить статус клиенту",
        f"Заказ #{order_num}, клиент VK ID {client_id}. Статус сохранён и будет досылаться автоматически.")
    return False


def kb_staff_menu():
    kb = VkKeyboard(one_time=True)
    kb.add_button("🔥 Загрузка кухни", color=VkKeyboardColor.PRIMARY)
    kb.add_line()
    kb.add_button("⛔ Стоп-лист", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_staff_load():
    kb = VkKeyboard(one_time=True)
    kb.add_button("🟢 45 минут", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🟡 60 минут", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🔴 75 минут", color=VkKeyboardColor.NEGATIVE)
    kb.add_line()
    kb.add_button("◀️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_staff_stop_point():
    kb = VkKeyboard(one_time=True)
    for p in STOP_POINTS:
        kb.add_button(f"📍 {p}", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("◀️ Назад", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()


def kb_staff_stop_items():
    kb = VkKeyboard(one_time=True)
    kb.add_button("◀️ К точкам", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
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


def kb_delivery_comment():
    kb = VkKeyboard(one_time=True)
    kb.add_button("➖ Без комментария", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🏠 В начало", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()



def kb_choose_payment(order):
    kb = VkKeyboard(one_time=True)
    kb.add_button("💳 Оплатить онлайн", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    if order.get("order_type") == "delivery":
        kb.add_button("💳 Картой курьеру", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
        kb.add_button("💵 Наличными", color=VkKeyboardColor.SECONDARY)
    else:
        kb.add_button("💵 Оплата при получении", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("◀️ В корзину", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def make_random_id():
    # VK использует random_id для дедупликации сообщений. Один ID создаём на
    # логическое сообщение и повторяем с тем же ID во всех retry-попытках.
    return (uuid.uuid4().int % 2147483646) + 1

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
    random_id = make_random_id()
    params = {"user_id": user_id, "message": text, "random_id": random_id}
    if keyboard:
        params["keyboard"] = keyboard
    # Три попытки — сеть до ВК иногда обрывается. random_id остаётся тем же,
    # поэтому успешная первая отправка не продублируется при потерянном ответе API.
    for attempt in range(3):
        try:
            vk.messages.send(**params)
            return True
        except Exception as e:
            print(f"Не отправилось (попытка {attempt + 1}): {e}")
            time.sleep(2)
    return False


def send_emergency_alert(vk, title, details=""):
    """Аварийные уведомления отправляются ТОЛЬКО на VK ID 72534661."""
    try:
        msg = f"🚨 BOT ERROR\n\n{title}"
        if details:
            msg += f"\n\n{details}"
        # Не вызываем алерт из send(), чтобы не получить рекурсию при проблемах VK.
        send(vk, ERROR_ALERT_VK_ID, msg)
    except Exception as e:
        print(f"Не удалось отправить аварийный алерт: {e}")


def mark_payment_abandoned(vk, payment_id, order, reason):
    """Выключает онлайн-платёж в логике бота при смене способа оплаты.
    Одностадийный pending-платёж ЮKassa нельзя гарантированно закрыть API,
    поэтому оставляем его под наблюдением и автоматически вернём деньги,
    если старая ссылка будет оплачена позже.
    Возвращает: abandoned / canceled / paid / missing.
    """
    if not payment_id:
        return "missing"
    info = pending_payments.get(payment_id)
    kind = (info or {}).get("shop_kind") or shop_kind_for_order(order)
    shop_id, secret_key = credentials_for_shop_kind(kind)
    status = check_payment(payment_id, shop_id=shop_id, secret_key=secret_key)

    if status == "succeeded":
        # Деньги уже пришли: другой способ оплаты не включаем — вызывающий код
        # финализирует заказ как оплаченный онлайн.
        return "paid"

    if status == "canceled":
        pending_payments.pop(payment_id, None)
        payment_records[payment_id] = {
            "status": "canceled", "order_num": str(order.get("order_num", "")),
            "updated_at": time.time(),
        }
        save_pending_payments(); save_payment_records()
        return "canceled"

    if info is None:
        # Восстановим минимум данных, чтобы отслеживать возможную позднюю оплату.
        info = {
            "user_id": None, "order": copy.deepcopy(order),
            "order_num": order.get("order_num"), "cart": format_cart(order),
            "total": get_total(order), "created_at": time.time(),
            "last_checked_at": 0, "shop_kind": kind,
        }
        pending_payments[payment_id] = info

    # Пробуем cancel и для pending, и для waiting_for_capture. Для обычной
    # одностадийной оплаты ЮKassa может отклонить cancel у pending — тогда
    # сработает безопасный fallback: наблюдение + автоматический возврат.
    if status in ("pending", "waiting_for_capture"):
        cancel_key = info.get("cancel_idempotence_key") or str(uuid.uuid4())
        info["cancel_idempotence_key"] = cancel_key
        save_pending_payments()
        code, data = cancel_payment(payment_id, shop_id, secret_key, cancel_key)
        if code and 200 <= code < 300 and data.get("status") == "canceled":
            pending_payments.pop(payment_id, None)
            payment_records[payment_id] = {
                "status": "canceled_by_switch", "order_num": str(order.get("order_num", "")),
                "updated_at": time.time(),
            }
            save_pending_payments(); save_payment_records()
            return "canceled"

    info["abandoned"] = True
    info["abandoned_reason"] = reason
    info["abandoned_at"] = time.time()
    info["late_refund_idempotence_key"] = info.get("late_refund_idempotence_key") or str(uuid.uuid4())
    payment_records[payment_id] = {
        "status": "abandoned", "order_num": str(order.get("order_num", "")),
        "updated_at": time.time(),
    }
    save_pending_payments(); save_payment_records()
    return "abandoned"


def abandon_waiting_payment_before_new_flow(vk, user_id, state, user_name="Клиент", first_name="Друг", reason="Новый сценарий"):
    """Если клиент выходит из wait_payment, старая ссылка больше не должна
    неожиданно оживить старый заказ. Помечаем её abandoned до reset_state().
    Если деньги уже успели пройти — сначала безопасно обрабатываем оплату.
    """
    if state.get("step") != "wait_payment":
        return "none"
    order = state.get("order", {})
    payment_id = order.get("payment_id")
    if not payment_id:
        return "none"

    result = mark_payment_abandoned(vk, payment_id, order, reason)
    if result == "paid":
        fallback = {
            "user_id": user_id, "user_name": user_name, "first_name": first_name,
            "order": copy.deepcopy(order), "order_num": order.get("order_num"),
            "cart": format_cart(order), "total": get_total(order),
            "created_at": time.time(), "shop_kind": shop_kind_for_order(order),
        }
        paid_result = finalize_paid_payment(vk, payment_id, fallback)
        if paid_result in ("finalized", "already"):
            send(vk, user_id,
                 f"✅ Оплата заказа #{order.get('order_num')} уже успела пройти. Старый заказ обработан как оплаченный онлайн.")
        elif paid_result == "refunded":
            # finalize_paid_payment уже объяснил клиенту причину и возврат.
            pass
        elif paid_result in ("refund_retry", "refund_failed"):
            send(vk, user_id,
                 "⚠️ Оплата старого заказа уже прошла, но заказ не отправлен на кухню. Мы проверяем возврат; уведомление сотруднику уже отправлено.")
        return paid_result
    return result


def ensure_order_available(vk, user_id, state):
    """Финальная проверка меню, текущих цен, зоны и стоп-листа перед оплатой."""
    order = state["order"]
    missing, invalid_zone = refresh_order_prices(order)

    if invalid_zone:
        state["step"] = "delivery_zone"
        send(vk, user_id,
             "⚠️ Сохранённая зона доставки больше недоступна. Выбери актуальную зону 👇",
             kb_delivery_zones())
        return False

    if missing:
        state["step"] = "cart_edit"
        send(vk, user_id,
             "⚠️ Некоторые позиции больше отсутствуют в меню:\n• " + "\n• ".join(missing) +
             "\n\nУдали их из корзины или выбери замену 👇",
             cart_keyboard_for_state(state))
        return False

    stopped = stopped_for_order(order)
    blocked = []
    for item in order.get("items", []):
        if item.get("name") in stopped and item.get("name") not in blocked:
            blocked.append(item.get("name"))
        sauce = item.get("sauce")
        if sauce and sauce != "Без соуса" and sauce in stopped and sauce not in blocked:
            blocked.append(f"Соус {sauce}")
        for extra in item.get("extras", []):
            if extra in EXTRAS and extra in stopped and extra not in blocked:
                blocked.append(extra)
            elif isinstance(extra, str) and extra.startswith("Соус "):
                sauce_name = extra[5:]
                if sauce_name in stopped and f"Соус {sauce_name}" not in blocked:
                    blocked.append(f"Соус {sauce_name}")
    if blocked:
        state["step"] = "cart_edit"
        send(vk, user_id,
             "😔 Пока ты оформлял заказ, некоторые позиции, добавки или соусы попали в стоп-лист:\n• " + "\n• ".join(blocked) +
             "\n\nИзмени заказ и выбери доступный вариант 👇",
             cart_keyboard_for_state(state))
        return False
    return True


def start_checkout(vk, user_id, state):
    """Общий переход к оформлению: проверки и выбор времени.
    Возвращает True если перешли дальше."""
    order = state["order"]
    if not order["items"]:
        send(vk, user_id, "Корзина пуста! Добавь хотя бы одну позицию 😊", kb_categories_for_order(state["order"]))
        return

    if not ensure_order_available(vk, user_id, state):
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

    # Ненавязчивый upsell: предлагаем только напиток (морс).
    if not state.get("upsell_drink_shown") and not any(i.get("cat") == "Напитки" for i in order["items"]):
        state["upsell_drink_shown"] = True
        available_stopped = stopped_for_order(order)
        offered = [n for n in ["Морс Фруктовый", "Морс Облепиховый", "Морс Малина-мята"] if n not in available_stopped]
        if offered:
            state["step"] = "upsell_drink"
            send(vk, user_id, "🥤 Добавить морс к заказу? Один клик — и он в корзине.", kb_upsell_drink(available_stopped))
            return

    # Доставка — проверка минимальной суммы (только товары, без доставки)
    if order.get("order_type") == "delivery":
        goods = get_goods_total(order)
        if goods < DELIVERY_MIN_ORDER:
            need = DELIVERY_MIN_ORDER - goods
            send(vk, user_id,
                f"🛒 Минимальная сумма заказа на доставку — {DELIVERY_MIN_ORDER}₽.\n"
                f"Сейчас на {goods}₽, добавь ещё на {need}₽ 😊",
                kb_categories_for_order(state["order"]))
            return
        # Доставка открыта — доступны «Побыстрее» и «К определённому времени».
        # Доставка закрыта — оформляем предзаказ только на рабочее окно 12:00–01:00.
        if is_delivery_open():
            state["order"]["is_preorder"] = False
            asap_min, load_note = get_asap_minutes()
            state["step"] = "delivery_time_mode"
            send(vk, user_id,
                "🕒 Когда доставить?\n\n"
                f"{load_note}"
                f"⚡ Побыстрее — примерно {asap_min} минут\n" +
                ("🕒 К определённому времени — не раньше чем через 90 минут" if DELIVERY_TIME_LIMITS_ENABLED else "🕒 К определённому времени — любое время для теста"),
                kb_delivery_time())
        else:
            state["order"]["is_preorder"] = True
            # Фиксируем конкретную смену предзаказа: прошедшее время не переносим молча на завтра.
            state["order"]["preorder_service_date"] = datetime.datetime.now(TZ).date().isoformat()
            state["step"] = "delivery_time_custom"
            send(vk, user_id,
                "🌙 Сейчас доставка не работает (она с 12:00 до 01:00),\n"
                "но можно оформить предзаказ 🚗\n\n"
                "🕒 Напиши время доставки в формате ЧЧ:ММ.\n"
                "Доступное время: с 12:00 до 01:00",
                None)
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
    """Финализирует заказ идемпотентно: сначала /data, потом кассир и клиент."""
    is_delivery = order.get("order_type") == "delivery"
    manager_id = MANAGERS.get(order.get("point"), ADMIN_VK_ID)

    if is_delivery:
        d = order["delivery"]
        addr = f"{d['street']}, д. {d['house']}"
        if d.get("apt"):
            addr += f", кв. {d['apt']}"
        if d.get("entrance"):
            addr += f", подъезд {d['entrance']}"
        if d.get("floor"):
            addr += f", этаж {d['floor']}"
        notif = (
            f"🚗 НОВЫЙ ЗАКАЗ #{order_num} — ДОСТАВКА\n\n"
            f"👤 {user_name} (vk.com/id{user_id})\n"
            f"📱 {order.get('phone', 'не указан')}\n"
            f"🚗 Зона: {d['zone']}\n"
            f"🏠 Адрес: {addr}\n"
            + (f"🔔 Домофон: {d['domofon']}\n" if d.get("domofon") else "")
            + f"🍳 Готовит: {order['point']}\n"
            f"🕒 Время: {order['pickup_time']}\n"
            + (f"💬 Комментарий: {order['comment']}\n" if order.get('comment') else "")
            + f"\n{cart}\n\n"
            f"💰 Итого с доставкой: {total}₽\n"
            f"💳 {payment_status}"
        )
        client_msg = (
            f"🎉 Заказ #{order_num} принят!\n\n"
            f"🚗 Доставка: {d['zone']}\n"
            f"🏠 {addr}\n"
            f"🕒 {order['pickup_time']}\n"
            f"💰 Итого с доставкой: {total}₽\n"
            f"💳 {payment_status}\n\n"
            f"Спасибо, {first_name}! Уже готовим 🌯🔥"
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
        client_msg = (
            f"🎉 Заказ #{order_num} принят!\n\n"
            f"📍 {order['point']}\n"
            f"⏰ Будет готов к {order['pickup_time']}\n"
            f"💰 Сумма: {total}₽\n"
            f"💳 {payment_status}\n\n"
            f"Ждём тебя, {first_name}! До встречи 🌯🔥"
        )

    # 1) Сначала пишем заказ на Railway Volume. Даже если ВК упадёт, заказ не потеряется.
    entry = save_active_order(order_num, user_id, order, manager_id, total=total,
                              payment_status=payment_status,
                              manager_notification=notif,
                              client_notification=client_msg)
    save_customer(user_id, order)

    # 2) Затем уведомляем кассира. При сбое notification_retry_watcher повторит.
    if not entry.get("manager_notified"):
        if send(vk, manager_id, notif, kb_manager_status(order_num, is_delivery)):
            entry["manager_notified"] = True
            _save_json(ACTIVE_ORDERS_FILE, active_orders)
        else:
            send_emergency_alert(vk,
                "Заказ не отправился кассиру после 3 попыток",
                f"Заказ #{order_num}, менеджер VK ID {manager_id}. Заказ сохранён в /data и будет досылаться автоматически.")

    # 3) И клиента. Тоже не дублируем при повторной финализации.
    if not entry.get("client_notified"):
        if send(vk, user_id, client_msg, kb_final()):
            entry["client_notified"] = True
            _save_json(ACTIVE_ORDERS_FILE, active_orders)
        else:
            send_emergency_alert(vk,
                "Клиенту не отправилось подтверждение заказа",
                f"Заказ #{order_num}, клиент VK ID {user_id}. Заказ на кухне сохранён.")
    return True



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
    if step in ("delivery_entrance", "delivery_floor"):
        return kb_skip()
    if step == "delivery_domofon":
        return kb_domofon()
    if step in ("confirm_saved_address", "repeat_confirm_address"):
        return kb_saved_address()
    if step == "confirm_saved_phone":
        return kb_saved_phone()
    if step == "repeat_order_confirm":
        return kb_repeat_order()
    if step == "cart_edit":
        return cart_keyboard_for_state(state)
    if step == "upsell_extra":
        return kb_upsell_extra(stopped_for_order(order))
    if step == "upsell_drink":
        return kb_upsell_drink(stopped_for_order(order))
    if step == "delivery_comment":
        return kb_delivery_comment()
    if step == "choose_category":
        return kb_categories_for_order(order)
    if step == "choose_item":
        cat = state.get("current_category")
        return kb_items(cat, stopped_for_order(order)) if cat in MENU else kb_categories_for_order(order)
    if step == "choose_sauce_for_item":
        return kb_sauces(stopped_for_order(order))
    if step == "choose_extras_for_item":
        return kb_extras_page2(stopped_for_order(order)) if state.get("extras_page", 1) == 2 else kb_extras_page1(stopped_for_order(order))
    if step == "delivery_time_mode":
        return kb_delivery_time()
    if step == "choose_time":
        point = order.get("point")
        if point:
            return kb_time(get_time_slots(point, min_minutes=order.get("min_minutes", 15)))
    if step == "confirm":
        return kb_confirm()
    if step == "choose_payment":
        return kb_choose_payment(order)
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
                # Если заказ завершён/пользователь вернулся в главное меню — не напоминаем.
                # Пульт сотрудника тоже не считаем незавершённым клиентским заказом.
                current_step = state.get("step", "main")
                if current_step == "main" or str(current_step).startswith("staff_"):
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
    """Фоновая проверка оплат. После 40 минут проверяем реже, но до 24 часов.
    Заброшенную старую ссылку не финализируем: если её оплатили после смены
    способа оплаты, автоматически создаём возврат.
    """
    while True:
        time.sleep(15)
        try:
            now = time.time()
            changed = False
            for pid in list(pending_payments.keys()):
                info = pending_payments.get(pid)
                if not info:
                    continue

                age = now - float(info.get("created_at", now))
                if age > PENDING_PAYMENT_TTL:
                    pending_payments.pop(pid, None)
                    changed = True
                    print(f"Платёж {pid} старше 24 часов — убран из ожидания")
                    continue

                last_checked = float(info.get("last_checked_at", 0))
                if age >= PAYMENT_SLOW_AFTER and now - last_checked < PAYMENT_SLOW_INTERVAL:
                    continue

                kind = info.get("shop_kind") or shop_kind_for_order(info.get("order", {}))
                shop_id, secret_key = credentials_for_shop_kind(kind)
                status = check_payment(pid, shop_id=shop_id, secret_key=secret_key)
                info["last_checked_at"] = now
                changed = True

                if status == "succeeded":
                    if info.get("abandoned"):
                        # Клиент уже выбрал другой способ оплаты, но старая ссылка была оплачена.
                        refund_key = info.get("late_refund_idempotence_key") or str(uuid.uuid4())
                        info["late_refund_idempotence_key"] = refund_key
                        save_pending_payments()
                        refund = create_refund(
                            pid, int(info.get("total", 0)), info.get("order_num", ""),
                            shop_id=shop_id, secret_key=secret_key,
                            idempotence_key=refund_key,
                            description=f"Автовозврат старой оплаты заказа #{info.get('order_num', '')}"
                        )
                        if refund and refund.get("status") in ("succeeded", "pending"):
                            payment_records[pid] = {
                                "status": "late_payment_refunded",
                                "order_num": str(info.get("order_num", "")),
                                "refund_id": refund.get("id"),
                                "refund_status": refund.get("status"),
                                "shop_kind": kind,
                                "user_id": info.get("user_id"),
                                "total": info.get("total"),
                                "updated_at": now,
                            }
                            client_id = info.get("user_id")
                            if client_id:
                                send(vk, int(client_id),
                                     f"ℹ️ Старая ссылка оплаты заказа #{info.get('order_num')} была оплачена уже после смены способа оплаты. "
                                     f"Мы автоматически оформили возврат {info.get('total')}₽.",
                                     kb_main())
                            send_emergency_alert(vk,
                                "Оплачена старая ссылка — создан автовозврат",
                                f"Заказ #{info.get('order_num')}, сумма {info.get('total')}₽, refund {refund.get('id')} ({refund.get('status')}).")
                            pending_payments.pop(pid, None)
                            changed = True
                        else:
                            # Если ЮKassa вернула финальный canceled по возврату, бесконечно
                            # повторять тот же idempotence key бессмысленно — нужна ручная проверка.
                            if refund and refund.get("status") == "canceled":
                                payment_records[pid] = {
                                    "status": "late_refund_failed",
                                    "order_num": str(info.get("order_num", "")),
                                    "refund_id": refund.get("id"),
                                    "refund_status": "canceled",
                                    "updated_at": now,
                                }
                                pending_payments.pop(pid, None)
                                changed = True
                            if not info.get("late_refund_fail_alerted"):
                                info["late_refund_fail_alerted"] = True
                                send_emergency_alert(vk,
                                    "КРИТИЧНО: не удалось вернуть позднюю оплату",
                                    f"Заказ #{info.get('order_num')}, payment_id {pid}, сумма {info.get('total')}₽. Нужна ручная проверка ЮKassa.")
                    else:
                        result = finalize_paid_payment(vk, pid)
                        print(f"Платёж {pid}: succeeded -> {result}")
                        if result in ("finalized", "already", "refunded", "refund_failed", "refund_retry"):
                            client_id = info.get("user_id")
                            if client_id:
                                if result == "refund_retry" and not info.get("refund_retry_client_alerted"):
                                    info["refund_retry_client_alerted"] = True
                                    pending_payments[pid] = info
                                    save_pending_payments()
                                    send(vk, int(client_id),
                                         f"⚠️ Оплата заказа #{info.get('order_num')} получена, но заказ не отправлен на кухню. "
                                         "Бот оформляет возврат, сотрудник уже уведомлён.", kb_main())
                                reset_state(int(client_id))

                elif status == "canceled":
                    pending_payments.pop(pid, None)
                    payment_records[pid] = {
                        "status": "canceled",
                        "order_num": str(info.get("order_num", "")),
                        "updated_at": now,
                    }
                    save_payment_records()
                    changed = True

                    # Если клиент уже переключился на другой способ — ничего не дёргаем.
                    if not info.get("abandoned"):
                        client_id = info.get("user_id")
                        if client_id:
                            st = get_state(int(client_id))
                            # Возвращаем заказ на выбор способа оплаты, сохраняя корзину.
                            st["order"] = copy.deepcopy(info.get("order", st.get("order", {})))
                            st["order"].pop("payment_id", None)
                            st["order"].pop("payment_create_key", None)
                            st["step"] = "choose_payment"
                            register_user_activity(st)
                            persist_user_states()
                            send(vk, int(client_id),
                                 f"⚠️ Онлайн-оплата заказа #{info.get('order_num')} была отменена или не прошла. "
                                 "Заказ на кухню не отправлен. Выбери способ оплаты ещё раз 👇",
                                 kb_choose_payment(st["order"]))

            if changed:
                save_pending_payments()
        except Exception as e:
            print(f"Ошибка в payment_watcher: {e}")
            send_emergency_alert(vk, "Ошибка payment_watcher", str(e)[:500])



def refund_watcher(vk):
    """Следит за возвратами со статусом pending до финального succeeded/canceled."""
    while True:
        time.sleep(60)
        try:
            active_changed = False
            records_changed = False

            # Возвраты по отменённым активным заказам.
            for order_num, info in list(active_orders.items()):
                if info.get("refund_status") != "pending" or not info.get("refund_id"):
                    continue
                kind = info.get("shop_kind") or ("sovetskaya" if info.get("point") == "Советская 2/10" else "default")
                shop_id, secret_key = credentials_for_shop_kind(kind)
                status, _ = check_refund(info["refund_id"], shop_id, secret_key)
                if status == "succeeded":
                    info["refund_status"] = "succeeded"
                    info["refund_updated_at"] = time.time()
                    active_changed = True
                    client_id = info.get("user_id")
                    if client_id:
                        send(vk, int(client_id),
                             f"✅ Возврат по заказу #{order_num} завершён. Сумма {info.get('total')}₽ возвращена через ЮKassa.",
                             kb_main())
                    manager_id = int(info.get("manager_id", ADMIN_VK_ID))
                    send(vk, manager_id, f"✅ Возврат по заказу #{order_num} завершён ЮKassa.")
                elif status == "canceled":
                    info["refund_status"] = "canceled"
                    info["refund_updated_at"] = time.time()
                    active_changed = True
                    send_emergency_alert(vk,
                        "КРИТИЧНО: возврат отменён ЮKassa",
                        f"Заказ #{order_num}, refund_id {info.get('refund_id')}, сумма {info.get('total')}₽. Нужна ручная проверка.")

            # Автовозвраты старых ссылок/недоступных оплаченных заказов.
            for pid, rec in list(payment_records.items()):
                if rec.get("refund_status") != "pending" or not rec.get("refund_id"):
                    continue
                kind = rec.get("shop_kind", "default")
                shop_id, secret_key = credentials_for_shop_kind(kind)
                status, _ = check_refund(rec["refund_id"], shop_id, secret_key)
                if status == "succeeded":
                    rec["refund_status"] = "succeeded"
                    if rec.get("status") == "unfulfillable_refund_pending":
                        rec["status"] = "unfulfillable_refunded"
                    rec["updated_at"] = time.time()
                    records_changed = True
                    client_id = rec.get("user_id")
                    if client_id:
                        send(vk, int(client_id),
                             f"✅ Возврат по заказу #{rec.get('order_num')} завершён. Сумма {rec.get('total')}₽ возвращена через ЮKassa.",
                             kb_main())
                elif status == "canceled":
                    rec["refund_status"] = "canceled"
                    rec["updated_at"] = time.time()
                    records_changed = True
                    send_emergency_alert(vk,
                        "КРИТИЧНО: автоматический возврат отменён ЮKassa",
                        f"Заказ #{rec.get('order_num')}, payment_id {pid}, refund_id {rec.get('refund_id')}, сумма {rec.get('total')}₽.")

            if active_changed:
                _save_json(ACTIVE_ORDERS_FILE, active_orders)
            if records_changed:
                save_payment_records()
        except Exception as e:
            print(f"Ошибка refund_watcher: {e}")
            send_emergency_alert(vk, "Ошибка refund_watcher", str(e)[:500])


def main():
    install_shutdown_handlers()
    cleanup_payment_records()
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()

    # Веб-панель кассира: заказы, статусы, стоп-лист, загрузка, клиенты.
    try:
        dashboard.start_dashboard({
            "vk": vk,
            "active_orders": active_orders,
            "customers": customers,
            "stop_list": stop_list,
            "kitchen_load": kitchen_load,
            "save_json": _save_json,
            "ACTIVE_ORDERS_FILE": ACTIVE_ORDERS_FILE,
            "STOP_LIST_FILE": STOP_LIST_FILE,
            "KITCHEN_LOAD_FILE": KITCHEN_LOAD_FILE,
            "send": send,
            "toggle_stop": toggle_stop,
            "save_kitchen_load": save_kitchen_load,
            "load_kitchen_load": load_kitchen_load,
            "MENU": MENU,
            "ALL_ITEMS": ALL_ITEMS,
            "STOP_POINTS": STOP_POINTS,
            "MANAGERS": MANAGERS,
            "kb_main": kb_main,
            "DELIVERY_POINT": DELIVERY_POINT,
        })
    except Exception as e:
        print(f"Панель не запустилась: {e}")

    threading.Thread(target=payment_watcher, args=(vk,), daemon=True).start()
    threading.Thread(target=refund_watcher, args=(vk,), daemon=True).start()
    threading.Thread(target=abandoned_order_watcher, args=(vk,), daemon=True).start()
    threading.Thread(target=active_orders_cleaner, daemon=True).start()
    threading.Thread(target=notification_retry_watcher, args=(vk,), daemon=True).start()
    threading.Thread(target=runtime_state_saver, daemon=True).start()
    print("Бот запущен!")
    print(f"Данные клиентов: {CUSTOMERS_FILE}")
    print(f"Активные заказы: {ACTIVE_ORDERS_FILE}")

    for event in safe_listen(vk_session):
        if event.type != VkEventType.MESSAGE_NEW:
            continue
        _preview = (getattr(event, "text", "") or "").strip()
        _is_status_cmd = any(_preview.startswith(p) for p in STATUS_PREFIXES)
        # Клиенты пишут как входящие (to_me). Кнопки статуса менеджер нажимает
        # с аккаунта самого бота — VK помечает их как исходящие (from_me),
        # поэтому статус-команды пропускаем через фильтр и в этом случае.
        if not ((event.to_me and not event.from_me) or (event.from_me and _is_status_cmd)):
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
        # Любое реальное действие клиента запускает новый цикл 5/30 минут.
        register_user_activity(state)
        step = state["step"]

        try:
            user_info = vk.users.get(user_ids=user_id)
            user_name = f"{user_info[0]['first_name']} {user_info[0]['last_name']}"
            first_name = user_info[0]['first_name']
        except:
            user_name = "Клиент"
            first_name = "Друг"

        # СТАТУСЫ ЗАКАЗА — строгая последовательность, старые inline-кнопки безопасны.
        if any(text.startswith(prefix) for prefix in STATUS_PREFIXES):
            try:
                order_num = text.split("#")[-1].strip()
                info = active_orders.get(order_num)
                print(f"STATUS order_num={order_num!r} found={'yes' if info else 'NO'} from_me={getattr(event, 'from_me', None)}")
                if not info:
                    send(vk, user_id, "⚠️ Этот заказ не найден или уже старше 24 часов.")
                    continue

                # from_me = владелец аккаунта-бота нажал кнопку сам → это менеджер.
                is_manager = bool(getattr(event, "from_me", False)) or (user_id == int(info.get("manager_id", -1)))
                is_courier = (user_id == COURIER_VK_ID)
                if not (is_manager or is_courier):
                    send(vk, user_id, "⚠️ У тебя нет доступа к статусу этого заказа.")
                    continue

                current = info.get("status", "Принят")
                is_delivery_order = info.get("order_type") == "delivery"
                final_statuses = {"✅ Готов", "✅ Доставлен", "❌ Отменён"}

                # Менеджер отказался от отмены оплаченного заказа.
                if text.startswith("🚫 Не отменять"):
                    if not is_manager:
                        send(vk, user_id, "⚠️ Только менеджер может отменять заказ.")
                    else:
                        send(vk, user_id, f"✅ Заказ #{order_num} оставлен без изменений: {current}.")
                    continue

                # Подтверждённая отмена онлайн-оплаченного заказа = полный возврат ЮKassa.
                if text.startswith("↩️ Возврат и отмена"):
                    if not is_manager:
                        send(vk, user_id, "⚠️ Только менеджер может оформить возврат.")
                        continue
                    if current in final_statuses:
                        send(vk, user_id, f"⚠️ Заказ #{order_num} уже завершён: {current}.")
                        continue
                    payment_id = info.get("payment_id")
                    if not payment_id:
                        send(vk, user_id, "⚠️ У заказа не найден payment_id. Автовозврат невозможен.")
                        send_emergency_alert(vk, "Нет payment_id для возврата", f"Заказ #{order_num}.")
                        continue
                    refund_key = info.get("refund_idempotence_key") or str(uuid.uuid4())
                    info["refund_idempotence_key"] = refund_key
                    _save_json(ACTIVE_ORDERS_FILE, active_orders)
                    kind = info.get("shop_kind") or ("sovetskaya" if info.get("point") == "Советская 2/10" else "default")
                    shop_id, secret_key = credentials_for_shop_kind(kind)
                    pay_state = check_payment(payment_id, shop_id=shop_id, secret_key=secret_key)
                    if pay_state != "succeeded":
                        send(vk, user_id, f"⚠️ ЮKassa показывает статус платежа: {pay_state or 'не удалось получить'}. Возврат не создан.")
                        send_emergency_alert(vk, "Не удалось подтвердить оплату перед возвратом",
                                             f"Заказ #{order_num}, payment_id {payment_id}, status={pay_state}.")
                        continue
                    refund = create_refund(payment_id, int(info.get("total", 0)), order_num,
                                           shop_id=shop_id, secret_key=secret_key,
                                           idempotence_key=refund_key)
                    if not refund or refund.get("status") not in ("succeeded", "pending"):
                        send(vk, user_id, "⚠️ Возврат создать не удалось. Заказ НЕ отменён. Проверь ЮKassa.")
                        send_emergency_alert(vk, "КРИТИЧНО: возврат ЮKassa не создан",
                                             f"Заказ #{order_num}, payment_id {payment_id}, сумма {info.get('total')}₽.")
                        continue
                    info["status"] = "❌ Отменён"
                    info["status_updated_at"] = time.time()
                    info["refund_id"] = refund.get("id")
                    info["refund_status"] = refund.get("status")
                    _save_json(ACTIVE_ORDERS_FILE, active_orders)
                    client_id = int(info["user_id"])
                    if refund.get("status") == "succeeded":
                        refund_text = f"✅ Возврат {info.get('total')}₽ оформлен."
                    else:
                        refund_text = f"↩️ Возврат {info.get('total')}₽ создан и обрабатывается ЮKassa."
                    send(vk, client_id,
                         f"❌ Заказ #{order_num} отменён.\n{refund_text} Деньги вернутся тем же способом оплаты.",
                         kb_main())
                    send(vk, user_id, f"✅ Заказ #{order_num} отменён. {refund_text}")
                    continue

                if current in final_statuses:
                    send(vk, user_id, f"⚠️ Заказ #{order_num} уже завершён: {current}. Статус менять нельзя.")
                    continue

                # Курьер может только сообщить задержку или завершить уже выехавшую доставку.
                if is_courier and not is_manager:
                    if text.startswith("⏱ Задержка"):
                        if current != "🚗 Курьер выехал":
                            send(vk, user_id, "⚠️ Задержку можно отметить только после статуса «Курьер выехал».")
                            continue
                        client_id = int(info["user_id"])
                        send(vk, client_id,
                             f"⏱ Небольшая задержка по заказу #{order_num} — курьер будет примерно на 15 минут позже. Спасибо за ожидание! 🙏",
                             kb_main())
                        mgr = int(info.get("manager_id", ADMIN_VK_ID))
                        if mgr != user_id:
                            send(vk, mgr, f"⏱ Курьер сообщил о задержке ~15 мин по заказу #{order_num}.")
                        send(vk, user_id, f"✅ Клиенту отправлено уведомление о задержке по #{order_num}.")
                        continue
                    if not text.startswith("✅ Доставлен") or current != "🚗 Курьер выехал":
                        send(vk, user_id, "⚠️ Курьер может поставить «Доставлен» только после статуса «Курьер выехал».")
                        continue

                requested = None
                client_text = None
                if text.startswith("🔥"):
                    requested = "🔥 Готовим"
                    client_text = f"🔥 Заказ #{order_num} уже готовим! Скоро будет готов 🌯"
                elif text.startswith("✅ Готов"):
                    requested = "✅ Готов"
                    client_text = f"✅ Заказ #{order_num} готов! Можно забирать 🌯🔥"
                elif text.startswith("🚗"):
                    requested = "🚗 Курьер выехал"
                    client_text = f"🚗 Заказ #{order_num} передан курьеру. Уже едет к тебе!"
                elif text.startswith("✅ Доставлен"):
                    requested = "✅ Доставлен"
                    client_text = f"✅ Заказ #{order_num} доставлен. Приятного аппетита! 🌯🔥"
                elif text.startswith("❌"):
                    if "Оплачено онлайн" in str(info.get("payment_status", "")):
                        if not is_manager:
                            send(vk, user_id, "⚠️ Только менеджер может отменить оплаченный заказ.")
                            continue
                        send(vk, user_id,
                             f"⚠️ Заказ #{order_num} оплачен онлайн на {info.get('total')}₽.\n\n"
                             "При отмене бот создаст полный возврат через ЮKassa. Подтвердить?",
                             kb_refund_confirm(order_num))
                        continue
                    requested = "❌ Отменён"
                    client_text = f"❌ Заказ #{order_num} отменён. Если это неожиданно — напиши нам, пожалуйста."
                elif text.startswith("⏱ Задержка"):
                    send(vk, user_id, "⚠️ Задержку отмечает курьер после выезда.")
                    continue

                if not is_delivery_order:
                    allowed = {
                        "Принят": {"🔥 Готовим", "❌ Отменён"},
                        "🔥 Готовим": {"✅ Готов", "❌ Отменён"},
                    }
                else:
                    allowed = {
                        "Принят": {"🔥 Готовим", "❌ Отменён"},
                        "🔥 Готовим": {"🚗 Курьер выехал", "❌ Отменён"},
                        "🚗 Курьер выехал": {"✅ Доставлен", "❌ Отменён"},
                    }

                if requested not in allowed.get(current, set()):
                    next_txt = {
                        "Принят": "сначала нажми «🔥 Готовим»",
                        "🔥 Готовим": "для доставки нажми «🚗 Курьер выехал», для самовывоза — «✅ Готов»",
                        "🚗 Курьер выехал": "следующий статус — «✅ Доставлен»",
                    }.get(current, "проверь текущий статус")
                    send(vk, user_id, f"⚠️ Нельзя изменить {current} → {requested}. {next_txt}.")
                    continue

                info["status"] = requested
                info["status_updated_at"] = time.time()
                _save_json(ACTIVE_ORDERS_FILE, active_orders)

                # Статус клиенту больше не теряется при кратком сбое VK:
                # сначала сохраняем уведомление в /data, затем отправляем, при ошибке watcher досылает.
                client_ok = _send_client_status(vk, order_num, info, client_text)
                send(vk, user_id,
                     f"Статус заказа #{order_num}: {requested}"
                     + ("\n✅ Клиент уведомлён." if client_ok else "\n⚠️ Клиенту пока не доставлено — бот будет повторять автоматически."))

                if is_courier:
                    mgr = int(info.get("manager_id", ADMIN_VK_ID))
                    if mgr != user_id:
                        send(vk, mgr, f"Курьер обновил заказ #{order_num}: {requested}")

            except Exception as e:
                print(f"Ошибка статуса: {e}")
                send_emergency_alert(vk, "Ошибка изменения статуса заказа", str(e)[:500])
            continue

        # ===== ПУЛЬТ СОТРУДНИКА (загрузка кухни + стоп-лист) =====
        if user_id in STAFF:
            st = state.get("step", "")
            if text.strip().lower() in ("пульт", "/пульт", "админ"):
                state["step"] = "staff_menu"
                send(vk, user_id,
                     f"🛠 Пульт сотрудника\n\n"
                     f"🔥 Загрузка сейчас: {load_kitchen_load()} мин\n\n"
                     f"Что настроить?",
                     kb_staff_menu())
                continue
            if st == "staff_menu":
                if text == "🔥 Загрузка кухни":
                    state["step"] = "staff_load"
                    send(vk, user_id,
                         f"🔥 Текущая оценка «Побыстрее»: {load_kitchen_load()} мин\n\n"
                         f"Выбери уровень загрузки — он влияет на время доставки "
                         f"(берётся максимум с авто-часами-пик 12–14 и 17–19):",
                         kb_staff_load())
                    continue
                if text == "⛔ Стоп-лист":
                    state["step"] = "staff_stop_point"
                    send(vk, user_id, "⛔ Стоп-лист. Выбери точку:", kb_staff_stop_point())
                    continue
                if text in ("🏠 В начало", "◀️ Назад"):
                    reset_state(user_id)
                    send(vk, user_id, "Вышел из пульта.", kb_main())
                    continue
                send(vk, user_id, "Выбери раздел 👇", kb_staff_menu())
                continue
            if st == "staff_load":
                mp = {"🟢 45 минут": 45, "🟡 60 минут": 60, "🔴 75 минут": 75}
                if text in mp:
                    save_kitchen_load(mp[text])
                    state["step"] = "staff_menu"
                    send(vk, user_id,
                         f"✅ Загрузка обновлена: {mp[text]} мин.\n"
                         f"«Побыстрее» на доставке = максимум из этого и часов пик.",
                         kb_staff_menu())
                    continue
                if text in ("◀️ Назад", "🏠 В начало"):
                    state["step"] = "staff_menu"
                    send(vk, user_id, "🛠 Пульт сотрудника", kb_staff_menu())
                    continue
                send(vk, user_id, "Выбери уровень 👇", kb_staff_load())
                continue
            if st == "staff_stop_point":
                if text == "◀️ Назад":
                    state["step"] = "staff_menu"
                    send(vk, user_id, "🛠 Пульт сотрудника", kb_staff_menu())
                    continue
                point = None
                for p in STOP_POINTS:
                    if p in text:
                        point = p
                        break
                if point:
                    state["staff_point"] = point
                    state["step"] = "staff_stop_items"
                    send(vk, user_id, render_stop_list(point), kb_staff_stop_items())
                    continue
                if text == "🏠 В начало":
                    reset_state(user_id)
                    send(vk, user_id, "Вышел из пульта.", kb_main())
                    continue
                send(vk, user_id, "Выбери точку 👇", kb_staff_stop_point())
                continue
            if st == "staff_stop_items":
                point = state.get("staff_point", STOP_POINTS[0])
                if text == "◀️ К точкам":
                    state["step"] = "staff_stop_point"
                    send(vk, user_id, "⛔ Стоп-лист. Выбери точку:", kb_staff_stop_point())
                    continue
                if text in ("🏠 В начало", "◀️ Назад"):
                    reset_state(user_id)
                    send(vk, user_id, "Вышел из пульта.", kb_main())
                    continue
                parts = text.replace(",", " ").split()
                nums = [p for p in parts if p.isdigit()]
                if not nums:
                    send(vk, user_id,
                         "Отправь номер позиции (например: 3) или несколько через пробел (3 7 12).",
                         kb_staff_stop_items())
                    continue
                results = []
                for p in nums:
                    r = toggle_stop(point, int(p))
                    if r:
                        results.append(r)
                head = ("\n".join(results) + "\n\n") if results else ""
                send(vk, user_id, head + render_stop_list(point), kb_staff_stop_items())
                continue

        # СТАРТ
        if text.lower() in ["начать", "start", "/start", "сначала", "❌ отмена",
                            "🔄 начать заново", "◀️ назад",
                            "🏠 вернуться в начало", "🏠 в начало"]:
            abandon_waiting_payment_before_new_flow(vk, user_id, state, user_name, first_name, "Пользователь вернулся в начало")
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
            abandon_waiting_payment_before_new_flow(vk, user_id, state, user_name, first_name, "Пользователь начал повтор заказа")
            last = get_customer(user_id).get("last_order")
            if not last or not last.get("items"):
                send(vk, user_id, "Пока нет прошлого заказа, который можно повторить 😊", kb_main())
                continue
            reset_state(user_id)
            state = get_state(user_id)
            import copy
            state["order"] = copy.deepcopy(last)
            state["order"]["pickup_time"] = None
            missing, invalid_zone = refresh_order_prices(state["order"])
            if missing:
                send(vk, user_id,
                     "⚠️ В прошлом заказе есть позиции, которых больше нет в меню:\n• " + "\n• ".join(missing) +
                     "\n\nНажми «Изменить заказ», чтобы убрать или заменить их.")
            if invalid_zone:
                state["order"]["delivery"] = None
            state["step"] = "repeat_order_confirm"
            send(vk, user_id, "🔁 Твой прошлый заказ по актуальным ценам:\n\n" + format_cart(state["order"]) + "\n\nПовторяем?", kb_repeat_order())
            continue

        if text in ["🛒 Корзина", "✏️ Корзина"]:
            if state.get("step") == "wait_payment":
                leave_result = abandon_waiting_payment_before_new_flow(
                    vk, user_id, state, user_name, first_name, "Пользователь вернулся к корзине"
                )
                if leave_result in ("finalized", "already"):
                    reset_state(user_id)
                    send(vk, user_id, "✅ Оплата уже прошла — заказ отправлен на кухню. Корзину оплаченного заказа изменить нельзя.", kb_main())
                    continue
                if leave_result in ("refunded", "refund_retry", "refund_failed"):
                    reset_state(user_id)
                    continue
                # Старая ссылка выключена в логике бота; текущую корзину можно редактировать.
                state["order"].pop("payment_id", None)
                state["order"].pop("payment_create_key", None)
            if not state["order"].get("items"):
                send(vk, user_id, "🛒 Корзина пока пуста.", kb_main())
            else:
                state["step"] = "cart_edit"
                state["cart_page"] = 0
                send(vk, user_id, "🛒 Твой заказ:\n\n" + format_cart(state["order"]) + "\n\nМожно изменить количество или удалить позицию 👇", cart_keyboard_for_state(state))
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
            abandon_waiting_payment_before_new_flow(vk, user_id, state, user_name, first_name, "Пользователь начал новый самовывоз")
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
            abandon_waiting_payment_before_new_flow(vk, user_id, state, user_name, first_name, "Пользователь начал новую доставку")
            reset_state(user_id)
            state = get_state(user_id)
            state["order"]["order_type"] = "delivery"
            state["order"]["point"] = DELIVERY_POINT
            # Доставку можно оформить в любое время. Если сейчас нерабочие часы —
            # предупреждаем, что это будет предзаказ только на рабочее окно 12:00–01:00.
            preorder_note = ""
            if not is_delivery_open():
                preorder_note = (
                    "🌙 Сейчас доставка не работает (она с 12:00 до 01:00).\n"
                    "Можно оформить предзаказ — доставим ко времени с 12:00 до 01:00 🚗\n\n"
                )
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
                send(vk, user_id, f"{preorder_note}🚗 Доставить снова сюда?\n\n🏠 {addr}\nЗона: {saved_d.get('zone')}", kb_saved_address())
            else:
                state["step"] = "delivery_zone"
                zones_txt = "\n".join(f"• {z} — {p}₽" for z, p in DELIVERY_ZONES.items())
                send(vk, user_id,
                    f"{preorder_note}🚗 Доставка по зонам:\n{zones_txt}\n\n"
                    f"Минимальная сумма заказа — {DELIVERY_MIN_ORDER}₽\n\n"
                    f"Куда везём? Выбери зону 👇",
                    kb_delivery_zones())
            continue

        # ПОВТОР: подтверждение прошлого заказа
        if step == "repeat_order_confirm":
            if text == "✅ Повторить этот заказ":
                o = state["order"]
                # «Повторить» должен быть одинаково быстрым для самовывоза и доставки:
                # upsell не показываем ни в одном из двух сценариев.
                state["upsell_extras_shown"] = True
                state["upsell_drink_shown"] = True
                if o.get("order_type") == "delivery":
                    # Доставку можно повторить в любое время: в нерабочие часы
                    # это станет предзаказом (обрабатывается на шаге выбора времени).

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
                    start_checkout(vk, user_id, state)
            elif text == "✏️ Изменить заказ":
                # Состав можно менять, но адрес доставки после изменений
                # обязательно уточним ещё раз перед оформлением.
                if state["order"].get("order_type") == "delivery":
                    state["repeat_needs_address_confirm"] = True
                state["step"] = "cart_edit"
                state["cart_page"] = 0
                send(vk, user_id, "🛒 Измени заказ 👇\n\n" + format_cart(state["order"]), cart_keyboard_for_state(state))
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
            if text == "⬅️ Корзина":
                state["cart_page"] = max(0, state.get("cart_page", 0) - 1)
                send(vk, user_id, "🛒 Твой заказ:\n\n" + format_cart(state["order"]), cart_keyboard_for_state(state))
                continue
            if text == "Корзина ➡️":
                state["cart_page"] = min(cart_page_count(state["order"]) - 1, state.get("cart_page", 0) + 1)
                send(vk, user_id, "🛒 Твой заказ:\n\n" + format_cart(state["order"]), cart_keyboard_for_state(state))
                continue
            if text == "➕ Добавить ещё":
                state["step"] = "choose_category"
                send(vk, user_id, "Выбери категорию:", kb_categories_for_order(state["order"]))
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
                        state["cart_page"] = normalize_cart_page(state["order"], state.get("cart_page", 0))
                        send(vk, user_id, "🛒 Корзина обновлена:\n\n" + format_cart(state["order"]), cart_keyboard_for_state(state))
                    else:
                        state["step"] = "choose_category"
                        send(vk, user_id, "Корзина пуста. Добавим что-нибудь? 👇", kb_categories_for_order(state["order"]))
                continue
            send(vk, user_id, "Выбери действие с корзиной 👇", cart_keyboard_for_state(state))
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
            stopped_now = stopped_for_order(state["order"])
            if extra is not None and extra in stopped_now:
                send(vk, user_id, f"😔 «{extra}» только что закончилась. Выбери другой вариант 👇", kb_upsell_extra(stopped_now))
            elif extra is not None and isinstance(idx, int) and 0 <= idx < len(state["order"]["items"]):
                if extra not in state["order"]["items"][idx].setdefault("extras", []):
                    state["order"]["items"][idx]["extras"].append(extra)
                send(vk, user_id, f"✅ {extra} добавлен.")
                start_checkout(vk, user_id, state)
            else:
                send(vk, user_id, "Выбери добавку или нажми «Без добавки» 👇", kb_upsell_extra(stopped_now))
            continue

        # UPSELL НАПИТКА
        if step == "upsell_drink":
            if text == "➡️ Без напитка":
                start_checkout(vk, user_id, state)
                continue
            stopped_now = stopped_for_order(state["order"])
            selected = None
            for name, price in MENU["Напитки"].items():
                if name not in stopped_now and text == f"🥤 {name} +{price}₽":
                    selected = (name, price)
                    break
            if selected:
                name, price = selected
                state["order"]["items"].append({"name": name, "price": price, "sauce": None, "extras": [], "cat": "Напитки", "qty": 1})
                send(vk, user_id, f"✅ {name} добавлен в заказ.")
                start_checkout(vk, user_id, state)
            else:
                send(vk, user_id, "Выбери доступный напиток или нажми «Без напитка» 👇", kb_upsell_drink(stopped_now))
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
                send(vk, user_id, "✅ Адрес подставили. Теперь собери заказ 👇", kb_categories_for_order(state["order"]))
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
                    kb_categories_for_order(state["order"]))
            else:
                state["order"]["delivery"]["apt"] = text.strip()
                state["step"] = "delivery_entrance"
                send(vk, user_id, "🚪 Номер подъезда?\n\nНапиши номер или нажми «Пропустить»:", kb_skip())
            continue

        # ДОСТАВКА: подъезд
        if step == "delivery_entrance":
            state["order"]["delivery"]["entrance"] = None if text == "Пропустить" else text.strip()
            state["step"] = "delivery_floor"
            send(vk, user_id, "🏢 Этаж?\n\nНапиши этаж или нажми «Пропустить»:", kb_skip())
            continue

        # ДОСТАВКА: этаж
        if step == "delivery_floor":
            state["order"]["delivery"]["floor"] = None if text == "Пропустить" else text.strip()
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
            if d.get("entrance"):
                addr += f", подъезд {d['entrance']}"
            if d.get("floor"):
                addr += f", этаж {d['floor']}"
            send(vk, user_id,
                f"✅ Адрес: {addr}\n"
                f"🔔 Домофон: {d['domofon']}\n"
                f"🚗 Зона: {d['zone']} (+{d['price']}₽)\n\n"
                f"Теперь собери заказ. Минимум на доставку — {DELIVERY_MIN_ORDER}₽.\n\n"
                f"Выбери категорию:",
                kb_categories_for_order(state["order"]))
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
                        kb_categories_for_order(state["order"]))
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
            # На доставке скрыты только кофе и чай; морсы и газировка доступны
            if matched_cat and otype == "delivery" and matched_cat in DELIVERY_HIDDEN_CATS:
                send(vk, user_id, "🚗 Кофе и чай на доставке пока недоступны 😔\nМорсы и газировка есть в разделе «Напитки» 👇", kb_categories_for_order(state["order"]))
                continue
            if matched_cat:
                state["step"] = "choose_item"
                state["current_category"] = matched_cat
                send(vk, user_id, f"Выбери позицию из «{matched_cat}»:", kb_items(matched_cat, stopped_for_order(state["order"])))
            else:
                send(vk, user_id, "Выбери категорию 👇", kb_categories_for_order(state["order"]))
            continue

        # ВЫБОР БЛЮДА
        if step == "choose_item":
            if text == "◀️ К категориям":
                state["step"] = "choose_category"
                cart = format_cart(state["order"])
                send(vk, user_id, f"🛒 Корзина:\n{cart}\n\nВыбери категорию:", kb_categories_for_order(state["order"]))
                continue

            cat = state.get("current_category", "")
            stopped = stopped_for_order(state["order"])
            found = False
            for name, price in MENU.get(cat, {}).items():
                # Точное совпадение: кнопка содержит имя + цену вида "Название 350₽"
                expected = f"{name} {price}₽"
                if text == expected or text == name:
                    found = True
                    if name in stopped:
                        send(vk, user_id, f"😔 «{name}» сейчас закончилась. Выбери другое 👇", kb_items(cat, stopped))
                        break
                    state["current_item"] = {"name": name, "price": price, "sauce": None, "extras": [], "cat": cat, "qty": 1}

                    if cat in SAUCE_CATS:
                        state["step"] = "choose_sauce_for_item"
                        send(vk, user_id,
                            f"✅ {name}\n\nВыбери соус:",
                            kb_sauces(stopped_for_order(state["order"])))
                    elif cat in EXTRAS_CATS:
                        state["step"] = "choose_extras_for_item"
                        state["extras_page"] = 1
                        send(vk, user_id,
                            f"✅ {name}\n\nХочешь добавки?",
                            kb_extras_page1(stopped_for_order(state["order"])))
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
                send(vk, user_id, "Выбери позицию из списка 👇", kb_items(cat, stopped_for_order(state["order"])))
            continue

        # СОУС ДЛЯ ПОЗИЦИИ
        if step == "choose_sauce_for_item":
            stopped = stopped_for_order(state["order"])
            if text in SAUCES and (text == "Без соуса" or text not in stopped):
                state["current_item"]["sauce"] = text
                state["step"] = "choose_extras_for_item"
                state["extras_page"] = 1
                send(vk, user_id, "➕ Хочешь добавки?", kb_extras_page1(stopped))
            elif text in stopped:
                send(vk, user_id, f"😔 Соус «{text}» сейчас закончился. Выбери другой 👇", kb_sauces(stopped))
            else:
                send(vk, user_id, "Выбери соус 👇", kb_sauces(stopped))
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
                    send(vk, user_id, "➕ Ещё добавки:", kb_extras_page2(stopped_for_order(state["order"])))
                else:
                    _finish_item()
                continue

            if text == "✅ Готово":
                _finish_item()
                continue

            if text == "🥫 Доп соус +42₽":
                stopped = stopped_for_order(state["order"])
                if any(s not in stopped for s in SAUCES[:-1]):
                    send(vk, user_id, "Выбери соус:", kb_extra_sauces(stopped))
                else:
                    send(vk, user_id, "😔 Дополнительные соусы сейчас в стоп-листе.", kb_extras_page1(stopped))
                continue

            if text == "◀️ Назад к добавкам":
                state["extras_page"] = 1
                send(vk, user_id, "➕ Добавки:", kb_extras_page1(stopped_for_order(state["order"])))
                continue

            # Доп соус выбран
            for sauce in SAUCES[:-1]:
                if f"{sauce} +42₽" == text:
                    stopped = stopped_for_order(state["order"])
                    if sauce in stopped:
                        send(vk, user_id, f"😔 Соус «{sauce}» сейчас закончился. Выбери другой 👇", kb_extra_sauces(stopped))
                        break
                    extra_name = f"Соус {sauce}"
                    if extra_name not in state["current_item"]["extras"]:
                        state["current_item"]["extras"].append(extra_name)
                    send(vk, user_id,
                        f"✅ {extra_name} добавлен\nЕщё добавки или «Готово»:",
                        kb_extras_page1(stopped))
                    break
            else:
                matched_extra = None
                for extra_name in EXTRAS.keys():
                    if extra_name in text:
                        matched_extra = extra_name
                        break
                if matched_extra:
                    stopped_now = stopped_for_order(state["order"])
                    if matched_extra in stopped_now:
                        send(vk, user_id,
                            f"😔 «{matched_extra}» сейчас закончилась. Выбери другую добавку 👇",
                            kb_extras_page1(stopped_now))
                    else:
                        if matched_extra not in state["current_item"]["extras"]:
                            state["current_item"]["extras"].append(matched_extra)
                        send(vk, user_id,
                            f"✅ {matched_extra} добавлен\nЕщё добавки или «Готово»:",
                            kb_extras_page1(stopped_now))
                else:
                    send(vk, user_id, "Выбери добавку 👇", kb_extras_page1(stopped_for_order(state["order"])))
            continue

        # ПОСЛЕ ДОБАВЛЕНИЯ ПОЗИЦИИ
        if step == "choose_category" and text == "➕ Добавить ещё":
            send(vk, user_id, "Выбери категорию:", kb_categories_for_order(state["order"]))
            continue

        if step == "choose_category" and text == "🛒 Оформить заказ":
            start_checkout(vk, user_id, state)
            continue

        # ДОСТАВКА: режим времени
        if step == "delivery_time_mode":
            if text.startswith("⚡ Побыстрее"):
                asap_min, _ = get_asap_minutes()
                state["order"]["pickup_time"] = f"Побыстрее (~{asap_min} мин)"
                state["order"]["delivery_asap"] = True
                state["order"].pop("pickup_at", None)
                request_phone(vk, user_id, state)
                continue
            if text == "🕒 К определённому времени":
                state["step"] = "delivery_time_custom"
                now = datetime.datetime.now(TZ)
                min_dt = now + datetime.timedelta(minutes=90)
                earliest = min_dt.strftime("%H:%M")
                close_dt = current_delivery_close_datetime(now)
                no_slot_this_shift = bool(DELIVERY_TIME_LIMITS_ENABLED and close_dt and min_dt > close_dt)
                state["delivery_custom_next_window_only"] = no_slot_this_shift
                if no_slot_this_shift:
                    next_open = next_delivery_open_datetime(now)
                    hint = (
                        "🕒 До закрытия текущей доставки осталось меньше 90 минут, "
                        "поэтому к определённому времени в этой смене уже не успеваем.\n\n"
                        f"Ближайшее время для заказа «к определённому времени» — {day_word(next_open, now)} с {next_open.strftime('%H:%M')}.\n"
                        "Если нужно оформить заказ сейчас — нажми «⚡ Побыстрее»."
                    )
                    send(vk, user_id, hint, kb_delivery_custom_late())
                else:
                    hint = "🕒 Напиши желаемое время в формате ЧЧ:ММ"
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
            is_preorder = state["order"].get("is_preorder", False)

            # Если ночью человек выбрал «к определённому времени», но до закрытия уже <90 минут,
            # он может одним нажатием вернуться к «Побыстрее».
            if text.startswith("⚡ Побыстрее") and not is_preorder:
                asap_min, _ = get_asap_minutes()
                state["order"]["pickup_time"] = f"Побыстрее (~{asap_min} мин)"
                state["order"]["delivery_asap"] = True
                state["order"].pop("pickup_at", None)
                state.pop("delivery_custom_next_window_only", None)
                request_phone(vk, user_id, state)
                continue

            if len(text) == 5 and ":" in text:
                try:
                    now = datetime.datetime.now(TZ)
                    h, m = map(int, text.split(":"))
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError

                    if is_preorder:
                        candidate, open_dt, close_dt = resolve_preorder_datetime(state["order"], h, m, now)
                        if candidate is None:
                            send(vk, user_id,
                                "⚠️ Предзаказ доступен только на рабочее время доставки: с 12:00 до 01:00.\n"
                                "Напиши время в этом окне (например 12:00):", None)
                        elif candidate <= now:
                            send(vk, user_id,
                                f"⚠️ {text} уже прошло. Предзаказ нельзя поставить на прошедшее время.\n\n"
                                f"Укажи будущее время в текущем окне доставки — до {close_dt.strftime('%H:%M')}.", None)
                        elif not (open_dt <= candidate <= close_dt):
                            send(vk, user_id,
                                "⚠️ Предзаказ доступен только на рабочее время доставки: с 12:00 до 01:00.", None)
                        else:
                            suffix = "завтра, предзаказ" if candidate.date() > now.date() else "предзаказ"
                            state["order"]["pickup_time"] = f"{text} ({suffix})"
                            state["order"]["pickup_at"] = candidate.isoformat()
                            state["order"]["delivery_asap"] = False
                            request_phone(vk, user_id, state)
                        continue

                    input_dt = datetime.datetime.combine(now.date(), datetime.time(h, m), tzinfo=TZ)
                    if input_dt < now:
                        input_dt += datetime.timedelta(days=1)

                    min_time = now + datetime.timedelta(minutes=90)
                    if DELIVERY_TIME_LIMITS_ENABLED:
                        hh = input_dt.hour + input_dt.minute / 60
                        delivery_ok = hh >= DELIVERY_OPEN_H or hh <= (DELIVERY_CLOSE_H - 24)
                    else:
                        delivery_ok = True

                    next_window_only = bool(state.get("delivery_custom_next_window_only"))
                    next_open = next_delivery_open_datetime(now) if next_window_only else None

                    if next_window_only and input_dt < next_open:
                        send(vk, user_id,
                            "⚠️ До закрытия текущей смены уже меньше 90 минут.\n"
                            f"Для заказа к определённому времени ближайшее доступное — {day_word(next_open, now)} с {next_open.strftime('%H:%M')}.\n\n"
                            "Если нужно сейчас — нажми «⚡ Побыстрее».",
                            kb_delivery_custom_late())
                    elif DELIVERY_TIME_LIMITS_ENABLED and input_dt < min_time:
                        send(vk, user_id,
                            f"⚠️ Слишком рано! Доставка не раньше чем через 90 минут "
                            f"(с {min_time.strftime('%H:%M')}). Напиши другое время:", None)
                    elif DELIVERY_TIME_LIMITS_ENABLED and not delivery_ok:
                        if current_delivery_close_datetime(now) and min_time > current_delivery_close_datetime(now):
                            nopen = next_delivery_open_datetime(now)
                            send(vk, user_id,
                                f"⚠️ В текущей смене времени уже не хватает. Ближайший заказ к определённому времени — "
                                f"{day_word(nopen, now)} с {nopen.strftime('%H:%M')}.\n"
                                "Если нужно сейчас — выбери «⚡ Побыстрее».",
                                kb_delivery_custom_late())
                        else:
                            send(vk, user_id,
                                "⚠️ Доставка работает с 12:00 до 01:00. Выбери время в этом окне:", None)
                    else:
                        label = text
                        if input_dt.date() > now.date():
                            label += " (завтра)"
                        state["order"]["pickup_time"] = label
                        state["order"]["pickup_at"] = input_dt.isoformat()
                        state["order"]["delivery_asap"] = False
                        state.pop("delivery_custom_next_window_only", None)
                        request_phone(vk, user_id, state)
                except Exception:
                    send(vk, user_id, "⚠️ Неверный формат. Напиши как 19:30:", None)
            else:
                send(vk, user_id, "⚠️ Напиши время в формате ЧЧ:ММ (например 19:30):", None)
            continue

        # ВРЕМЯ
        if step == "choose_time":
            min_min = state["order"].get("min_minutes", 15)
            slots = get_time_slots(state["order"]["point"], min_minutes=min_min)

            chosen_time = None
            chosen_dt = None

            if text in slots:
                try:
                    h, m = map(int, text.split(":"))
                    chosen_dt, _, valid_open = resolve_pickup_datetime(state["order"]["point"], h, m, datetime.datetime.now(TZ))
                    if valid_open:
                        chosen_time = text
                except Exception:
                    chosen_time = None
            elif len(text) == 5 and ":" in text:
                try:
                    now = datetime.datetime.now(TZ)
                    h, m = map(int, text.split(":"))
                    if not (0 <= h <= 23 and 0 <= m <= 59):
                        raise ValueError
                    open_h, close_h = HOURS.get(state["order"]["point"], (9, 22))
                    min_time = now + datetime.timedelta(minutes=min_min)
                    input_dt, close_dt, valid_open = resolve_pickup_datetime(
                        state["order"]["point"], h, m, now
                    )

                    if input_dt < min_time:
                        send(vk, user_id,
                            f"⚠️ Слишком рано! Минимум через {min_min} мин.\nВведи другое время:",
                            kb_time(slots))
                    elif input_dt > close_dt or not valid_open:
                        send(vk, user_id, "⚠️ Точка в это время будет закрыта.\nВыбери другое время:", kb_time(slots))
                    else:
                        chosen_time = text
                        chosen_dt = input_dt
                except:
                    send(vk, user_id, "⚠️ Неверный формат. Напиши как 14:30:", kb_time(slots))
            else:
                send(vk, user_id, "Выбери время или напиши в формате ЧЧ:ММ 👇", kb_time(slots))

            if chosen_time:
                state["order"]["pickup_time"] = chosen_time
                if chosen_dt:
                    state["order"]["pickup_at"] = chosen_dt.isoformat()
                request_phone(vk, user_id, state)
            continue

        # СОХРАНЁННЫЙ ТЕЛЕФОН
        if step == "confirm_saved_phone":
            if text == "✅ Использовать этот номер":
                phone = get_customer(user_id).get("phone")
                if phone:
                    state["order"]["phone"] = phone
                    order = state["order"]
                    if order.get("order_type") == "delivery":
                        state["step"] = "delivery_comment"
                        send(vk, user_id,
                            "💬 Добавить комментарий к заказу?\n"
                            "(пожелания, ориентир для курьера, код домофона)\n\n"
                            "Напиши его сообщением или нажми «Без комментария» 👇",
                            kb_delivery_comment())
                    else:
                        state["step"] = "confirm"
                        cart = format_cart(order)
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

                order = state["order"]
                if order.get("order_type") == "delivery":
                    state["step"] = "delivery_comment"
                    send(vk, user_id,
                        "💬 Добавить комментарий к заказу?\n"
                        "(пожелания, ориентир для курьера, код домофона)\n\n"
                        "Напиши его сообщением или нажми «Без комментария» 👇",
                        kb_delivery_comment())
                else:
                    state["step"] = "confirm"
                    cart = format_cart(order)
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

        # ДОСТАВКА: комментарий к заказу
        if step == "delivery_comment":
            order = state["order"]
            if text in ("➖ Без комментария", "Без комментария"):
                order["comment"] = ""
            else:
                order["comment"] = text.strip()[:300]
            state["step"] = "confirm"
            phone = order.get("phone", "")
            d = order["delivery"]
            addr = f"{d['street']}, д. {d['house']}" + (f", кв. {d['apt']}" if d.get('apt') else "")
            if d.get("entrance"):
                addr += f", подъезд {d['entrance']}"
            if d.get("floor"):
                addr += f", этаж {d['floor']}"
            cart = format_cart(order)
            comment_line = f"💬 Комментарий: {order['comment']}\n" if order.get("comment") else ""
            summary = (
                f"🚗 Проверь заказ:\n\n"
                f"🏠 Адрес: {addr}\n"
                f"🕒 Время: {order['pickup_time']}\n"
                f"📱 Телефон: {phone}\n"
                f"{comment_line}\n"
                f"{cart}\n\n"
                f"Всё верно? 👇"
            )
            send(vk, user_id, summary, kb_confirm())
            continue

        # ПОДТВЕРЖДЕНИЕ
        if step == "confirm":
            if text == "✅ Подтвердить":
                if not ensure_order_available(vk, user_id, state):
                    continue
                order = state["order"]
                # Новый заказ = новая попытка оплаты. Сбрасываем ключи прошлой
                # онлайн-оплаты, иначе идемпотентный ключ ЮKassa вернёт старую сумму.
                order.pop("payment_id", None)
                order.pop("payment_create_key", None)
                order_num = next_order_num()
                total = get_total(order)
                state["order"]["order_num"] = order_num
                cart = format_cart(order)

                state["step"] = "choose_payment"
                send(vk, user_id,
                    f"✅ Заказ #{order_num} собран!\n\n"
                    f"💰 Сумма: {total}₽\n\n"
                    f"Осталось выбрать способ оплаты 👇",
                    kb_choose_payment(order))

            elif text == "🔄 Начать заново":
                reset_state(user_id)
                send(vk, user_id, "Хорошо, начнём заново 😊", kb_main())
            else:
                send(vk, user_id, "Нажми «Подтвердить» или «Начать заново» 👇", kb_confirm())
            continue

        # ВЫБОР ОПЛАТЫ
        if step == "choose_payment":
            order = state["order"]
            if text == "◀️ В корзину":
                state["step"] = "cart_edit"
                state["cart_page"] = 0
                send(vk, user_id, "🛒 Твой заказ:\n\n" + format_cart(order), cart_keyboard_for_state(state))
                continue
            if not ensure_order_available(vk, user_id, state):
                continue
            if not ensure_order_time_current(vk, user_id, state):
                continue
            total = get_total(order)
            order_num = order.get("order_num", 0)
            cart = format_cart(order)

            if text == "💳 Оплатить онлайн":
                # Один idempotence key на одну попытку создания онлайн-платежа.
                # Сохраняем ДО запроса, чтобы Railway Restart/таймаут не породил дубль.
                payment_create_key = order.get("payment_create_key")
                if not payment_create_key:
                    payment_create_key = str(uuid.uuid4())
                    order["payment_create_key"] = payment_create_key
                    persist_user_states()
                if order.get("order_type") == "delivery":
                    description = f"Заказ #{order_num} Eat to End — доставка {order['delivery']['zone']}"
                else:
                    description = f"Заказ #{order_num} Eat to End — {order['point']}"
                # Выбираем ключи в зависимости от точки
                phone = order.get("phone", "")
                items = order.get("items", [])
                delivery_price = order.get("delivery", {}).get("price", 0) if order.get("order_type") == "delivery" else 0
                if order["point"] == "Советская 2/10":
                    pay_url, pay_id = create_payment(total, order_num, description,
                        phone=phone, items=items, delivery_price=delivery_price,
                        shop_id=YUKASSA_SHOP_ID_SOVETSKAYA,
                        secret_key=YUKASSA_SECRET_KEY_SOVETSKAYA,
                        idempotence_key=payment_create_key)
                else:
                    pay_url, pay_id = create_payment(total, order_num, description,
                        phone=phone, items=items, delivery_price=delivery_price,
                        idempotence_key=payment_create_key)

                if pay_url:
                    state["order"]["payment_id"] = pay_id
                    state["step"] = "wait_payment"

                    # Регистрируем платёж на Railway Volume, поэтому Deploy/Restart его не забывает.
                    pending_payments[pay_id] = {
                        "user_id": user_id,
                        "user_name": user_name,
                        "first_name": first_name,
                        "order": copy.deepcopy(order),
                        "order_num": order_num,
                        "cart": cart,
                        "total": total,
                        "created_at": time.time(),
                        "last_checked_at": 0,
                        "shop_kind": shop_kind_for_order(order),
                        "create_idempotence_key": payment_create_key,
                    }
                    save_pending_payments()

                    send(vk, user_id,
                        f"💳 Ссылка для оплаты заказа #{order_num}:\n\n"
                        f"{pay_url}\n\n"
                        f"После оплаты заказ уйдёт на кухню автоматически 👌",
                        kb_wait_payment(order))
                else:
                    state["step"] = "choose_payment"
                    send(vk, user_id,
                        "⚠️ Не удалось создать ссылку на оплату. Заказ пока НЕ отправлен на кухню.\n"
                        "Выбери другой способ оплаты или попробуй онлайн ещё раз 👇",
                        kb_choose_payment(order))
                    send_emergency_alert(vk,
                        "Не удалось создать платёж ЮKassa",
                        f"Заказ #{order_num}, сумма {total}₽. Idempotence key сохранён для безопасного повтора.")
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

                # Выбираем ключи для проверки
                if order.get("point") == "Советская 2/10":
                    status = check_payment(payment_id,
                        shop_id=YUKASSA_SHOP_ID_SOVETSKAYA,
                        secret_key=YUKASSA_SECRET_KEY_SOVETSKAYA) if payment_id else None
                else:
                    status = check_payment(payment_id) if payment_id else None

                if status == "succeeded":
                    fallback = {
                        "user_id": user_id,
                        "user_name": user_name,
                        "first_name": first_name,
                        "order": copy.deepcopy(order),
                        "order_num": order_num,
                        "cart": cart,
                        "total": total,
                        "created_at": time.time(),
                        "shop_kind": shop_kind_for_order(order),
                    }
                    result = finalize_paid_payment(vk, payment_id, fallback)
                    if result == "already":
                        send(vk, user_id, "✅ Оплата уже получена, заказ на кухне!", kb_main())
                    elif result == "missing":
                        send(vk, user_id, "⚠️ Оплату вижу, но данные заказа не восстановились. Напиши нам — мы сразу проверим заказ.", kb_main())
                    elif result == "refund_retry":
                        send(vk, user_id, "⚠️ Оплата получена, но заказ не отправлен на кухню. Бот оформляет возврат; сотрудник уже уведомлён.", kb_main())
                    elif result == "refund_failed":
                        send(vk, user_id, "⚠️ Оплата получена, но заказ не отправлен на кухню. Автовозврат не завершился — сотрудник уже уведомлён и проверит его вручную.", kb_main())
                    # При refunded клиент уже получил подробное сообщение из finalize_paid_payment.
                    reset_state(user_id)
                elif status == "pending":
                    send(vk, user_id,
                        "⏳ Платёж ещё обрабатывается. Подожди минуту и нажми «Я оплатил» снова.",
                        kb_wait_payment(order))
                elif status == "canceled":
                    pending_payments.pop(payment_id, None)
                    save_pending_payments()
                    order.pop("payment_id", None)
                    order.pop("payment_create_key", None)
                    state["step"] = "choose_payment"
                    persist_user_states()
                    send(vk, user_id,
                         f"⚠️ Онлайн-оплата заказа #{order_num} отменена или не прошла. "
                         "Заказ на кухню не отправлен. Выбери способ оплаты ещё раз 👇",
                         kb_choose_payment(order))
                else:
                    if order.get("order_type") == "delivery":
                        hint = "⚠️ Оплата не найдена. Оплати онлайн ещё раз или выбери оплату курьеру 👇"
                    else:
                        hint = "⚠️ Оплата не найдена. Попробуй ещё раз или выбери оплату при получении."
                    send(vk, user_id, hint, kb_wait_payment(order))
                continue

            if text == "💵 Оплачу при получении":
                switch = mark_payment_abandoned(vk, order.get("payment_id"), order, "Оплата при получении")
                if switch == "paid":
                    fallback = {"user_id": user_id, "user_name": user_name, "first_name": first_name,
                                "order": copy.deepcopy(order), "order_num": order_num, "cart": cart,
                                "total": total, "created_at": time.time(), "shop_kind": shop_kind_for_order(order)}
                    paid_result = finalize_paid_payment(vk, order.get("payment_id"), fallback)
                    if paid_result in ("finalized", "already"):
                        send(vk, user_id, "✅ Онлайн-оплата уже успела пройти — заказ принят как оплаченный онлайн.", kb_main())
                    elif paid_result == "refund_retry":
                        send(vk, user_id, "⚠️ Оплата успела пройти, но заказ не отправлен на кухню. Возврат оформляется; сотрудник уведомлён.", kb_main())
                    elif paid_result == "refund_failed":
                        send(vk, user_id, "⚠️ Оплата успела пройти, но заказ не отправлен на кухню. Нужна ручная проверка возврата; сотрудник уведомлён.", kb_main())
                    reset_state(user_id)
                    continue
                # Старая онлайн-ссылка уже abandoned/canceled. Теперь безопасно проверяем заказ.
                order.pop("payment_id", None)
                order.pop("payment_create_key", None)
                if not ensure_order_available(vk, user_id, state):
                    continue
                if not ensure_order_time_current(vk, user_id, state):
                    continue
                _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Оплата при получении")
                reset_state(user_id)
                continue

            if text == "💳 Картой курьеру":
                switch = mark_payment_abandoned(vk, order.get("payment_id"), order, "Картой курьеру")
                if switch == "paid":
                    fallback = {"user_id": user_id, "user_name": user_name, "first_name": first_name,
                                "order": copy.deepcopy(order), "order_num": order_num, "cart": cart,
                                "total": total, "created_at": time.time(), "shop_kind": shop_kind_for_order(order)}
                    paid_result = finalize_paid_payment(vk, order.get("payment_id"), fallback)
                    if paid_result in ("finalized", "already"):
                        send(vk, user_id, "✅ Онлайн-оплата уже успела пройти — заказ принят как оплаченный онлайн.", kb_main())
                    elif paid_result == "refund_retry":
                        send(vk, user_id, "⚠️ Оплата успела пройти, но заказ не отправлен на кухню. Возврат оформляется; сотрудник уведомлён.", kb_main())
                    elif paid_result == "refund_failed":
                        send(vk, user_id, "⚠️ Оплата успела пройти, но заказ не отправлен на кухню. Нужна ручная проверка возврата; сотрудник уведомлён.", kb_main())
                    reset_state(user_id)
                    continue
                order.pop("payment_id", None)
                order.pop("payment_create_key", None)
                if not ensure_order_available(vk, user_id, state):
                    continue
                if not ensure_order_time_current(vk, user_id, state):
                    continue
                _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, "Картой курьеру")
                reset_state(user_id)
                continue

            if text == "💵 Наличными":
                switch = mark_payment_abandoned(vk, order.get("payment_id"), order, "Наличными курьеру")
                if switch == "paid":
                    fallback = {"user_id": user_id, "user_name": user_name, "first_name": first_name,
                                "order": copy.deepcopy(order), "order_num": order_num, "cart": cart,
                                "total": total, "created_at": time.time(), "shop_kind": shop_kind_for_order(order)}
                    paid_result = finalize_paid_payment(vk, order.get("payment_id"), fallback)
                    if paid_result in ("finalized", "already"):
                        send(vk, user_id, "✅ Онлайн-оплата уже успела пройти — заказ принят как оплаченный онлайн.", kb_main())
                    elif paid_result == "refund_retry":
                        send(vk, user_id, "⚠️ Оплата успела пройти, но заказ не отправлен на кухню. Возврат оформляется; сотрудник уведомлён.", kb_main())
                    elif paid_result == "refund_failed":
                        send(vk, user_id, "⚠️ Оплата успела пройти, но заказ не отправлен на кухню. Нужна ручная проверка возврата; сотрудник уведомлён.", kb_main())
                    reset_state(user_id)
                    continue
                order.pop("payment_id", None)
                order.pop("payment_create_key", None)
                if not ensure_order_available(vk, user_id, state):
                    continue
                if not ensure_order_time_current(vk, user_id, state):
                    continue
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
            # Позиция могла уехать в стоп, пока клиент выбирал сдачу.
            if not ensure_order_available(vk, user_id, state):
                continue
            if not ensure_order_time_current(vk, user_id, state):
                continue
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
            _finalize_order(vk, user_id, user_name, first_name, order, order_num, cart, total, pay_status)
            reset_state(user_id)
            continue

        # Дефолт
        send(vk, user_id, f"Привет, {first_name}! 👋\nВыбери действие:", kb_main())


if __name__ == "__main__":
    main()
