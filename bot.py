"""
Учебный Telegram-бот: выбор города → категория мерча → вариант → ссылка на оплату.
Заполните .env по образцу .env.example. Запуск: python bot.py
"""
from __future__ import annotations

import os
import re
import threading
import time
from json import loads
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import telebot
from dotenv import load_dotenv
from telebot import types

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")
CHANNEL = os.getenv("CHANNEL_URL", "@your_channel")
SUPPORT = os.getenv("SUPPORT_USERNAME", "@support")
RUB_PER_USD_RAW = os.getenv("RUB_PER_USD", "100")
CRYPTO_WALLET_TRC20 = os.getenv("CRYPTO_WALLET_TRC20", "TNcCnFM6q573qiQksJxnQFAb2cq7mZDDNv")
USD_RUB_API_URL = os.getenv("USD_RUB_API_URL", "https://open.er-api.com/v6/latest/USD")
RATE_CACHE_TTL_SEC_RAW = os.getenv("RATE_CACHE_TTL_SEC", "1800")

if not BOT_TOKEN or not ADMIN_ID_RAW:
    raise SystemExit("Укажите BOT_TOKEN и ADMIN_ID в файле .env (см. .env.example)")

ADMIN_ID = int(ADMIN_ID_RAW)
try:
    RUB_PER_USD = float(RUB_PER_USD_RAW.replace(",", "."))
except ValueError:
    RUB_PER_USD = 100.0
if RUB_PER_USD <= 0:
    RUB_PER_USD = 100.0
try:
    RATE_CACHE_TTL_SEC = int(RATE_CACHE_TTL_SEC_RAW)
except ValueError:
    RATE_CACHE_TTL_SEC = 1800
if RATE_CACHE_TTL_SEC < 60:
    RATE_CACHE_TTL_SEC = 60

WIKIDATA_SPARQL_URL = os.getenv("WIKIDATA_SPARQL_URL", "https://query.wikidata.org/sparql")
DISTRICTS_MAX_RAW = os.getenv("DISTRICTS_MAX", "12")
DISTRICTS_CACHE_TTL_SEC_RAW = os.getenv("DISTRICTS_CACHE_TTL_SEC", "86400")

try:
    DISTRICTS_MAX = int(DISTRICTS_MAX_RAW)
except ValueError:
    DISTRICTS_MAX = 12
if DISTRICTS_MAX <= 0:
    DISTRICTS_MAX = 12

try:
    DISTRICTS_CACHE_TTL_SEC = int(DISTRICTS_CACHE_TTL_SEC_RAW)
except ValueError:
    DISTRICTS_CACHE_TTL_SEC = 86400
if DISTRICTS_CACHE_TTL_SEC < 300:
    DISTRICTS_CACHE_TTL_SEC = 300

bot = telebot.TeleBot(BOT_TOKEN)

_rate_lock = threading.Lock()
_cached_rub_per_usd = RUB_PER_USD
_cached_rate_ts = 0.0

_districts_cache_lock = threading.Lock()
_districts_cache: dict[str, tuple[float, list[str]]] = {}

_user_state_lock = threading.Lock()
# chat_id -> { "city": <base city>, "district": <district label> }
_user_state: dict[int, dict[str, str]] = {}

def fetch_online_rub_per_usd() -> float | None:
    try:
        with urlopen(USD_RUB_API_URL, timeout=5) as response:
            data = loads(response.read().decode("utf-8"))
        rub = data.get("rates", {}).get("RUB")
        if isinstance(rub, (int, float)) and rub > 0:
            return float(rub)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return None
    return None


def get_rub_per_usd() -> float:
    global _cached_rub_per_usd, _cached_rate_ts
    now = time.time()
    with _rate_lock:
        if now - _cached_rate_ts < RATE_CACHE_TTL_SEC:
            return _cached_rub_per_usd
    online_rate = fetch_online_rub_per_usd()
    with _rate_lock:
        if online_rate:
            _cached_rub_per_usd = online_rate
            _cached_rate_ts = now
        return _cached_rub_per_usd


