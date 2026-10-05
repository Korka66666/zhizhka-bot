import os
import json
import asyncio
import re
import html
import aiohttp
import datetime
from collections import Counter
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

BOT_TOKEN = "8737856125:AAErSYpfhMQp6WjeN3oZ-gaxP3z-Wr4JgV8"
ADMIN_CHAT_ID = 8651846848
WEBAPP_URL = "https://regal-parfait-e29c47.netlify.app"
GAS_URL = "https://script.google.com/macros/s/AKfycbyDk-sDPisni6TJ4R14SEzh5W765oSpj0-3PuqE0PeLGkbSW3XahP82Q64XuFHKgGTQ/exec"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ORDERS_FILE = "orders_data.json"
GAS_TIMEOUT_SECONDS = 25


# =============================================================
# LOCAL ORDER STORAGE
# =============================================================

def load_orders():
    if os.path.exists(ORDERS_FILE):
        try:
            with open(ORDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"⚠ Ошибка чтения {ORDERS_FILE}: {e}")
            return {}
    return {}


def save_orders(data):
    try:
        tmp_file = ORDERS_FILE + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_file, ORDERS_FILE)
    except Exception as e:
        print(f"⚠ Ошибка сохранения {ORDERS_FILE}: {e}")


orders_db = load_orders()
orders_lock = asyncio.Lock()


# =============================================================
# COMMON HELPERS
# =============================================================

def json_dump(data):
    return json.dumps(data, ensure_ascii=False)


def http_json_response(data, status=200):
    return web.Response(
        status=status,
        text=json_dump(data),
        content_type="application/json"
    )


def clean_text(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def safe_int(value, default=0):
    try:
        if isinstance(value, bool):
            return int(value)
        return int(float(value))
    except (TypeError, ValueError):
        return default


def get_item_variant(item):
    return clean_text(
        item.get("variant") or
        item.get("cleanName") or
        item.get("name")
    )


def get_item_title(item):
    return clean_text(item.get("title"), "Товар")


def make_gas_item(item, qty=None):
    """
    Формат позиции для Google Apps Script.

    ВАЖНО:
    - id добавляется только если это реальный ID товара,
      переданный отдельным полем id.
    - productId сайта специально НЕ используется как id,
      потому что это внутренний индекс карточки сайта, а не ID строки Google.
    """
    result = {
        "title": get_item_title(item),
        "variant": get_item_variant(item),
        "qty": safe_int(
            qty if qty is not None else item.get("qty", 1),
            1
        )
    }

    real_id = clean_text(item.get("id"))
    if real_id:
        result["id"] = real_id

    return result


async def gas_post(payload):
    """
    Один общий POST в Google Apps Script.

    Проверяет и HTTP-ошибку, и JSON-статус GAS.
    """
    timeout = aiohttp.ClientTimeout(
        total=GAS_TIMEOUT_SECONDS
    )

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                GAS_URL,
                json=payload,
                allow_redirects=True
            ) as resp:
                text = await resp.text()

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    raise RuntimeError(
                        f"Google Apps Script вернул некорректный ответ (HTTP {resp.status})"
                    )

                if resp.status >= 400:
                    raise RuntimeError(
                        data.get("message") or
                        f"Ошибка Google Apps Script: HTTP {resp.status}"
                    )

                if data.get("status") == "error":
                    raise RuntimeError(
                        data.get("message") or
                        "Google Apps Script отклонил операцию"
                    )

                return data

    except asyncio.TimeoutError:
        raise RuntimeError(
            "Google Таблица слишком долго отвечает. Повторите попытку."
        )
    except aiohttp.ClientError as e:
        raise RuntimeError(
            f"Ошибка соединения с Google Таблицей: {e}"
        )


