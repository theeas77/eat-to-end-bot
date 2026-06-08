import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor
import datetime

VK_TOKEN = "vk1.a.lbcUXPokTxgPCYnlF_UcqQGaHW4nbI2dkqpNUfqL2tGCrjhST6s-4yoeGf6z0xrx1B1TXjcaWMu1EAWDDrqfH9us2nT7381dpYQUaiiXbaZAwqZbpEVGQ9oxyw3Bqsu_mbdyWdFVKlhcbNZE3lybJXXGoadma1fWTdzjtADUvTTZR2bbIySqQn8_qlyj5bYTzaC1DzmOHoWGJkRH_szQsA"
ADMIN_VK_ID = 1118370233

MANAGERS = {
    "Ленина 36/2": 1118370233,
    "Промышленная 13": 1118370233,
    "Советская 2/10": 1118370233,
}

MENU = {
    "Шаурма": {
        "Шаурма с курицей мини": 235,
        "Шаурма с курицей стандарт": 280,
        "Шаурма с курицей большая": 350,
        "Шаурма со свининой мини": 250,
        "Шаурма со свининой стандарт": 325,
        "Шаурма со свининой большая": 380,
        "Шаурма овощная стандарт": 245,
        "Сэндвич с курицей": 250,
        "Сэндвич с беконом": 250,
    },
    "Шашлык": {
        "Шашлык из курицы": 360,
        "Шашлык из свинины": 395,
    },
    "Напитки": {
        "Лимонад Цитрусовый": 160,
        "Эспрессо": 100,
        "Двойной эспрессо": 120,
        "Американо": 100,
        "Латте": 120,
        "Капучино": 120,
        "Чай Черный": 100,
        "Чай Зеленый с можжевельником": 100,
        "Чай Черный с малиной и мятой": 120,
        "Чай Пряный с облепихой и имбирем": 120,
        "Морс Фруктово-ягодный": 90,
        "Морс Облепиховый": 90,
        "Морс Малина-мята": 90,
        "Морс Клубника-базилик": 90,
        "Добрый Кола 0.5л": 110,
        "Добрый Лимон-лайм 0.5л": 110,
        "Добрый Апельсин 0.5л": 110,
        "Добрый Кола 0.3л": 90,
        "Добрый Лимон-лайм 0.3л": 90,
        "Добрый Апельсин 0.3л": 90,
    },
}

SAUCES = ["Фирменный", "BBQ", "Острый", "Сырный", "Медово-горчичный", "Без соуса"]