def rub_to_usd(rub: float, rub_per_usd: float | None = None) -> float:
    current_rate = rub_per_usd if rub_per_usd else get_rub_per_usd()
    return rub / current_rate


def format_price_rub_usd(rub: int | float, rub_per_usd: float | None = None) -> str:
    usd = rub_to_usd(float(rub), rub_per_usd)
    # Показываем рубли и рядом конвертацию в доллары
    return f"{int(rub)}₽ (~${usd:.2f})"


CITIES_RAW = [
    "Москва",
    "Воронеж",
    "Норильск",
    "Томск",
    "Краснодар",
    "Красноярск",
    "Иркутск",
    "Улан-Удэ",
    "Бийск",
    "Борисоглебск",
    "Пермь",
    "Екатеринбург",
    "Сургут",
    "Сочи",
    "Ханты-Мансийск",
    "Абакан",
    "Оренбург",
    "Нижний Новгород",
    # Добавленные варианты
    "Московская область",
    "Ленинградская область",
    "Алтайский край",
    "Амурская область",
    "Архангельск",
    "Астрахань",
    "Белгород",
    "Брянск",
    "Владимир",
    "Волгоград",
    "Калининград",
    "Киров",
    "Липецк",
    "Мурманск",
    "Новгород",
    "Новосибирск",
    "Орлов",
    "Пенза",
    "Псков",
    "Ростов",
    "Тамбов",
    "Рязань",
    "Смоленск",
    "Сахалин",
    "Ставрополь",
    "Тверь",
    "Челябинск",
]


CITY_TO_REGION = {
    "Москва": "Москва (город федерального значения)",
    "Воронеж": "Воронежская область",
    "Норильск": "Красноярский край",
    "Томск": "Томская область",
    "Краснодар": "Краснодарский край",
    "Красноярск": "Красноярский край",
    "Иркутск": "Иркутская область",
    "Улан-Удэ": "Республика Бурятия",
    "Бийск": "Алтайский край",
    "Борисоглебск": "Воронежская область",
    "Пермь": "Пермский край",
    "Екатеринбург": "Свердловская область",
    "Сургут": "Ханты-Мансийский автономный округ — Югра",
    "Сочи": "Краснодарский край",
    "Ханты-Мансийск": "Ханты-Мансийский автономный округ — Югра",
    "Абакан": "Республика Хакасия",
    "Оренбург": "Оренбургская область",
    "Нижний Новгород": "Нижегородская область",
    "Архангельск": "Архангельская область",
    "Астрахань": "Астраханская область",
    "Белгород": "Белгородская область",
    "Брянск": "Брянская область",
    "Владимир": "Владимирская область",
    "Волгоград": "Волгоградская область",
    "Калининград": "Калининградская область",
    "Киров": "Кировская область",
    "Липецк": "Липецкая область",
    "Мурманск": "Мурманская область",
    "Новгород": "Новгородская область",
    "Новосибирск": "Новосибирская область",
    "Орлов": "Орловская область",
    "Пенза": "Пензенская область",
    "Псков": "Псковская область",
    "Ростов": "Ростовская область",
    "Тамбов": "Тамбовская область",
    "Рязань": "Рязанская область",
    "Смоленск": "Смоленская область",
    "Сахалин": "Сахалинская область",
    "Ставрополь": "Ставропольский край",
    "Тверь": "Тверская область",
    "Челябинск": "Челябинская область",
}


def make_display_city(item: str) -> str:
    lowered = item.casefold()
    # Если это уже регион/субъект федерации — оставляем как есть.
    if any(
        sub in lowered
        for sub in (
            "область",
            "край",
            "республика",
            "автономный округ",
            "федерального значения",
        )
    ):
        return item
    region = CITY_TO_REGION.get(item)
    if region:
        return f"{item} — {region}"
    return item