def build_admin_keyboard_from_lines(lines):
    bullet_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("•")
    ]

    if not bullet_indices:
        return None

    kb = [[
        InlineKeyboardButton(
            text="✅ Подтвердить / Заказ выдан",
            callback_data="order_done"
        )
    ]]

    for btn_idx, line_idx in enumerate(bullet_indices):
        line_text = lines[line_idx]

        m = re.search(
            r"\[(?:Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт",
            line_text
        )

        if m:
            var_name = html.unescape(m.group(1).strip())
            qty = int(m.group(2))

            btn_title = (
                f"🔙 Вернуть 1 шт: {var_name} ({qty} шт)"
                if qty > 1
                else f"🔙 Вернуть на склад: {var_name}"
            )

            kb.append([
                InlineKeyboardButton(
                    text=btn_title,
                    callback_data=f"ret1_{btn_idx}"
                )
            ])

    kb.append([
        InlineKeyboardButton(
            text="❌ Отменить заказ полностью",
            callback_data="ret_all"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=kb)


# =============================================================
# TELEGRAM COMMANDS
# =============================================================

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = message.from_user.id

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚡ Открыть витрину ЖИЖКА",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]
    ])

    if user_id == ADMIN_CHAT_ID:
        await message.answer(
            f"👑 <b>АВТОРИЗОВАН КАК ГЛАВНЫЙ АДМИНИСТРАТОР!</b>\n\n"
            f"🆔 Твой ID: <code>{user_id}</code>\n"
            f"Все заказы, доставка, подтверждения и возвраты подключены сюда.",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        await message.answer(
            f"👋 <b>Добро пожаловать в шоп ЖИЖКА!</b>\n\n"
            f"Жми кнопку ниже, чтобы собрать заказ:",
            reply_markup=kb,
            parse_mode="HTML"
        )


@dp.message()
async def any_text_handler(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="⚡ Открыть витрину ЖИЖКА",
            web_app=WebAppInfo(url=WEBAPP_URL)
        )]
    ])

    await message.answer(
        "Жми кнопку ниже, чтобы перейти в витрину шопа ЖИЖКА 👇",
        reply_markup=kb
    )


# =============================================================
# WEBSITE API — GET ORDERS
# =============================================================

async def handle_get_orders(request):
    user_id = clean_text(
        request.query.get("user_id")
    )

    if not user_id:
        return http_json_response([])

    async with orders_lock:
        user_orders = list(
            orders_db.get(user_id, [])
        )

    return http_json_response(user_orders)


# =============================================================
# WEBSITE API — DELETE ORDER
# =============================================================

async def handle_order_delete(request):
    try:
        data = await request.json()

        order_id = clean_text(
            data.get("order_id")
        )
        admin_message_id = clean_text(
            data.get("admin_message_id")
        )
        user_id = clean_text(
            data.get("user_id")
        )

        if not user_id:
            return http_json_response({
                "status": "error",
                "message": "Не указан Telegram ID покупателя"
            }, 400)

        if not order_id and not admin_message_id:
            return http_json_response({
                "status": "error",
                "message": "Не указан ID заказа"
            }, 400)

        found = False

        async with orders_lock:
            user_orders = orders_db.get(user_id, [])

            for idx, order in enumerate(user_orders):
                same_order = (
                    order_id and
                    str(order.get("id")) == order_id
                ) or (
                    admin_message_id and
                    str(order.get("admin_message_id")) == admin_message_id
                )

                if same_order:
                    user_orders.pop(idx)
                    found = True
                    break

            if found:
                save_orders(orders_db)

        if not found:
            return http_json_response({
                "status": "ok",
                "message": "Заказ уже отсутствует"
            })

        return http_json_response({
            "status": "ok"
        })

    except Exception as e:
        print(f"❌ Ошибка удаления заказа: {e}")
        return http_json_response({
            "status": "error",
            "message": str(e)
        }, 500)


# =============================================================
# WEBSITE API — NEW ORDER
# =============================================================