EXTRAS = {
    "Сыр тертый": 42,
    "Огурцы соленые": 42,
    "Морковка по-корейски": 42,
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

def get_time_slots():
    slots = []
    current = datetime.datetime.combine(datetime.date.today(), datetime.time(10, 0))
    end_dt = datetime.datetime.combine(datetime.date.today(), datetime.time(22, 0))
    now = datetime.datetime.now() + datetime.timedelta(minutes=20)
    while current <= end_dt:
        if current > now:
            slots.append(current.strftime("%H:%M"))
        current += datetime.timedelta(minutes=15)
    return slots[:16]

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
        lines.append(f"{item_name} x{qty} = {price * qty}р.")
        total += price * qty
    for extra in order.get("extras", []):
        price = EXTRAS.get(extra, 42)
        lines.append(f"{extra} = {price}р.")
        total += price
    order["total"] = total
    return "\n".join(lines) + f"\n\nИтого: {total}р."

def kb_main():
    kb = VkKeyboard(one_time=False)
    kb.add_button("Сделать предзаказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("О боте", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()

def kb_points():
    kb = VkKeyboard(one_time=True)
    for point in MANAGERS.keys():
        kb.add_button(point, color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_categories():
    kb = VkKeyboard(one_time=True)
    for cat in MENU.keys():
        kb.add_button(cat, color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("Корзина и далее", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_items(category):
    kb = VkKeyboard(one_time=True)
    items = list(MENU[category].items())
    for i, (name, price) in enumerate(items):
        short = name[:30]
        kb.add_button(f"{short} {price}р", color=VkKeyboardColor.SECONDARY)
        if (i + 1) % 2 == 0 and i < len(items) - 1:
            kb.add_line()
    kb.add_line()
    kb.add_button("Назад к категориям", color=VkKeyboardColor.SECONDARY)
    return kb.get_keyboard()

def kb_sauces():
    kb = VkKeyboard(one_time=True)
    for sauce in SAUCES:
        kb.add_button(sauce, color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    return kb.get_keyboard()

def kb_extras():
    kb = VkKeyboard(one_time=True)
    extras = list(EXTRAS.items())[:6]
    for extra, price in extras:
        kb.add_button(f"{extra} +{price}р", color=VkKeyboardColor.SECONDARY)
        kb.add_line()
    kb.add_button("Готово без добавок", color=VkKeyboardColor.POSITIVE)
    return kb.get_keyboard()

def kb_time(slots):
    kb = VkKeyboard(one_time=True)
    for i, slot in enumerate(slots[:12]):
        kb.add_button(slot, color=VkKeyboardColor.SECONDARY)
        if (i + 1) % 3 == 0:
            kb.add_line()
    kb.add_line()
    kb.add_button("Отмена", color=VkKeyboardColor.NEGATIVE)
    return kb.get_keyboard()

def kb_confirm():
    kb = VkKeyboard(one_time=True)
    kb.add_button("Подтвердить заказ", color=VkKeyboardColor.POSITIVE)
    kb.add_line()
    kb.add_button("Начать заново", color=VkKeyboardColor.NEGATIVE)
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

    processed_ids = set()
    for event in longpoll.listen():
        if event.type == VkEventType.MESSAGE_NEW and event.to_me and not event.from_me:
            if event.message_id in processed_ids:
                continue
            processed_ids.add(event.message_id)
            user_id = event.user_id
            text = event.text.strip()
            state = get_state(user_id)
            step = state["step"]

            try:
                user_info = vk.users.get(user_ids=user_id)
                user_name = f"{user_info[0]['first_name']} {user_info[0]['last_name']}"
            except:
                user_name = "Клиент"

            # СТАРТ
            if text.lower() in ["начать", "start", "/start", "отмена", "начать заново"]:
                reset_state(user_id)
                send(vk, user_id, "Привет! Это бот предзаказов Eat to End. Выбери действие:", kb_main())
                continue

            # О БОТЕ
            if text == "О боте":
                send(vk, user_id,
                    "Eat to End — предзаказ шаурмы\n\n"
                    "Наши точки:\n"
                    "Ленина 36/2 с 2\n"
                    "Промышленная 13\n"
                    "Советская 2/10 с 1\n\n"
                    "Режим работы: 10:00 - 22:00\n"
                    "Оплата при получении.",
                    kb_main())
                continue

            # НАЧАЛО ЗАКАЗА
            if text == "Сделать предзаказ" or step == "main":
                if text == "Сделать предзаказ":
                    state["step"] = "choose_point"
                    send(vk, user_id, "Выбери точку самовывоза:", kb_points())
                    continue
                else:
                    send(vk, user_id, "Выбери действие:", kb_main())
                    continue

            # ВЫБОР ТОЧКИ
            if step == "choose_point":
                matched = None
                for point in MANAGERS.keys():
                    if point in text:
                        matched = point
                        break
                if matched:
                    state["order"]["point"] = matched
                    state["step"] = "choose_category"
                    send(vk, user_id, f"Точка: {matched}\n\nВыбери категорию:", kb_categories())
                else:
                    send(vk, user_id, "Выбери точку из списка:", kb_points())
                continue

            # ВЫБОР КАТЕГОРИИ
            if step == "choose_category":
                if text == "Корзина и далее":
                    if not state["order"]["items"]:
                        send(vk, user_id, "Корзина пуста! Добавь хотя бы одну позицию.", kb_categories())
                    else:
                        state["step"] = "choose_sauce"
                        send(vk, user_id, "Выбери соус:", kb_sauces())
                    continue

                matched_cat = None
                for cat in MENU.keys():
                    if cat in text:
                        matched_cat = cat
                        break
                if matched_cat:
                    state["step"] = "choose_item"
                    state["current_category"] = matched_cat
                    send(vk, user_id, f"Выбери блюдо из {matched_cat}:", kb_items(matched_cat))
                else:
                    send(vk, user_id, "Выбери категорию:", kb_categories())
                continue

            # ВЫБОР БЛЮДА
            if step == "choose_item":
                if text == "Назад к категориям":
                    state["step"] = "choose_category"
                    cart = format_cart(state["order"])
                    send(vk, user_id, f"Корзина:\n{cart}\n\nВыбери категорию:", kb_categories())
                    continue

                cat = state.get("current_category", "")
                found = False
                for name, price in MENU.get(cat, {}).items():
                    if name in text or text.startswith(name[:15]):
                        state["order"]["items"][name] = state["order"]["items"].get(name, 0) + 1
                        found = True
                        cart = format_cart(state["order"])
                        send(vk, user_id, f"Добавлено: {name}\n\nКорзина:\n{cart}\n\nДобавить ещё?", kb_items(cat))
                        break
                if not found:
                    send(vk, user_id, "Выбери позицию из списка:", kb_items(cat))
                continue

            # ВЫБОР СОУСА
            if step == "choose_sauce":
                if text in SAUCES:
                    state["order"]["sauce"] = text
                    state["step"] = "choose_extras"
                    send(vk, user_id, "Хочешь добавки?", kb_extras())
                else:
                    send(vk, user_id, "Выбери соус:", kb_sauces())
                continue

            # ДОБАВКИ
            if step == "choose_extras":
                if text == "Готово без добавок":
                    slots = get_time_slots()
                    if not slots:
                        send(vk, user_id, "Мы уже закрыты. Приходи завтра с 10:00!", kb_main())
                        reset_state(user_id)
                    else:
                        state["step"] = "choose_time"
                        send(vk, user_id, "На какое время готовить?", kb_time(slots))
                    continue

                matched_extra = None
                for extra_name in EXTRAS.keys():
                    if extra_name in text:
                        matched_extra = extra_name
                        break
                if matched_extra:
                    if matched_extra not in state["order"]["extras"]:
                        state["order"]["extras"].append(matched_extra)
                    send(vk, user_id, f"Добавлено: {matched_extra}\nЕщё добавки или 'Готово без добавок':", kb_extras())
                else:
                    send(vk, user_id, "Выбери добавку:", kb_extras())
                continue

            # ВРЕМЯ
            if step == "choose_time":
                slots = get_time_slots()
                if text in slots:
                    state["order"]["pickup_time"] = text
                    state["step"] = "confirm"
                    cart = format_cart(state["order"])
                    summary = (
                        f"Твой заказ:\n\n"
                        f"Точка: {state['order']['point']}\n"
                        f"Время готовности: {text}\n"
                        f"Соус: {state['order']['sauce']}\n"
                        f"Добавки: {', '.join(state['order']['extras']) or 'нет'}\n\n"
                        f"{cart}\n\n"
                        f"Оплата при получении\n\nВсё верно?"
                    )
                    send(vk, user_id, summary, kb_confirm())
                else:
                    send(vk, user_id, "Выбери время из списка:", kb_time(slots))
                continue

            # ПОДТВЕРЖДЕНИЕ
            if step == "confirm":
                if text == "Подтвердить заказ":
                    order_counter += 1
                    order = state["order"]
                    cart = format_cart(order)

                    manager_id = MANAGERS.get(order["point"], ADMIN_VK_ID)
                    notif = (
                        f"НОВЫЙ ЗАКАЗ #{order_counter}\n\n"
                        f"Клиент: {user_name} (id{user_id})\n"
                        f"Точка: {order['point']}\n"
                        f"Время готовности: {order['pickup_time']}\n"
                        f"Соус: {order['sauce']}\n"
                        f"Добавки: {', '.join(order['extras']) or 'нет'}\n\n"
                        f"{cart}\n\nОплата при получении"
                    )
                    try:
                        vk.messages.send(user_id=manager_id, message=notif, random_id=0)
                    except Exception as e:
                        print(f"Ошибка уведомления: {e}")

                    send(vk, user_id,
                        f"Заказ #{order_counter} принят!\n\n"
                        f"Точка: {order['point']}\n"
                        f"Будет готов к {order['pickup_time']}\n"
                        f"Оплата при получении\n\nЖдём тебя!",
                        kb_main())
                    reset_state(user_id)

                elif text == "Начать заново":
                    reset_state(user_id)
                    send(vk, user_id, "Начнём заново:", kb_main())
                else:
                    send(vk, user_id, "Нажми 'Подтвердить заказ' или 'Начать заново':", kb_confirm())
                continue

            send(vk, user_id, "Выбери действие:", kb_main())

if __name__ == "__main__":
    main()