def dedup_and_sort_cities(items: list[str], sort_by_display: bool = False) -> list[str]:
    # Дедуп по регистру, затем сортировка по русскому алфавиту (приближенно через Unicode casefold)
    seen: set[str] = set()
    unique: list[str] = []
    for it in items:
        key = it.casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(it)
    if sort_by_display:
        return sorted(unique, key=lambda s: make_display_city(s).casefold())
    return sorted(unique, key=lambda s: s.casefold())


CITIES_BASE = dedup_and_sort_cities(CITIES_RAW, sort_by_display=True)
CITIES = [make_display_city(it) for it in CITIES_BASE]

# Ключ группы: для "городов" берём соответствующую область/край, для "самих областей" — само значение.
CITY_GROUP_KEY = [CITY_TO_REGION.get(it, it) for it in CITIES_BASE]


def fetch_districts_for_city(city_base: str) -> list[str]:
    """
    Берём список "районов" для города из интернета (Wikidata).
    Кэшируем, чтобы не дергать API при каждом клике.
    """
    if not city_base:
        return []

    now = time.time()
    with _districts_cache_lock:
        cached = _districts_cache.get(city_base)
        if cached and now - cached[0] < DISTRICTS_CACHE_TTL_SEC:
            return cached[1]

    city_escaped = city_base.replace("\"", "\\\"")
    # P131: located in; UNION с P361 (part of) на случай различий в данных
    query = f"""
SELECT ?districtLabel WHERE {{
  ?city rdfs:label "{city_escaped}"@ru .
  {{
    ?district wdt:P131 ?city .
  }} UNION {{
    ?district wdt:P361 ?city .
  }}
  ?district wdt:P31/wdt:P279* wd:Q33614 .  # administrative territorial entity
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "ru". }}
}}
LIMIT 200
""".strip()

    params = {"format": "json", "query": query}
    url = f"{WIKIDATA_SPARQL_URL}?{urlencode(params)}"
    req = Request(
        url,
        headers={
            "Accept": "application/sparql+json",
            "User-Agent": "Mozilla/5.0 (Cursor Telegram Bot Builder)",
        },
    )
    districts: list[str] = []
    try:
        with urlopen(req, timeout=8) as response:
            data = loads(response.read().decode("utf-8"))
        bindings = data.get("results", {}).get("bindings", [])
        for b in bindings:
            v = b.get("districtLabel", {}).get("value")
            if isinstance(v, str) and v.strip():
                districts.append(v.strip())
    except Exception as exc:
        print(f"Не удалось загрузить районы для {city_base}: {exc}")
        districts = []

    # Дедуп/сортировка и ограничение
    districts = sorted(set(districts), key=lambda s: s.casefold())[:DISTRICTS_MAX]
    with _districts_cache_lock:
        _districts_cache[city_base] = (now, districts)
    return districts


def get_user_state(chat_id: int) -> dict[str, str]:
    with _user_state_lock:
        st = _user_state.get(chat_id)
        if not st:
            st = {}
            _user_state[chat_id] = st
        return st


def district_keyboard(city_idx: int, districts: list[str]) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup(row_width=2)
    # city_idx в callback_data нужен, чтобы восстановить city_base на втором шаге
    buttons: list[types.InlineKeyboardButton] = []
    for di, d in enumerate(districts):
        buttons.append(types.InlineKeyboardButton(text=d, callback_data=f"r{city_idx}_{di}"))
    # Разложить по 2 в строке
    for i in range(0, len(buttons), 2):
        if i + 1 < len(buttons):
            kb.row(buttons[i], buttons[i + 1])
        else:
            kb.row(buttons[i])
    kb.add(types.InlineKeyboardButton(text="Назад", callback_data="back_main"))
    return kb


