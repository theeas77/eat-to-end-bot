"""
VK-бот для предзаказов Eat to End
Требования: pip install vkbottle
"""

import asyncio
import json
import datetime
from vkbottle.bot import Bot, Message
from vkbottle import Keyboard, KeyboardButtonColor, Text, Callback

# ==================== НАСТРОЙКИ ====================
VK_TOKEN = "ВАШ_ТОКЕН_ГРУППЫ_ВК"
ADMIN_VK_ID = 0  # ВАШ VK ID для уведомлений (число)

# Менеджеры точек: VK ID менеджера для каждой точки
MANAGERS = {
    "Ленина 36/2": 0,       # замените на VK ID менеджера
    "Промышленная 13": 0,
    "Советская 2/10": 0,
}

# ==================== МЕНЮ ====================
MENU = {
    "🌯 Шаурма": {
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
    "🔥 Шашлык": {
        "Шашлык из курицы": 360,
        "Шашлык из свинины": 395,
    },
    "🥤 Напитки": {
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

SAUCES = ["Фирменный", "BBQ", "Острый", "Сырный", "Медово-горчичный"]

EXTRAS = {
    "Сыр тертый": 42, "Огурцы соленые": 42, "Морковка по-корейски": 42,
    "Красный лук": 42, "Лук фри": 42, "Халапеньо": 42, "Бекон": 42,
    "Ананасы": 42, "Оливки": 42, "Перец болгарский": 42, "Соус доп.": 42,
    "Курица доп.": 77, "Свинина доп.": 77,
}

# Временные слоты (каждые 15 минут, 10:00–22:00)
def get_time_slots():
    slots = []
    start = datetime.time(10, 0)
    end = datetime.time(22, 0)
    current = datetime.datetime.combine(datetime.date.today(), start)
    end_dt = datetime.datetime.combine(datetime.date.today(), end)
    now = datetime.datetime.now() + datetime.timedelta(minutes=20)  # минимум 20 мин на приготовление
    while current <= end_dt:
        if current > now:
            slots.append(current.strftime("%H:%M"))
        current += datetime.timedelta(minutes=15)
    return slots[:16]  # максимум 16 слотов в клавиатуре

# ==================== GOOGLE SHEETS ====================
def get_sheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDENTIALS_FILE, scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open(GOOGLE_SHEET_NAME).sheet1
    except gspread.SpreadsheetNotFound:
        spreadsheet = client.create(GOOGLE_SHEET_NAME)
        sheet = spreadsheet.sheet1
        sheet.append_row(["№", "Дата", "Время заказа", "Время готовности", "Точка", "VK ID", "Имя", "Заказ", "Соус", "Добавки", "Сумма", "Статус"])
    return sheet

def save_order_to_sheet(order: dict) -> int:
    sheet = get_sheet()
    all_rows = sheet.get_all_values()
    order_num = len(all_rows)  # номер заказа = кол-во строк включая заголовок

    items_str = "; ".join([f"{k} x{v}" for k, v in order["items"].items()])
    extras_str = ", ".join(order.get("extras", [])) or "—"
    sauce_str = order.get("sauce", "—")

    sheet.append_row([
        order_num,
        datetime.datetime.now().strftime("%d.%m.%Y"),
        datetime.datetime.now().strftime("%H:%M"),
        order.get("pickup_time", "—"),
        order.get("point", "—"),
        order.get("user_id", "—"),
        order.get("user_name", "—"),
        items_str,
        sauce_str,
        extras_str,
        order.get("total", 0),
        "Новый",
    ])
    return order_num

# ==================== СОСТОЯНИЯ ПОЛЬЗОВАТЕЛЕЙ ====================
# Хранится в памяти. При перезапуске бота сбрасывается — для продакшна используйте Redis
user_states = {}  # user_id -> {"step": ..., "order": {...}}

def get_state(user_id: int) -> dict:
    if user_id not in user_states:
        user_states[user_id] = {"step": "start", "order": {"items": {}, "extras": [], "sauce": None}}
    return user_states[user_id]

def reset_state(user_id: int):
    user_states[user_id] = {"step": "start", "order": {"items": {}, "extras": [], "sauce": None}}

# ==================== КЛАВИАТУРЫ ====================
def kb_main():
    kb = Keyboard(one_time=False)
    kb.add(Text("🛒 Сделать предзаказ", {"cmd": "order"}), color=KeyboardButtonColor.POSITIVE)
    kb.row()
    kb.add(Text("📋 Мои заказы", {"cmd": "my_orders"}))
    kb.add(Text("ℹ️ О боте", {"cmd": "about"}))
    return kb

def kb_points():
    kb = Keyboard(one_time=True)
    for point in MANAGERS.keys():
        kb.add(Text(f"📍 {point}"))
        kb.row()
    kb.add(Text("❌ Отмена"))
    return kb

def kb_categories():
    kb = Keyboard(one_time=True)
    for cat in MENU.keys():
        kb.add(Text(cat))
        kb.row()
    kb.add(Text("✅ Корзина и далее"))
    kb.row()
    kb.add(Text("❌ Отмена"))
    return kb

def kb_items(category: str):
    kb = Keyboard(one_time=True)
    items = MENU[category]
    for name, price in items.items():
        kb.add(Text(f"{name} — {price}₽"))
        kb.row()
    kb.add(Text("◀️ Назад к категориям"))
    return kb

def kb_sauces():
    kb = Keyboard(one_time=True)
    for sauce in SAUCES:
        kb.add(Text(sauce))
        kb.row()
    kb.add(Text("Без соуса"))
    return kb

def kb_extras():
    kb = Keyboard(one_time=True)
    for extra, price in list(EXTRAS.items())[:6]:  # первые 6 для примера
        kb.add(Text(f"+ {extra} (+{price}₽)"))
        kb.row()
    kb.add(Text("✅ Без добавок / готово"))
    return kb

def kb_time_slots():
    slots = get_time_slots()
    kb = Keyboard(one_time=True)
    for i, slot in enumerate(slots):
        kb.add(Text(f"⏰ {slot}"))
        if (i + 1) % 3 == 0:
            kb.row()
    kb.row()
    kb.add(Text("❌ Отмена"))
    return kb

def kb_confirm():
    kb = Keyboard(one_time=True)
    kb.add(Text("✅ Подтвердить заказ"), color=KeyboardButtonColor.POSITIVE)
    kb.row()
    kb.add(Text("🗑 Очистить и начать заново"), color=KeyboardButtonColor.NEGATIVE)
    return kb

# ==================== БОТ ====================
bot = Bot(token=VK_TOKEN)

def format_cart(order: dict) -> str:
    if not order["items"]:
        return "Корзина пуста"
    lines = []
    total = 0
    for item, qty in order["items"].items():
        # Найти цену
        price = 0
        for cat in MENU.values():
            for name, p in cat.items():
                if name in item or item in name:
                    price = p
                    break
        lines.append(f"• {item} x{qty} = {price * qty}₽")
        total += price * qty
    for extra in order.get("extras", []):
        extra_price = EXTRAS.get(extra.replace("+ ", "").split(" (+")[0], 42)
        lines.append(f"• {extra} = {extra_price}₽")
        total += extra_price
    order["total"] = total
    return "\n".join(lines) + f"\n\n💰 Итого: {total}₽"

@bot.on.message()
async def handle(message: Message):
    user_id = message.from_id
    text = message.text.strip()
    state = get_state(user_id)
    step = state["step"]

    # Получить имя пользователя
    user_info = await bot.api.users.get(user_ids=[user_id])
    user_name = f"{user_info[0].first_name} {user_info[0].last_name}" if user_info else "Пользователь"

    # ---- СТАРТ / ГЛАВНОЕ МЕНЮ ----
    if text in ["Начать", "/start", "start", "🏠 Главная", "❌ Отмена"] or step == "start":
        if text == "❌ Отмена":
            reset_state(user_id)
        state = get_state(user_id)
        state["step"] = "main"
        await message.answer(
            "👋 Привет! Это бот предзаказов Eat to End.\n\nВыбери действие:",
            keyboard=kb_main()
        )
        return

    # ---- НАЧАЛО ЗАКАЗА ----
    if text == "🛒 Сделать предзаказ" or (step == "main" and "заказ" in text.lower()):
        state["step"] = "choose_point"
        await message.answer("📍 Выбери точку самовывоза:", keyboard=kb_points())
        return

    # ---- ВЫБОР ТОЧКИ ----
    if step == "choose_point":
        for point in MANAGERS.keys():
            if point in text:
                state["order"]["point"] = point
                state["step"] = "choose_category"
                await message.answer(
                    f"✅ Точка: {point}\n\nВыбери категорию блюд:",
                    keyboard=kb_categories()
                )
                return
        await message.answer("Пожалуйста, выбери точку из списка:", keyboard=kb_points())
        return

    # ---- ВЫБОР КАТЕГОРИИ ----
    if step == "choose_category":
        if text == "✅ Корзина и далее":
            if not state["order"]["items"]:
                await message.answer("Корзина пуста! Добавь хотя бы одну позицию.", keyboard=kb_categories())
                return
            state["step"] = "choose_sauce"
            await message.answer("🥫 Выбери соус:", keyboard=kb_sauces())
            return

        for cat in MENU.keys():
            if cat in text:
                state["step"] = "choose_item"
                state["current_category"] = cat
                await message.answer(f"Выбери позицию из «{cat}»:", keyboard=kb_items(cat))
                return

        await message.answer("Выбери категорию из меню:", keyboard=kb_categories())
        return

    # ---- ВЫБОР ПОЗИЦИИ ----
    if step == "choose_item":
        if text == "◀️ Назад к категориям":
            state["step"] = "choose_category"
            cart_text = format_cart(state["order"])
            await message.answer(f"🛒 Корзина:\n{cart_text}\n\nВыбери категорию:", keyboard=kb_categories())
            return

        # Ищем совпадение с позицией меню
        cat = state.get("current_category", "")
        found = False
        for name, price in MENU.get(cat, {}).items():
            if name in text:
                if name in state["order"]["items"]:
                    state["order"]["items"][name] += 1
                else:
                    state["order"]["items"][name] = 1
                found = True
                cart_text = format_cart(state["order"])
                await message.answer(
                    f"✅ Добавлено: {name}\n\n🛒 Корзина:\n{cart_text}\n\nДобавить ещё или перейти к оформлению?",
                    keyboard=kb_items(cat)
                )
                break

        if not found:
            await message.answer("Выбери позицию из списка:", keyboard=kb_items(cat))
        return

    # ---- ВЫБОР СОУСА ----
    if step == "choose_sauce":
        if text in SAUCES or text == "Без соуса":
            state["order"]["sauce"] = text
            state["step"] = "choose_extras"
            await message.answer("➕ Хочешь добавки? (можно несколько):", keyboard=kb_extras())
            return
        await message.answer("Выбери соус:", keyboard=kb_sauces())
        return

    # ---- ДОБАВКИ ----
    if step == "choose_extras":
        if text == "✅ Без добавок / готово":
            state["step"] = "choose_time"
            slots = get_time_slots()
            if not slots:
                await message.answer("⚠️ К сожалению, мы уже закрыты. Приходи завтра с 10:00!")
                reset_state(user_id)
                return
            await message.answer("⏰ На какое время сделать заказ?", keyboard=kb_time_slots())
            return

        # Добавка
        for extra_name in EXTRAS.keys():
            if extra_name in text:
                if extra_name not in state["order"]["extras"]:
                    state["order"]["extras"].append(extra_name)
                await message.answer(
                    f"✅ Добавлено: {extra_name}\nЕщё добавки или «Без добавок / готово»:",
                    keyboard=kb_extras()
                )
                return

        await message.answer("Выбери добавку или нажми «Без добавок / готово»:", keyboard=kb_extras())
        return

    # ---- ВРЕМЯ ГОТОВНОСТИ ----
    if step == "choose_time":
        if ":" in text:
            time_val = text.replace("⏰ ", "").strip()
            state["order"]["pickup_time"] = time_val
            state["order"]["user_id"] = user_id
            state["order"]["user_name"] = user_name
            state["step"] = "confirm"

            cart_text = format_cart(state["order"])
            summary = (
                f"📋 Твой заказ:\n\n"
                f"📍 Точка: {state['order']['point']}\n"
                f"⏰ Время: {time_val}\n"
                f"🥫 Соус: {state['order']['sauce']}\n"
                f"➕ Добавки: {', '.join(state['order']['extras']) or '—'}\n\n"
                f"{cart_text}\n\n"
                f"💳 Оплата при получении\n\n"
                f"Всё верно?"
            )
            await message.answer(summary, keyboard=kb_confirm())
            return
        await message.answer("Выбери время из списка:", keyboard=kb_time_slots())
        return

    # ---- ПОДТВЕРЖДЕНИЕ ----
    if step == "confirm":
        if text == "✅ Подтвердить заказ":
            try:
                order_num = save_order_to_sheet(state["order"])
                order = state["order"]

                # Уведомление менеджеру точки
                manager_id = MANAGERS.get(order["point"], ADMIN_VK_ID)
                if manager_id:
                    cart_text = format_cart(order)
                    notif = (
                        f"🆕 НОВЫЙ ЗАКАЗ #{order_num}\n\n"
                        f"👤 {user_name} (vk.com/id{user_id})\n"
                        f"📍 {order['point']}\n"
                        f"⏰ Готовность: {order['pickup_time']}\n"
                        f"🥫 Соус: {order['sauce']}\n"
                        f"➕ Добавки: {', '.join(order['extras']) or '—'}\n\n"
                        f"{cart_text}"
                    )
                    await bot.api.messages.send(
                        user_id=manager_id,
                        message=notif,
                        random_id=0
                    )

                await message.answer(
                    f"🎉 Заказ #{order_num} принят!\n\n"
                    f"📍 {order['point']}\n"
                    f"⏰ Будет готов к {order['pickup_time']}\n"
                    f"💳 Оплата при получении\n\n"
                    f"Ждём тебя! 🌯",
                    keyboard=kb_main()
                )
                reset_state(user_id)
            except Exception as e:
                await message.answer(f"⚠️ Ошибка при сохранении заказа. Попробуй ещё раз.\n{e}")
            return

        if text == "🗑 Очистить и начать заново":
            reset_state(user_id)
            await message.answer("Заказ сброшен. Начнём заново:", keyboard=kb_main())
            return

        await message.answer("Нажми «Подтвердить заказ» или «Очистить»:", keyboard=kb_confirm())
        return

    # ---- О БОТЕ ----
    if "О боте" in text or "о боте" in text:
        await message.answer(
            "🌯 Eat to End — предзаказ шаурмы\n\n"
            "Наши точки:\n"
            "📍 Ленина 36/2 с 2\n"
            "📍 Промышленная 13\n"
            "📍 Советская 2/10 с 1\n\n"
            "Режим работы: 10:00 – 22:00\n"
            "Оплата при получении.",
            keyboard=kb_main()
        )
        return

    # ---- МОИ ЗАКАЗЫ (заглушка) ----
    if "заказы" in text.lower():
        await message.answer("📋 История заказов будет доступна в следующей версии.", keyboard=kb_main())
        return

    # Дефолт
    await message.answer("Выбери действие:", keyboard=kb_main())

if __name__ == "__main__":
    print("Бот запущен...")
    bot.run_forever()
