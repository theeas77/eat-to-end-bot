import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import datetime
import time
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Yekaterinburg")  # UTC+5 Пермь

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

# Категории с соусом
SAUCE_CATS = {"Шаурма и сэндвичи"}
# Категории с добавками
EXTRAS_CATS = {"Шаурма и сэндвичи", "Шашлык"}

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
            "order": {
                "items": [],  # list of dicts: {name, price, sauce, extras}
                "point": None,
                "pickup_time": None,
            },
            "current_item": None,  # item being configured right now
        }
    return user_states[user_id]


def reset_state(user_id):
    user_states[user_id] = {
        "step": "main",
        "order": {
            "items": [],
            "point": None,
            "pickup_time": None,
        },
        "current_item": None,
    }


def is_point_open(point):
    now = datetime.datetime.now(TZ)
    open_h, close_h = HOURS.get(point, (9, 22))
    current_h = now.hour
    if close_h == 24:
        return current_h >= open_h
    return open_h <= current_h < close_h


def get_time_slots(point, min_minutes=15):
    slots = []
    open_h, close_h = HOURS.get(point, (9, 22))
    if close_h == 24:
        close_h = 23
        close_m = 59
    else:
        close_m = 0

    now = datetime.datetime.now(TZ)
    today = now.date()

    # Первый слот = текущее время + min_minutes, округлённое до минуты
    start_time = (now + datetime.timedelta(minutes=min_minutes)).replace(second=0, microsecond=0)

    end_dt = datetime.datetime.combine(today, datetime.time(close_h, close_m), tzinfo=TZ)
    current = start_time

    while current <= end_dt:
        if current.hour >= open_h:
            slots.append(current.strftime("%H:%M"))
        current += datetime.timedelta(minutes=10)

    return slots[:9]


def format_cart(order):
    if not order["items"]:
        return "Корзина пуста"
    lines = []
    total = 0
    for item in order["items"]:
        name = item["name"]
        price = item["price"]
        sauce = item.get("sauce")
        extras = item.get("extras", [])
        line = f"  {name} — {price}₽"
        if sauce and sauce != "Без соуса":
            line += f" (соус: {sauce})"
        if extras:
            line += f"\n    + {', '.join(extras)}"
            extras_total = sum(EXTRAS.get(e, 42) for e in extras)
            price += extras_total
        lines.append(line)
        total += price
    return "\n".join(lines) + f"\n\n  Итого: {total}₽"