# Каталог (цены в ₽; доллары считаются по RUB_PER_USD из .env)
CATALOG = [
    {
        "title": "СК Синее",
        "options": [
            ("0,5", 900),
            ("1", 1200),
            ("1,5", 2200),
        ],
    },
    {
        "title": "gashиш",
        "options": [
            ("0,5", 650),
            ("1", 1100),
            ("1,5", 1500),
        ],
    },
    {
        "title": "gashиш rolex",
        "options": [
            ("0,5", 950),
            ("1", 1350),
        ],
    },
]


def _user_label(message: types.Message) -> str:
    name = (message.chat.first_name or "") + (" " + message.chat.last_name if message.chat.last_name else "")
    name = name.strip() or "Без имени"
    return f"{name} [ {message.chat.id} ]"


def _callback_user_label(call: types.CallbackQuery) -> str:
    u = call.from_user
    name = (u.first_name or "") + (" " + u.last_name if u.last_name else "")
    name = name.strip() or "Без имени"
    cid = call.message.chat.id if call.message else 0
    return f"{name} [ {cid} ]"


def notify_admin(text: str) -> None:
    try:
        bot.send_message(ADMIN_ID, text)
    except Exception as exc:
        print("Не удалось отправить админу:", exc)


def city_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for i in range(0, len(CITIES), 2):
        row_btns = [
            types.InlineKeyboardButton(text=CITIES[i + j], callback_data=f"c{i + j}")
            for j in range(2)
            if i + j < len(CITIES)
        ]
        kb.row(*row_btns)
    return kb


def product_keyboard() -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    for i, line in enumerate(CATALOG):
        kb.add(types.InlineKeyboardButton(text=line["title"], callback_data=f"p{i}"))
    return kb


def option_keyboard(product_index: int) -> types.InlineKeyboardMarkup:
    kb = types.InlineKeyboardMarkup()
    line = CATALOG[product_index]
    current_rate = get_rub_per_usd()
    for j, (label, price) in enumerate(line["options"]):
        text = f"{label} — {format_price_rub_usd(price, current_rate)}"
        kb.add(types.InlineKeyboardButton(text=text, callback_data=f"o{product_index}_{j}"))
    return kb


@bot.message_handler(commands=["start"])
def cmd_start(message: types.Message) -> None:
    notify_admin(f"{_user_label(message)} | /start")
    with _user_state_lock:
        _user_state.pop(message.chat.id, None)
    text = (
        f"Привет, {message.chat.first_name or 'друг'}.\n"
        f"Добро пожаловать в бот kaif.\n"
        f"Инфо-канал: {CHANNEL}\n"
        f"Поддержка: {SUPPORT}\n\n"
        "Выберите город доставки:"
    )
    bot.send_message(message.chat.id, text, reply_markup=city_keyboard())


@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith("c") and call.data[1:].isdigit())
def on_city(call: types.CallbackQuery) -> None:
    idx = int(call.data[1:])
    if idx < 0 or idx >= len(CITIES):
        bot.answer_callback_query(call.id, "Неверный выбор")
        return
    selected_display = CITIES[idx]
    selected_group_key = CITY_GROUP_KEY[idx]
    message = call.message
    if not message:
        bot.answer_callback_query(call.id)
        return

    group_indices = [i for i, gk in enumerate(CITY_GROUP_KEY) if gk == selected_group_key]
    bot.answer_callback_query(call.id)

    group_kb = types.InlineKeyboardMarkup(row_width=2)
    row_buttons: list[types.InlineKeyboardButton] = []
    for gi in group_indices:
        row_buttons.append(types.InlineKeyboardButton(text=CITIES[gi], callback_data=f"m{gi}"))
        if len(row_buttons) == 2:
            group_kb.row(*row_buttons)
            row_buttons = []
    if row_buttons:
        group_kb.row(*row_buttons)

    # Кнопка назад на главный список
    group_kb.row(types.InlineKeyboardButton(text="Назад", callback_data="back_main"))

    bot.send_message(
        message.chat.id,
        f'Вы выбрали: {selected_display}\nВ этой области/крае находятся:\n(выберите город из списка)',
        reply_markup=group_kb,
    )