async def handle_order_post(request):
    try:
        data = await request.json()

        items = data.get("items", [])
        total = safe_int(data.get("total"), 0)
        user_info = data.get("user") or {}
        delivery_type = clean_text(
            data.get("delivery_type"),
            "pickup"
        ).lower()
        delivery_addr = html.escape(
            clean_text(
                data.get("delivery_address")
            )
        )

        if not isinstance(items, list) or not items:
            return http_json_response({
                "status": "error",
                "message": "Корзина пуста"
            }, 400)

        if delivery_type not in {"pickup", "delivery"}:
            delivery_type = "pickup"

        client_id = safe_int(
            user_info.get("id"),
            0
        )

        if client_id <= 0:
            return http_json_response({
                "status": "error",
                "message":
                    "Не удалось определить Telegram ID покупателя"
            }, 400)

        raw_username = clean_text(
            user_info.get("username")
        )
        username_str = (
            f"@{raw_username}"
            if raw_username
            else "Не указан"
        )

        client_name = html.escape(
            clean_text(
                user_info.get("name"),
                "Покупатель"
            )
        )

        # ------------------------------------------------------
        # Группировка одинаковых позиций.
        # ------------------------------------------------------
        counts = Counter()

        for item in items:
            if not isinstance(item, dict):
                continue

            title = get_item_title(item)
            variant = get_item_variant(item)

            if not variant:
                continue

            price = safe_int(
                item.get("price"),
                0
            )

            type_label = clean_text(
                item.get("typeLabel"),
                "Вкус"
            )

            real_id = clean_text(
                item.get("id")
            )

            counts[
                (
                    real_id,
                    title,
                    variant,
                    price,
                    type_label
                )
            ] += 1

        if not counts:
            return http_json_response({
                "status": "error",
                "message": "В заказе нет корректных позиций"
            }, 400)

        items_for_gas = []
        order_lines = []
        clean_items_list = []

        for (
            real_id,
            title,
            variant,
            price,
            type_label
        ), count in counts.items():

            gas_item = {
                "title": title,
                "variant": variant,
                "qty": count
            }

            if real_id:
                gas_item["id"] = real_id

            items_for_gas.append(gas_item)

            order_lines.append(
                f"• <b>{html.escape(title)}</b> "
                f"[{html.escape(type_label)}: "
                f"{html.escape(variant)}] — "
                f"{count} шт. по {price} ₽"
            )

            for _ in range(count):
                item_entry = {
                    "title": title,
                    "variant": variant,
                    "price": price,
                    "typeLabel": type_label
                }

                if real_id:
                    item_entry["id"] = real_id

                clean_items_list.append(
                    item_entry
                )

        # ------------------------------------------------------
        # Списание. Google сам атомарно проверяет остатки
        # под LockService ДО изменения таблицы.
        # ------------------------------------------------------
        try:
            await gas_post({
                "action": "deduct",
                "items": items_for_gas
            })
        except Exception as gas_error:
            return http_json_response({
                "status": "error",
                "message": str(gas_error)
            }, 400)

        items_text = "\n".join(
            order_lines
        )

        if delivery_type == "delivery":
            delivery_text = (
                "🚗 <b>Способ:</b> Доставка (150–400 ₽)\n"
                f"📍 <b>Адрес:</b> "
                f"{delivery_addr or 'Уточнить при связи'}"
            )
        else:
            delivery_text = (
                "🏬 <b>Способ:</b> Самовывоз"
            )

        admin_msg = (
            "🚨 <b>НОВЫЙ ЗАКАЗ В ШОПЕ «ЖИЖКА»!</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} "
            f"({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"{delivery_text}\n\n"
            f"📦 <b>Состав заказа:</b>\n"
            f"{items_text}\n\n"
            f"💵 <b>Итого за товары:</b> {total} ₽"
        )

        admin_markup = build_admin_keyboard_from_lines(
                admin_msg.split("\n")
            )

        # ------------------------------------------------------
        # Если Telegram не принял сообщение,
        # возвращаем остатки обратно.
        # ------------------------------------------------------
        try:
            sent_admin_msg = await bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=admin_msg,
                reply_markup=admin_markup,
                parse_mode="HTML"
            )
        except Exception as telegram_error:
            try:
                await gas_post({
                    "action": "add",
                    "items": items_for_gas
                })
                rollback_note = " Остатки возвращены в таблицу."
            except Exception as rollback_error:
                rollback_note = (
                    " ВНИМАНИЕ: автоматический возврат остатков "
                    f"не удался: {rollback_error}"
                )

            print(
                "❌ Не удалось отправить новый заказ администратору: "
                f"{telegram_error}.{rollback_note}"
            )

            return http_json_response({
                "status": "error",
                "message":
                    "Не удалось отправить заказ администратору."
                    + rollback_note
            }, 500)

        order_id = int(
            datetime.datetime.now().timestamp() * 1000
        )

        order_entry = {
            "id": order_id,
            "admin_message_id":
                sent_admin_msg.message_id,
            "date": datetime.datetime.now().strftime("%H:%M"),
            "items": clean_items_list,
            "total": total,
            "delivery_type": delivery_type,
            "delivery_address": delivery_addr,
            "status": "active"
        }

        str_cid = str(client_id)

        async with orders_lock:
            if str_cid not in orders_db:
                orders_db[str_cid] = []

            orders_db[str_cid].insert(
                0,
                order_entry
            )

            save_orders(orders_db)

        # ------------------------------------------------------
        # Чек клиенту.
        # ------------------------------------------------------
        if client_id != ADMIN_CHAT_ID:
            try:
                buyer_delivery = (
                    "🚗 Доставка: 150–400 ₽ "
                    "(администратор согласует точную сумму)"
                    if delivery_type == "delivery"
                    else "🏬 Самовывоз"
                )

                buyer_msg = (
                    "✅ <b>Ваш заказ успешно оформлен!</b>\n\n"
                    f"{buyer_delivery}\n"
                    f"📦 <b>Товары:</b>\n{items_text}\n\n"
                    f"💵 <b>Сумма:</b> {total} ₽\n\n"
                    "Администратор уже получил заявку и свяжется с вами."
                )

                await bot.send_message(
                    chat_id=client_id,
                    text=buyer_msg,
                    parse_mode="HTML"
                )

            except Exception as buyer_error:
                print(
                    "⚠ Не удалось отправить чек покупателю: "
                    f"{buyer_error}"
                )

        print(
            f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
            f"Новый заказ от {client_name} на сумму {total} ₽"
        )

        return http_json_response({
            "status": "ok",
            "admin_message_id":
                sent_admin_msg.message_id
        })

    except Exception as e:
        print(
            f"❌ Ошибка в handle_order_post: {e}"
        )

        return http_json_response({
            "status": "error",
            "message": str(e)
        }, 500)


