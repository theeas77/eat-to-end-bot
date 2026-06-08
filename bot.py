import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import datetime
import time

VK_TOKEN = "vk1.a.lbcUXPokTxgPCYnlF_UcqQGaHW4nbI2dkqpNUfqL2tGCrjhST6s-4yoeGf6z0xrx1B1TXjcaWMu1EAWDDrqfH9us2nT7381dpYQUaiiXbaZAwqZbpEVGQ9oxyw3Bqsu_mbdyWdFVKlhcbNZE3lybJXXGoadma1fWTdzjtADUvTTZR2bbIySqQn8_qlyj5bYTzaC1DzmOHoWGJkRH_szQsA"
ADMIN_VK_ID = 1118370233

MANAGERS = {
    "Ленина 36/2": 1118370233,
    "Промышленная 13": 1118370233,
    "Советская 2/10": 1118370233,
}

HOURS = {
    "Ленина 36/2": (9, 24),
    "Промышленная 13": (9, 23),
    "Советская 2/10": (9, 21),
}

MENU = {
    "Шаурма и сэндвичи": {
        "С курицей мини": 235,
        "С курицей стандарт": 280,
        "С курицей большая": 350,
        "Со свининой мини": 250,
        "Со свининой стандарт": 325,
        "Со свининой большая": 380,
        "Овощная стандарт": 245,
        "Сэндвич с курицей": 250,
        "Сэндвич с беконом": 250,
    },
    "Шашлык": {
        "Шашлык из курицы": 360,
        "Шашлык из свинины": 395,
    },
    "Кофе и чай": {
        "Эспрессо": 100,
        "Двойной эспрессо": 120,
        "Американо": 100,
        "Латте": 120,
        "Капучино": 120,
        "Чай Черный": 100,
        "Чай Зеленый": 100,
        "Чай с малиной": 120,
        "Чай Пряный": 120,
    },
    "Напитки": {
        "Морс Фруктовый": 90,
        "Морс Облепиховый": 90,
        "Морс Малина-мята": 90,
        "Морс Клубника": 90,
        "Кола 0.5л": 110,
        "Лимон-лайм 0.5л": 110,
        "Апельсин 0.5л": 110,
        "Кола 0.3л": 90,
        "Лимон-лайм 0.3л": 90,
        "Апельсин 0.3л": 90,
    },
}

SAUCES = ["Фирменный", "BBQ", "Острый", "Сырный", "Медово-горчичный", "Без соуса"]

EXTRAS = {
    "Сыр тертый": 42,
    "Огурцы соленые": 42,
    "Морковка корейская": 42,
    "Красный лук": 42,
    "Лук фри": 42,
    "Халапеньо": 42,
    "Бекон": 42,
    "Ананасы": 42,
    "Оливки": 42,
    "Перец болгарский": 42,
    "Курица доп.": 77,
    "Свинина доп.": 77,
}

order_counter = 0
user_states = {}
processed_msgs = {}

def get_state(user_id):
    if user_id not in user_states:
        user_states[user_id] = {
            "step": "main",
            "order": {"items": {}, "extras": [], "sauce": None, "point": None, "pickup_time": None}
        }
    return user_states[user_id]

def reset_state(user_id):
    user_states[user_id] = {
        "step": "main",
        "order": {"items": {}, "extras": [], "sauce": None, "point": None, "pickup_time": None}
    }

def is_point_open(point):
    now = datetime.datetime.now()
    open_h, close_h = HOURS.get(point, (9, 22))
    current_h = now.hour
    if close_h == 24:
        return current_h >= open_h or current_h < 0
    return open_h <= current_h < close_h

def get_time_slots(point):
    slots = []
    open_h, close_h = HOURS.get(point, (9, 22))
    if close_h == 24:
        close_h = 23
        close_m = 45
    else:
        close_m = 0

    current = datetime.datetime.combine(datetime.date.today(), datetime.time(open_h, 0))
    end_dt = datetime.datetime.combine(datetime.date.today(), datetime.time(close_h, close_m))
    now = datetime.datetime.now() + datetime.timedelta(minutes=20)

    while current <= end_dt:
        if current > now:
            slots.append(current.strftime("%H:%M"))
        current += datetime.timedelta(minutes=15)
    return slots[:12]