@bot.callback_query_handler(func=lambda call: call.data == "back_main")
def on_back_main(call: types.CallbackQuery) -> None:
    message = call.message
    if not message:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    bot.send_message(
        message.chat.id,
        "Выберите город доставки:",
        reply_markup=city_keyboard(),
    )


@bot.callback_query_handler(func=lambda call: call.data and re.match(r"^m\d+$", call.data))
def on_group_member(call: types.CallbackQuery) -> None:
    idx = int(call.data[1:])
    if idx < 0 or idx >= len(CITIES):
        bot.answer_callback_query(call.id, "Неверный выбор")
        return
    city_display = CITIES[idx]
    city_base = CITIES_BASE[idx] if idx < len(CITIES_BASE) else CITIES_BASE[0]
    message = call.message
    if not message:
        bot.answer_callback_query(call.id)
        return

    # Сохраняем выбор, чтобы использовать в итоговом сообщении заказа
    st = get_user_state(message.chat.id)
    st["city"] = city_base
    st.pop("district", None)

    notify_admin(f"{_callback_user_label(call)} | Город: {city_display}")
    bot.answer_callback_query(call.id)

    # Для "чистых" регионов (область/край) районы не подбираем — сразу категории
    if city_base not in CITY_TO_REGION:
        bot.send_message(
            message.chat.id,
            f"Вы выбрали город/субъект: {city_display}.\nТеперь выберите категорию:",
            reply_markup=product_keyboard(),
        )
        return

    districts = fetch_districts_for_city(city_base)
    if not districts:
        bot.send_message(
            message.chat.id,
            f"Вы выбрали город: {city_display}.\nРайоны не найдены — показываем категории:",
            reply_markup=product_keyboard(),
        )
        return

    bot.send_message(
        message.chat.id,
        f"Вы выбрали город: {city_display}\nТеперь выберите район:",
        reply_markup=district_keyboard(idx, districts),
    )


@bot.callback_query_handler(func=lambda call: call.data and re.match(r"^r\d+_\d+$", call.data))
def on_district_member(call: types.CallbackQuery) -> None:
    parts = call.data[1:].split("_")
    city_idx = int(parts[0])
    district_idx = int(parts[1])
    if city_idx < 0 or city_idx >= len(CITIES_BASE):
        bot.answer_callback_query(call.id, "Неверный выбор")
        return
    city_base = CITIES_BASE[city_idx]
    message = call.message
    if not message:
        bot.answer_callback_query(call.id)
        return

    districts = fetch_districts_for_city(city_base)
    if district_idx < 0 or district_idx >= len(districts):
        bot.answer_callback_query(call.id, "Неверный выбор")
        return

    district = districts[district_idx]
    st = get_user_state(message.chat.id)
    st["district"] = district

    bot.answer_callback_query(call.id)
    notify_admin(f"{_callback_user_label(call)} | Район: {district}")
    city_display = CITIES[city_idx] if city_idx < len(CITIES) else city_base
    bot.send_message(
        message.chat.id,
        f"Вы выбрали город: {city_display}\nРайон: {district}\nТеперь выберите категорию:",
        reply_markup=product_keyboard(),
    )


@bot.callback_query_handler(func=lambda call: call.data and re.match(r"^p\d+$", call.data))
def on_product(call: types.CallbackQuery) -> None:
    idx = int(call.data[1:])
    if idx < 0 or idx >= len(CATALOG):
        bot.answer_callback_query(call.id, "Неверный выбор")
        return
    message = call.message
    if not message:
        bot.answer_callback_query(call.id)
        return
    title = CATALOG[idx]["title"]
    notify_admin(f"{_callback_user_label(call)} | Категория: {title}")
    bot.answer_callback_query(call.id)
    bot.send_message(
        message.chat.id,
        f"Вы выбрали: {title}.\nВыберите вариант:",
        reply_markup=option_keyboard(idx),
    )