# =============================================================
# WEBSITE API — EDIT ORDER
# =============================================================

async def handle_order_edit(request):
    try:
        data = await request.json()

        admin_message_id = safe_int(
            data.get("admin_message_id"),
            0
        )

        old_items = data.get("old_items", [])
        new_items = data.get("new_items", [])
        total = safe_int(
            data.get("total"),
            0
        )
        user_info = data.get("user") or {}
        delivery_type = clean_text(
            data.get("delivery_type"),
            "pickup"
        ).lower()
        delivery_addr = html.escape(
            clean_text(
                data.get("delivery_address")
            )
        )

        if admin_message_id <= 0:
            return http_json_response({
                "status": "error",
                "message": "Не указан admin_message_id"
            }, 400)

        if not isinstance(old_items, list):
            old_items = []

        if not isinstance(new_items, list):
            new_items = []

        if not new_items:
            return http_json_response({
                "status": "error",
                "message": "Новый заказ не должен быть пустым"
            }, 400)

        if delivery_type not in {"pickup", "delivery"}:
            delivery_type = "pickup"

        client_id = safe_int(
            user_info.get("id"),
            0
        )

        if client_id <= 0:
            return http_json_response({
                "status": "error",
                "message":
                    "Не удалось определить Telegram ID покупателя"
            }, 400)

        raw_username = clean_text(
            user_info.get("username")
        )
        username_str = (
            f"@{raw_username}"
            if raw_username
            else "Не указан"
        )

        client_name = html.escape(
            clean_text(
                user_info.get("name"),
                "Покупатель"
            )
        )

        # ------------------------------------------------------
        # Проверяем, что этот заказ действительно принадлежит
        # указанному Telegram-пользователю.
        # ------------------------------------------------------
        str_cid = str(client_id)
        existing_order = None

        async with orders_lock:
            for order in orders_db.get(str_cid, []):
                if safe_int(
                    order.get("admin_message_id"),
                    0
                ) == admin_message_id:
                    existing_order = order
                    break

        if existing_order is None:
            return http_json_response({
                "status": "error",
                "message": "Активный заказ не найден"
            }, 404)

        # ------------------------------------------------------
        # Подготовка старых и новых остатков.
        #
        # НОВЫЙ GAS action=adjust делает всё атомарно:
        # вернуть старое + списать новое.
        # ------------------------------------------------------
        old_counts = Counter()
        new_counts = Counter()

        old_stock_items = []
        new_stock_items = []

        for item in old_items:
            if not isinstance(item, dict):
                continue

            variant = get_item_variant(item)
            title = get_item_title(item)

            if not variant:
                continue

            key = (
                clean_text(item.get("id")),
                title,
                variant
            )

            old_counts[key] += 1

        for item in new_items:
            if not isinstance(item, dict):
                continue

            variant = get_item_variant(item)
            title = get_item_title(item)

            if not variant:
                continue

            key = (
                clean_text(item.get("id")),
                title,
                variant
            )

            new_counts[key] += 1

        # Формируем возврат старых позиций.
        for (
            real_id,
            title,
            variant
        ), count in old_counts.items():

            item = {
                "title": title,
                "variant": variant,
                "qty": count
            }

            if real_id:
                item["id"] = real_id

            old_stock_items.append(item)

        # Формируем списание новых позиций.
        for (
            real_id,
            title,
            variant
        ), count in new_counts.items():

            item = {
                "title": title,
                "variant": variant,
                "qty": count
            }

            if real_id:
                item["id"] = real_id

            new_stock_items.append(item)

        # ------------------------------------------------------
        # Атомарная корректировка остатков.
        # ------------------------------------------------------
        try:
            await gas_post({
                "action": "adjust",
                "add_items": old_stock_items,
                "deduct_items": new_stock_items
            })
        except Exception as gas_error:
            return http_json_response({
                "status": "error",
                "message": str(gas_error)
            }, 400)

        # ------------------------------------------------------
        # Формируем новую карточку заказа.
        # ------------------------------------------------------
        new_counts_for_display = Counter()

        for item in new_items:
            if not isinstance(item, dict):
                continue

            title = get_item_title(item)
            variant = get_item_variant(item)

            if not variant:
                continue

            price = safe_int(
                item.get("price"),
                0
            )

            type_label = clean_text(
                item.get("typeLabel"),
                "Вкус"
            )

            real_id = clean_text(
                item.get("id")
            )

            new_counts_for_display[
                (
                    real_id,
                    title,
                    variant,
                    price,
                    type_label
                )
            ] += 1

        order_lines = []
        clean_new_items = []

        for (
            real_id,
            title,
            variant,
            price,
            type_label
        ), count in new_counts_for_display.items():

            order_lines.append(
                f"• <b>{html.escape(title)}</b> "
                f"[{html.escape(type_label)}: "
                f"{html.escape(variant)}] — "
                f"{count} шт. по {price} ₽"
            )

            for _ in range(count):
                entry = {
                    "title": title,
                    "variant": variant,
                    "price": price,
                    "typeLabel": type_label
                }

                if real_id:
                    entry["id"] = real_id

                clean_new_items.append(entry)

        items_text = "\n".join(
            order_lines
        )

        if delivery_type == "delivery":
            delivery_text = (
                "🚗 <b>Способ:</b> Доставка (150–400 ₽)\n"
                f"📍 <b>Адрес:</b> "
                f"{delivery_addr or 'Уточнить при связи'}"
            )
        else:
            delivery_text = (
                "🏬 <b>Способ:</b> Самовывоз"
            )

        updated_msg = (
            "🔄 <b>ЗАКАЗ ИЗМЕНЕН ПОКУПАТЕЛЕМ!</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} "
            f"({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"{delivery_text}\n\n"
            f"📦 <b>Актуальный состав заказа:</b>\n"
            f"{items_text}\n\n"
            f"💵 <b>Новый итог:</b> {total} ₽"
        )

        admin_markup = build_admin_keyboard_from_lines(
                updated_msg.split("\n")
            )

        # ------------------------------------------------------
        # Если Telegram не удалось обновить,
        # откатываем остатки к старому состоянию.
        # ------------------------------------------------------
        try:
            await bot.edit_message_text(
                chat_id=ADMIN_CHAT_ID,
                message_id=admin_message_id,
                text=updated_msg,
                reply_markup=admin_markup,
                parse_mode="HTML"
            )
        except Exception as telegram_error:

            try:
                # Откат: вернуть новые + снова списать старые.
                await gas_post({
                    "action": "adjust",
                    "add_items": new_stock_items,
                    "deduct_items": old_stock_items
                })
                rollback_note = " Остатки возвращены к старому состоянию."
            except Exception as rollback_error:
                rollback_note = (
                    " ВНИМАНИЕ: откат остатков не удался: "
                    f"{rollback_error}"
                )

            print(
                "❌ Ошибка обновления сообщения админа: "
                f"{telegram_error}.{rollback_note}"
            )

            return http_json_response({
                "status": "error",
                "message":
                    "Не удалось обновить заказ у администратора."
                    + rollback_note
            }, 500)

        # ------------------------------------------------------
        # Обновление локальной базы.
        # ------------------------------------------------------
        async with orders_lock:
            for order in orders_db.get(str_cid, []):
                if safe_int(
                    order.get("admin_message_id"),
                    0
                ) == admin_message_id:
                    order["items"] = clean_new_items
                    order["total"] = total
                    order["delivery_type"] = delivery_type
                    order["delivery_address"] = delivery_addr
                    break

            save_orders(orders_db)

        return http_json_response({
            "status": "ok"
        })

    except Exception as e:
        print(
            f"❌ Ошибка в handle_order_edit: {e}"
        )

        return http_json_response({
            "status": "error",
            "message": str(e)
        }, 500)