def get_total(order):
    total = 0
    for item in order["items"]:
        total += item["price"]
        for e in item.get("extras", []):
            total += EXTRAS.get(e, 42)
    return total


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
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("❌ Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()


def kb_items(category):
    kb = VkKeyboard(one_time=True)
    for name, price in MENU[category].items():
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


def kb_extras_page1():
    kb = VkKeyboard(one_time=True)
    extras = list(EXTRAS.items())[:7]
    for extra, price in extras:
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("🥫 Доп соус +42₽", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("➡️ Ещё добавки", color=VkKeyboardColor.SECONDARY)
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
    for extra, price in extras:
        kb.add_button(f"{extra} +{price}₽", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("✅ Без добавок", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_after_item():
    kb = VkKeyboard(one_time=True)
    kb.add_button("➕ Добавить ещё", color=VkKeyboardColor.SECONDARY)
    kb.add_line()
    kb.add_button("🛒 Оформить заказ", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()


def kb_time(slots):
    kb = VkKeyboard(one_time=True)
    for slot in slots[:9]:
        kb.add_button(slot, color=VkKeyboardColor.SECONDARY)
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

        msg_key = f"{event.user_id}_{event.message_id}"
        if msg_key in processed_msgs:
            continue
        processed_msgs[msg_key] = time.time()
        if len(processed_msgs) > 1000:
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

        if text == "ℹ️ О нас":
            send(vk, user_id,
                "🌯 Eat to End — шаурма из шашлыка\n\n"
                "Мы готовим из качественных продуктов "
                "в стильном заведении. Мясо на углях — наша фишка.\n\n"
                "💳 Оплата при получении\n"
                "📦 Предзаказ — без очереди\n\n"
                "— Дружелюбно\n— Честно\n— Вкусно",
                kb_main())
            continue

        if text == "📍 Наши точки":
            send(vk, user_id,
                "📍 Наши точки:\n\n"
                "1. Ленина 36/2 с 2\n   ⏰ 09:00 — 00:00\n\n"
                "2. Промышленная 13\n   ⏰ 09:00 — 23:00\n\n"
                "3. Советская 2/10 с 1\n   ⏰ 09:00 — 21:00",
                kb_main())
            continue

        # НАЧАЛО ЗАКАЗА
        if text == "🌯 Сделать предзаказ":
            state["step"] = "choose_point"
            send(vk, user_id,
                "📍 Выбери точку самовывоза:\n\n✅ — открыто  ❌ — закрыто",
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
                        f"Режим работы: {open_h}:00 — {'00' if close_h == 24 else close_h}:00\n\n"
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
            if text == "🛒 Оформить заказ":
                if not state["order"]["items"]:
                    send(vk, user_id, "Корзина пуста! Добавь хотя бы одну позицию 😊", kb_categories())
                else:
                    # Считаем мин время
                    has_shashlik = any("Шашлык" in i["name"] for i in state["order"]["items"])
                    min_min = 30 if has_shashlik else 15
                    slots = get_time_slots(state["order"]["point"], min_minutes=min_min)
                    if not slots:
                        send(vk, user_id,
                            "😔 Точка скоро закрывается, не успеем приготовить.\nПриходи завтра!",
                            kb_main())
                        reset_state(user_id)
                    else:
                        state["step"] = "choose_time"
                        state["order"]["min_minutes"] = min_min
                        hint = "⏰ На какое время готовить?\n\nВыбери из списка или напиши своё время в формате ЧЧ:ММ (например 14:30)"
                        if has_shashlik:
                            hint += "\n\n🔥 Шашлык готовится 30 минут — учли это в слотах"
                        send(vk, user_id, hint, kb_time(slots))
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
                    found = True
                    # Сохраняем текущий выбранный item
                    state["current_item"] = {"name": name, "price": price, "sauce": None, "extras": [], "cat": cat}

                    if cat in SAUCE_CATS:
                        state["step"] = "choose_sauce_for_item"
                        send(vk, user_id,
                            f"✅ {name}\n\nВыбери соус:",
                            kb_sauces())
                    elif cat in EXTRAS_CATS:
                        state["step"] = "choose_extras_for_item"
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
                send(vk, user_id, "➕ Хочешь добавки?", kb_extras_page1())
            else:
                send(vk, user_id, "Выбери соус 👇", kb_sauces())
            continue

        # ДОБАВКИ ДЛЯ ПОЗИЦИИ
        if step == "choose_extras_for_item":
            if text == "➡️ Ещё добавки":
                send(vk, user_id, "➕ Ещё добавки:", kb_extras_page2())
                continue

            if text == "✅ Без добавок":
                # Добавляем позицию в корзину
                state["order"]["items"].append(state["current_item"])
                item_name = state["current_item"]["name"]
                state["current_item"] = None
                state["step"] = "choose_category"
                cart = format_cart(state["order"])
                send(vk, user_id,
                    f"✅ {item_name} добавлен в корзину!\n\n🛒 Корзина:\n{cart}\n\nДобавить ещё или оформить?",
                    kb_after_item())
                continue

            if text == "🥫 Доп соус +42₽":
                send(vk, user_id, "Выбери соус:", kb_extra_sauces())
                continue

            if text == "◀️ Назад к добавкам":
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
            send(vk, user_id, "Выбери категорию:", kb_categories())
            continue

        if step == "choose_category" and text == "🛒 Оформить заказ":
            if not state["order"]["items"]:
                send(vk, user_id, "Корзина пуста!", kb_categories())
            else:
                has_shashlik = any("Шашлык" in i["name"] for i in state["order"]["items"])
                min_min = 30 if has_shashlik else 15
                slots = get_time_slots(state["order"]["point"], min_minutes=min_min)
                if not slots:
                    send(vk, user_id, "😔 Точка скоро закрывается.\nПриходи завтра!", kb_main())
                    reset_state(user_id)
                else:
                    state["step"] = "choose_time"
                    state["order"]["min_minutes"] = min_min
                    hint = "⏰ На какое время готовить?\n\nВыбери из списка или напиши своё время в формате ЧЧ:ММ"
                    if has_shashlik:
                        hint += "\n\n🔥 Шашлык готовится 30 минут — учли это в слотах"
                    send(vk, user_id, hint, kb_time(slots))
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
                    input_dt = datetime.datetime.combine(now.date(), datetime.time(h, m), tzinfo=TZ)
                    min_time = now + datetime.timedelta(minutes=min_min)
                    open_h, close_h = HOURS.get(state["order"]["point"], (9, 22))
                    if close_h == 24:
                        close_dt = datetime.datetime.combine(now.date(), datetime.time(23, 59), tzinfo=TZ)
                    else:
                        close_dt = datetime.datetime.combine(now.date(), datetime.time(close_h, 0), tzinfo=TZ)

                    if input_dt < min_time:
                        send(vk, user_id,
                            f"⚠️ Слишком рано! Минимум через {min_min} мин.\nВведи другое время:",
                            kb_time(slots))
                    elif input_dt > close_dt:
                        send(vk, user_id, "⚠️ Точка уже будет закрыта.\nВыбери другое время:", kb_time(slots))
                    elif h < open_h:
                        send(vk, user_id, f"⚠️ Точка открывается в {open_h}:00.", kb_time(slots))
                    else:
                        chosen_time = text
                except:
                    send(vk, user_id, "⚠️ Неверный формат. Напиши как 14:30:", kb_time(slots))
            else:
                send(vk, user_id, "Выбери время или напиши в формате ЧЧ:ММ 👇", kb_time(slots))

            if chosen_time:
                state["order"]["pickup_time"] = chosen_time
                state["step"] = "enter_phone"
                send(vk, user_id,
                    "📱 Укажи номер телефона для связи\n\n"
                    "Напиши в формате: 89991234567")
            continue

        # ТЕЛЕФОН
        if step == "enter_phone":
            phone = text.strip().replace(" ", "").replace("-", "").replace("+", "")
            if phone.startswith("8") and len(phone) == 11 and phone.isdigit():
                state["order"]["phone"] = phone
                state["step"] = "confirm"
                cart = format_cart(state["order"])
                total = get_total(state["order"])
                summary = (
                    f"📋 Твой заказ:\n\n"
                    f"📍 {state['order']['point']}\n"
                    f"⏰ Время готовности: {state['order']['pickup_time']}\n"
                    f"📱 Телефон: {phone}\n\n"
                    f"{cart}\n\n"
                    f"💳 Оплата при получении\n\n"
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
                order_counter += 1
                order = state["order"]
                cart = format_cart(order)
                total = get_total(order)

                manager_id = MANAGERS.get(order["point"], ADMIN_VK_ID)
                notif = (
                    f"🆕 НОВЫЙ ЗАКАЗ #{order_counter}\n\n"
                    f"👤 {user_name} (vk.com/id{user_id})\n"
                    f"📱 {order.get('phone', 'не указан')}\n"
                    f"📍 {order['point']}\n"
                    f"⏰ Готовность: {order['pickup_time']}\n\n"
                    f"{cart}\n\n"
                    f"💳 Оплата при получении\n"
                    f"💰 Сумма: {total}₽"
                )
                try:
                    vk.messages.send(user_id=manager_id, message=notif, random_id=0)
                except Exception as e:
                    print(f"Ошибка уведомления: {e}")

                send(vk, user_id,
                    f"🎉 Заказ #{order_counter} принят!\n\n"
                    f"📍 {order['point']}\n"
                    f"⏰ Будет готов к {order['pickup_time']}\n"
                    f"💰 Сумма: {total}₽\n\n"
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
        send(vk, user_id, f"Привет, {first_name}! 👋\nВыбери действие:", kb_main())


if __name__ == "__main__":
    main()