@bot.callback_query_handler(func=lambda call: call.data and re.match(r"^o\d+_\d+$", call.data))
def on_option(call: types.CallbackQuery) -> None:
    parts = call.data[1:].split("_")
    pi, oi = int(parts[0]), int(parts[1])
    if pi < 0 or pi >= len(CATALOG):
        bot.answer_callback_query(call.id, "Ошибка")
        return
    opts = CATALOG[pi]["options"]
    if oi < 0 or oi >= len(opts):
        bot.answer_callback_query(call.id, "Ошибка")
        return
    label, price = opts[oi]
    title = CATALOG[pi]["title"]
    message = call.message
    if not message:
        bot.answer_callback_query(call.id)
        return
    uid = message.chat.id
    current_rate = get_rub_per_usd()
    usd = rub_to_usd(float(price), current_rate)
    with _user_state_lock:
        st = _user_state.get(uid, {})
    city = st.get("city")
    district = st.get("district")
    location_tail = ""
    if city:
        location_tail += f" | Город: {city}"
    if district:
        location_tail += f" | Район: {district}"
    notify_admin(
        f"{_callback_user_label(call)} | К оплате: {title} — {label} ({format_price_rub_usd(price, current_rate)}){location_tail}"
    )
    bot.answer_callback_query(call.id)
    location_lines = ""
    if city:
        location_lines += f"Город: {city}\n"
    if district:
        location_lines += f"Район: {district}\n"
    summary = (
        f"Заказ: {title}, {label}\n"
        f"К оплате: {format_price_rub_usd(price, current_rate)}\n"
        f"(онлайн-курс: 1 USD = {current_rate:g} ₽)\n"
        f"{location_lines}"
        f"Комментарий к платежу (укажите при оплате): {uid}"
    )
    bot.send_message(message.chat.id, summary)
    crypto_block = (
        "Крипта (USDT TRC20 / TRON):\n"
        f"`{CRYPTO_WALLET_TRC20}`\n"
        "Проверьте сеть: TRC20 (TRON), не путать с ERC20."
    )
    bot.send_message(message.chat.id, crypto_block, parse_mode="Markdown")
    paid_kb = types.InlineKeyboardMarkup()
    paid_kb.add(types.InlineKeyboardButton(text="Я оплатил", callback_data="paid"))
    bot.send_message(
        message.chat.id,
        'После оплаты нажми кнопку "Я оплатил".',
        reply_markup=paid_kb,
    )


@bot.message_handler(content_types=["text"])
def on_text(message: types.Message) -> None:
    notify_admin(f"{_user_label(message)} | Написал: {message.text}")
    bot.send_message(
        message.chat.id,
        "Чтобы начать заново, нажмите /start",
    )


@bot.callback_query_handler(func=lambda call: call.data == "paid")
def on_paid(call: types.CallbackQuery) -> None:
    chat_id = call.message.chat.id if call.message else None
    if not chat_id:
        bot.answer_callback_query(call.id)
        return
    bot.answer_callback_query(call.id)
    # Показать ожидание сразу, а затем отправить follow-up через 2 минуты.
    bot.send_message(chat_id, 'Подождите, платёж обрабатывается')

    def _follow_up() -> None:
        time.sleep(70)
        # Без интеграции с платежным провайдером/блокчейн-проверкой бот не может
        # гарантированно подтвердить платеж автоматически.
        try:
            bot.send_message(
                chat_id,
                "Мы не можем автоматически подтвердить платеж без TXID/ссылки на транзакцию. "
                "Пришлите хэш (TXID) или ссылку на перевод — и мы проверим вручную.",
            )
        except Exception as exc:
            print("Не удалось отправить follow-up после оплаты:", exc)

    threading.Thread(target=_follow_up, daemon=True).start()


if __name__ == "__main__":
    print("Бот запущен. Остановка: Ctrl+C")
    bot.infinity_polling(skip_pending=True)