# =============================================================
# TELEGRAM ADMIN — ORDER DONE
# =============================================================

@dp.callback_query(F.data == "order_done")
async def handle_order_done(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer(
            "❌ Только администратор!",
            show_alert=True
        )

    buyer_id = None
    found = False

    async with orders_lock:
        for uid, user_orders in orders_db.items():
            for order in user_orders:
                if safe_int(
                    order.get("admin_message_id"),
                    0
                ) == call.message.message_id:
                    order["status"] = "completed"
                    buyer_id = uid
                    found = True
                    break
            if found:
                break

        if found:
            save_orders(orders_db)

    if not found:
        return await call.answer(
            "⚠ Заказ уже закрыт или не найден.",
            show_alert=True
        )

    orig_html = (
        call.message.html_text or
        call.message.text or
        ""
    )

    new_text = (
        orig_html +
        "\n\n🎉 <b>ЗАКАЗ УСПЕШНО ВЫПОЛНЕН И ВЫДАН!</b>"
    )

    try:
        await call.message.edit_text(
            new_text,
            reply_markup=None,
            parse_mode="HTML"
        )
    except Exception as e:
        print(
            f"⚠ Ошибка обновления завершенного заказа: {e}"
        )

    await call.answer(
        "✅ Заказ подтвержден и закрыт!"
    )

    if buyer_id and buyer_id != str(ADMIN_CHAT_ID):
        try:
            await bot.send_message(
                chat_id=int(buyer_id),
                text=(
                    "🎉 <b>Ваш заказ успешно выдан!</b>\n"
                    "Спасибо за покупку в шопе ЖИЖКА! "
                    "Ждем вас снова."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            print(
                f"⚠ Не удалось уведомить покупателя: {e}"
            )


# =============================================================
# TELEGRAM ADMIN — RETURN ONE ITEM
# =============================================================

@dp.callback_query(F.data.startswith("ret1_"))
async def handle_return_one(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer(
            "❌ Только администратор!",
            show_alert=True
        )

    try:
        target_btn_idx = int(
            call.data.split("_", 1)[1]
        )
    except (IndexError, ValueError):
        return await call.answer(
            "Ошибка идентификатора позиции",
            show_alert=True
        )

    raw_text = (
        call.message.html_text or
        call.message.text or
        ""
    )

    lines = raw_text.split("\n")

    bullet_indices = [
        i for i, line in enumerate(lines)
        if line.strip().startswith("•")
    ]

    if target_btn_idx >= len(bullet_indices):
        return await call.answer(
            "Позиция уже возвращена!",
            show_alert=True
        )

    line_idx = bullet_indices[target_btn_idx]

    line_text = lines[line_idx]

    pattern = (
        r"•\s*(?:<b>)?(.*?)(?:</b>)?\s*"
        r"\[(Вкус|Цвет):\s*(.*?)\]\s*"
        r"—\s*(\d+)\s*шт\.\s*по\s*(\d+)"
    )

    match = re.search(
        pattern,
        line_text
    )

    if not match:
        return await call.answer(
            "Ошибка парсинга строки",
            show_alert=True
        )

    title = html.unescape(
        match.group(1).strip()
    )
    type_label = match.group(2).strip()
    variant = html.unescape(
        match.group(3).strip()
    )
    qty = int(match.group(4))
    price = int(match.group(5))

    if qty <= 0:
        return await call.answer(
            "Позиция уже возвращена!",
            show_alert=True
        )

    # ----------------------------------------------------------
    # Ищем текущий заказ в БД.
    # ----------------------------------------------------------
    target_order = None

    async with orders_lock:
        for uid, user_orders in orders_db.items():
            for order in user_orders:
                if safe_int(
                    order.get("admin_message_id"),
                    0
                ) == call.message.message_id:
                    order_owner = uid
                    target_order = order
                    break
            if target_order is not None:
                break

    if target_order is None:
        return await call.answer(
            "Заказ не найден в базе.",
            show_alert=True
        )

    # ----------------------------------------------------------
    # Возвращаем ровно 1 шт в Google.
    # title + variant -> конкретный товар.
    # ----------------------------------------------------------
    try:
        await gas_post({
            "action": "add",
            "items": [{
                "title": title,
                "variant": variant,
                "qty": 1
            }]
        })
    except Exception as gas_error:
        return await call.answer(
            f"❌ Не удалось вернуть товар: {gas_error}",
            show_alert=True
        )

    new_qty = qty - 1

    if new_qty > 0:
        lines[line_idx] = (
            f"• <b>{html.escape(title)}</b> "
            f"[{html.escape(type_label)}: "
            f"{html.escape(variant)}] — "
            f"{new_qty} шт. по {price} ₽"
        )
    else:
        lines[line_idx] = (
            f"❌ <s><b>{html.escape(title)}</b> "
            f"[{html.escape(type_label)}: "
            f"{html.escape(variant)}] — "
            f"1 шт.</s> (Возвращено)"
        )

    # ----------------------------------------------------------
    # Корректируем сумму.
    # ----------------------------------------------------------
    total_pattern = (
        r"((?:<b>)?"
        r"(?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого)"
        r":?(?:</b>)?:?\s*)(\d+)"
    )

    total_match = re.search(
        total_pattern,
        raw_text
    )

    if total_match:
        old_total = int(
            total_match.group(2)
        )
        new_total = max(
            0,
            old_total - price
        )

        lines = [
            re.sub(
                total_pattern,
                rf"\g<1>{new_total}",
                line
            )
            for line in lines
        ]

    # ----------------------------------------------------------
    # Корректируем конкретный заказ.
    # Учитываем title + variant, а не только variant.
    # ----------------------------------------------------------
    async with orders_lock:
        order_items = target_order.get("items", [])
        removed = False

        for idx, item in enumerate(order_items):
            item_title = get_item_title(item)
            item_variant = get_item_variant(item)

            if (
                item_title == title and
                item_variant == variant
            ):
                order_items.pop(idx)
                removed = True
                break

        if removed:
            target_order["items"] = order_items
            target_order["total"] = max(
                0,
                safe_int(
                    target_order.get("total"),
                    0
                ) - price
            )

            if (
                not target_order["items"] or
                target_order["total"] == 0
            ):
                target_order["status"] = "canceled"

            save_orders(orders_db)

    new_kb = build_admin_keyboard_from_lines(lines)

    try:
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=new_kb,
            parse_mode="HTML"
        )
    except Exception as telegram_error:
        print(
            "⚠ Не удалось обновить сообщение после возврата: "
            f"{telegram_error}"
        )

        # В этом редком случае остаток уже вернулся в таблицу,
        # поэтому операция экономически выполнена. Сообщение
        # админа можно обновить повторно вручную.

    await call.answer(
        f"✅ 1 шт. ({variant}) возвращена на склад!"
    )


# =============================================================
# TELEGRAM ADMIN — RETURN ALL
# =============================================================

@dp.callback_query(F.data == "ret_all")
async def handle_return_all(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer(
            "❌ Только администратор!",
            show_alert=True
        )

    raw_text = (
        call.message.html_text or
        call.message.text or
        ""
    )

    lines = raw_text.split("\n")
    items_to_return = []
    parsed_items = []

    pattern = (
        r"•\s*(?:<b>)?(.*?)(?:</b>)?\s*"
        r"\[(Вкус|Цвет):\s*(.*?)\]\s*"
        r"—\s*(\d+)\s*шт"
    )

    for idx, line in enumerate(lines):
        if not line.strip().startswith("•"):
            continue

        match = re.search(
            pattern,
            line
        )

        if not match:
            continue

        title = html.unescape(
            match.group(1).strip()
        )
        type_label = match.group(2).strip()
        variant = html.unescape(
            match.group(3).strip()
        )
        qty = int(match.group(4))

        if qty <= 0:
            continue

        items_to_return.append({
            "title": title,
            "variant": variant,
            "qty": qty
        })

        parsed_items.append({
            "line_idx": idx,
            "title": title,
            "typeLabel": type_label,
            "variant": variant,
            "qty": qty
        })

        lines[idx] = (
            re.sub(
                r"^•\s*",
                "❌ <s>",
                line
            ) +
            "</s> (Отменено)"
        )

    if not items_to_return:
        return await call.answer(
            "Позиции для возврата не найдены.",
            show_alert=True
        )

    # ----------------------------------------------------------
    # Находим заказ до изменения Google.
    # ----------------------------------------------------------
    target_order = None
    buyer_id = None

    async with orders_lock:
        for uid, user_orders in orders_db.items():
            for order in user_orders:
                if safe_int(
                    order.get("admin_message_id"),
                    0
                ) == call.message.message_id:
                    target_order = order
                    buyer_id = uid
                    break
            if target_order is not None:
                break

    if target_order is None:
        return await call.answer(
            "Заказ не найден в базе.",
            show_alert=True
        )

    # ----------------------------------------------------------
    # Возвращаем ВСЕ позиции одним запросом и одной блокировкой.
    # ----------------------------------------------------------
    try:
        await gas_post({
            "action": "add",
            "items": items_to_return
        })
    except Exception as gas_error:
        return await call.answer(
            f"❌ Не удалось вернуть товары: {gas_error}",
            show_alert=True
        )

    total_pattern = (
        r"((?:<b>)?"
        r"(?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого)"
        r":?(?:</b>)?:?\s*)\d+"
    )

    lines = [
        re.sub(
            total_pattern,
            r"\g<1>0",
            line
        )
        for line in lines
    ]

    lines.append(
        "\n❌ <b>ЗАКАЗ ПОЛНОСТЬЮ АННУЛИРОВАН</b>"
    )

    # ----------------------------------------------------------
    # Обновляем локальную БД.
    # ----------------------------------------------------------
    async with orders_lock:
        target_order["status"] = "canceled"
        target_order["total"] = 0
        target_order["items"] = []
        save_orders(orders_db)

    try:
        await call.message.edit_text(
            "\n".join(lines),
            reply_markup=None,
            parse_mode="HTML"
        )
    except Exception as telegram_error:
        print(
            f"⚠ Ошибка обновления отмененного заказа: {telegram_error}"
        )

    await call.answer(
        "✅ Весь заказ отменен, остатки возвращены!"
    )

    if buyer_id and buyer_id != str(ADMIN_CHAT_ID):
        try:
            await bot.send_message(
                chat_id=int(buyer_id),
                text=(
                    "❌ <b>Ваш заказ был отменен администратором.</b>\n"
                    "Товары возвращены на склад."
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            print(
                f"⚠ Не удалось уведомить покупателя: {e}"
            )


# =============================================================
# CORS
# =============================================================

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(
            headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods":
                    "POST, GET, OPTIONS, DELETE, PUT",
                "Access-Control-Allow-Headers":
                    "Content-Type, Authorization, X-Requested-With"
            }
        )

    response = await handler(request)

    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = (
        "POST, GET, OPTIONS, DELETE, PUT"
    )
    response.headers["Access-Control-Allow-Headers"] = (
        "Content-Type, Authorization, X-Requested-With"
    )

    return response


async def handle_ping(request):
    return web.Response(text="Bot is running!")


async def start_web_server():
    app = web.Application(
        middlewares=[cors_middleware]
    )

    app.router.add_get(
        "/",
        handle_ping
    )
    app.router.add_get(
        "/orders",
        handle_get_orders
    )
    app.router.add_post(
        "/order",
        handle_order_post
    )
    app.router.add_post(
        "/order/edit",
        handle_order_edit
    )
    app.router.add_post(
        "/order/delete",
        handle_order_delete
    )

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(
        os.environ.get(
            "PORT",
            8080
        )
    )

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        port
    )

    await site.start()

    print(
        f"🚀 Веб-сервер запущен на порту {port}"
    )


# =============================================================
# MAIN
# =============================================================

async def main():
    await start_web_server()

    while True:
        try:
            await bot.delete_webhook(
                drop_pending_updates=True
            )

            print(
                "🤖 Бот шопа ЖИЖКА запущен и слушает Telegram..."
            )

            await dp.start_polling(
                bot,
                handle_signals=False
            )

        except Exception as e:
            print(
                "⚠ Сбой соединения с Telegram: "
                f"{e}. Рестарт через 5 сек..."
            )

            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