def format_cart(order):
    if not order["items"]:
        return "Корзина пуста"
    lines = []
    total = 0
    for item_name, qty in order["items"].items():
        price = 0
        for cat in MENU.values():
            if item_name in cat:
                price = cat[item_name]
                break
        lines.append(f"  {item_name} x{qty} — {price * qty}₽")
        total += price * qty
    for extra in order.get("extras", []):
        price = EXTRAS.get(extra, 42)
        lines.append(f"  + {extra} — {price}₽")
        total += price
    order["total"] = total
    return "\n".join(lines) + f"\n\n  Итого: {total}₽"

def kb_main():
    kb = VkKeyboard(one_time=False)
    kb.add_button("🌯 Сделать предзаказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("📍 Наши точки", color=VkKeyboardColor.SECONDARY)
    kb.add_button("ℹ️ О нас", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()

def kb_points():
    kb = VkKeyboard(one_time=True)
    for point in MANAGERS.keys():
        status = "✅" if is_point_open(point) else "❌"
        kb.add_button(f"{status} {point}", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("◀️ Назад", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_categories():
    kb = VkKeyboard(one_time=True)
    for cat in MENU.keys():
        kb.add_button(cat, color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("🛒 Корзина и далее", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_items(category):
    kb = VkKeyboard(one_time=True)
    items = list(MENU[category].items())
    for i, (name, price) in enumerate(items):
        kb.add_button(f"{name} {price}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("◀️ К категориям", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()

def kb_sauces():
    kb = VkKeyboard(one_time=True)
    kb.add_button("Фирменный", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("BBQ", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Острый", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Сырный", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Медово-горчичный", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("Без соуса", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()

def kb_extras():
    kb = VkKeyboard(one_time=True)
    for extra, price in list(EXTRAS.items()):
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("✅ Без добавок", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()

def kb_time(slots):
    kb = VkKeyboard(one_time=True)
    for i, slot in enumerate(slots):
        kb.add_button(slot, color=VkKeyboardColor.SECONDARY)
        if (i + 1) % 3 == 0:
            kb.add_line()
    kb.add_line()
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_confirm():
    kb = VkKeyboard(one_time=True)
    kb.add_button("✅ Подтвердить", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("🔄 Начать заново", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def send(vk, user_id, text, keyboard=None):
    params = {"user_id": user_id, "message": text, "random_id": 0}
    if keyboard:
        params["keyboard"] = keyboard
    vk.messages.send(**params)

def main():
    global order_counter
    vk_session = vk_api.VkApi(token=VK_TOKEN)
    vk = vk_session.get_api()
    longpoll = VkLongPoll(vk_session)
    print("Бот запущен!")

    for event in longpoll.listen():
        if not (event.type == VkEventType.MESSAGE_NEW and event.to_me and not event.from_me):
            continue

        # Защита от дублирования
        msg_key = f"{event.user_id}_{event.message_id}"
        if msg_key in processed_msgs:
            continue
        processed_msgs[msg_key] = time.time()

        # Чистим старые ключи каждые 1000 сообщений
        if len(processed_msgs) > 1000:
            cutoff = time.time() - 60
            processed_msgs.clear()

        user_id = event.user_id
        text = event.text.strip()
        state = get_state(user_id)
        step = state["step"]

        try:
            user_info = vk.users.get(user_ids=user_id)
            user_name = f"{user_info[0]['first_name']} {user_info[0]['last_name']}"
            first_name = user_info[0]['first_name']
        except:
            user_name = "Клиент"
            first_name = "Друг"

        # СТАРТ
        if text.lower() in ["начать", "start", "/start", "❌ отмена", "🔄 начать заново", "◀️ назад"]:
            reset_state(user_id)
            send(vk, user_id,
                f"Привет, {first_name}! 👋\n\n"
                f"Добро пожаловать в Eat to End — шаурма из шашлыка 🌯🔥\n\n"
                f"Твой быстрый перекус в удобном месте.\n"
                f"Оформи предзаказ и забери готовое без очереди!",
                kb_main())
            continue

        # О НАС
        if text == "ℹ️ О нас":
            send(vk, user_id,
                "🌯 Eat to End — шаурма из шашлыка\n\n"
                "Мы готовим из качественных продуктов "
                "в стильном заведении. Мясо на углях — "
                "наша фишка.\n\n"
                "💳 Оплата при получении\n"
                "📦 Предзаказ — без очереди\n\n"
                "— Дружелюбно\n"
                "— Честно\n"
                "— Вкусно",
                kb_main())
            continue

        # НАШИ ТОЧКИ
        if text == "📍 Наши точки":
            send(vk, user_id,
                "📍 Наши точки:\n\n"
                "1. Ленина 36/2 с 2\n"
                "   ⏰ 09:00 — 00:00\n\n"
                "2. Промышленная 13\n"
                "   ⏰ 09:00 — 23:00\n\n"
                "3. Советская 2/10 с 1\n"
                "   ⏰ 09:00 — 21:00",
                kb_main())
            continue

        # НАЧАЛО ЗАКАЗА
        if text == "🌯 Сделать предзаказ":
            state["step"] = "choose_point"
            send(vk, user_id,
                "📍 Выбери точку самовывоза:\n\n"
                "✅ — открыто  ❌ — закрыто",
                kb_points())
            continue

        # ВЫБОР ТОЧКИ
        if step == "choose_point":
            matched = None
            for point in MANAGERS.keys():
                if point in text:
                    matched = point
                    break
            if matched:
                if not is_point_open(matched):
                    open_h, close_h = HOURS[matched]
                    send(vk, user_id,
                        f"😔 Точка {matched} сейчас закрыта.\n"
                        f"Режим работы: {open_h}:00 — {close_h if close_h != 24 else '00'}:00\n\n"
                        f"Выбери другую точку или приходи в рабочее время!",
                        kb_points())
                else:
                    state["order"]["point"] = matched
                    state["step"] = "choose_category"
                    send(vk, user_id,
                        f"✅ Точка: {matched}\n\nЧто будешь? Выбери категорию:",
                        kb_categories())
            else:
                send(vk, user_id, "Выбери точку из списка 👇", kb_points())
            continue

        # ВЫБОР КАТЕГОРИИ
        if step == "choose_category":
            if text == "🛒 Корзина и далее":
                if not state["order"]["items"]:
                    send(vk, user_id, "Корзина пуста! Добавь хотя бы одну позицию 😊", kb_categories())
                else:
                    state["step"] = "choose_sauce"
                    send(vk, user_id, "🥫 Выбери соус:", kb_sauces())
                continue

            matched_cat = None
            for cat in MENU.keys():
                if cat in text:
                    matched_cat = cat
                    break
            if matched_cat:
                state["step"] = "choose_item"
                state["current_category"] = matched_cat
                send(vk, user_id, f"Выбери позицию из «{matched_cat}»:", kb_items(matched_cat))
            else:
                send(vk, user_id, "Выбери категорию 👇", kb_categories())
            continue

        # ВЫБОР БЛЮДА
        if step == "choose_item":
            if text == "◀️ К категориям":
                state["step"] = "choose_category"
                cart = format_cart(state["order"])
                send(vk, user_id, f"🛒 Корзина:\n{cart}\n\nВыбери категорию:", kb_categories())
                continue

            cat = state.get("current_category", "")
            found = False
            for name, price in MENU.get(cat, {}).items():
                if name in text or text.startswith(name[:10]):
                    state["order"]["items"][name] = state["order"]["items"].get(name, 0) + 1
                    found = True
                    cart = format_cart(state["order"])
                    send(vk, user_id,
                        f"✅ Добавлено: {name}\n\n🛒 Корзина:\n{cart}\n\nДобавить ещё или перейти к оформлению?",
                        kb_items(cat))
                    break
            if not found:
                send(vk, user_id, "Выбери позицию из списка 👇", kb_items(cat))
            continue

        # ВЫБОР СОУСА
        if step == "choose_sauce":
            if text in SAUCES:
                state["order"]["sauce"] = text
                state["step"] = "choose_extras"
                send(vk, user_id, "➕ Хочешь добавки? Можно выбрать несколько.", kb_extras())
            else:
                send(vk, user_id, "Выбери соус 👇", kb_sauces())
            continue

        # ДОБАВКИ
        if step == "choose_extras":
            if text == "✅ Без добавок":
                slots = get_time_slots(state["order"]["point"])
                if not slots:
                    send(vk, user_id,
                        "😔 К сожалению, точка скоро закрывается и мы не успеем приготовить заказ.\n"
                        "Приходи завтра!",
                        kb_main())
                    reset_state(user_id)
                else:
                    state["step"] = "choose_time"
                    send(vk, user_id, "⏰ На какое время готовить?", kb_time(slots))
                continue

            matched_extra = None
            for extra_name in EXTRAS.keys():
                if extra_name in text:
                    matched_extra = extra_name
                    break
            if matched_extra:
                if matched_extra not in state["order"]["extras"]:
                    state["order"]["extras"].append(matched_extra)
                send(vk, user_id,
                    f"✅ Добавлено: {matched_extra}\nЕщё добавки или нажми «Без добавок»:",
                    kb_extras())
            else:
                send(vk, user_id, "Выбери добавку 👇", kb_extras())
            continue

        # ВРЕМЯ
        if step == "choose_time":
            slots = get_time_slots(state["order"]["point"])
            if text in slots:
                state["order"]["pickup_time"] = text
                state["step"] = "confirm"
                cart = format_cart(state["order"])
                summary = (
                    f"📋 Твой заказ:\n\n"
                    f"📍 {state['order']['point']}\n"
                    f"⏰ Время готовности: {text}\n"
                    f"🥫 Соус: {state['order']['sauce']}\n"
                    f"➕ Добавки: {', '.join(state['order']['extras']) or 'нет'}\n\n"
                    f"{cart}\n\n"
                    f"💳 Оплата при получении\n\n"
                    f"Всё верно? 👇"
                )
                send(vk, user_id, summary, kb_confirm())
            else:
                send(vk, user_id, "Выбери время из списка 👇", kb_time(slots))
            continue

        # ПОДТВЕРЖДЕНИЕ
        if step == "confirm":
            if text == "✅ Подтвердить":
                order_counter += 1
                order = state["order"]
                cart = format_cart(order)

                manager_id = MANAGERS.get(order["point"], ADMIN_VK_ID)
                notif = (
                    f"🆕 НОВЫЙ ЗАКАЗ #{order_counter}\n\n"
                    f"👤 {user_name} (vk.com/id{user_id})\n"
                    f"📍 {order['point']}\n"
                    f"⏰ Готовность: {order['pickup_time']}\n"
                    f"🥫 Соус: {order['sauce']}\n"
                    f"➕ Добавки: {', '.join(order['extras']) or 'нет'}\n\n"
                    f"{cart}\n\n"
                    f"💳 Оплата при получении"
                )
                try:
                    vk.messages.send(user_id=manager_id, message=notif, random_id=0)
                except Exception as e:
                    print(f"Ошибка уведомления: {e}")

                send(vk, user_id,
                    f"🎉 Заказ #{order_counter} принят!\n\n"
                    f"📍 {order['point']}\n"
                    f"⏰ Будет готов к {order['pickup_time']}\n\n"
                    f"Оплата при получении 💳\n\n"
                    f"Ждём тебя, {first_name}! До встречи 🌯🔥",
                    kb_main())
                reset_state(user_id)

            elif text == "🔄 Начать заново":
                reset_state(user_id)
                send(vk, user_id, "Хорошо, начнём заново 😊", kb_main())
            else:
                send(vk, user_id, "Нажми «Подтвердить» или «Начать заново» 👇", kb_confirm())
            continue

        # Дефолт
        send(vk, user_id,
            f"Привет, {first_name}! 👋\n"
            f"Выбери действие:",
            kb_main())

if __name__ == "__main__":
    main()
