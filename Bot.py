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

BOT_TOKEN = "8896574305:AAFwFgiKWKh004XR76VL98YBNQtm_yFkGJg"
ADMIN_CHAT_ID = 8651846848
WEBAPP_URL = "https://regal-parfait-e29c47.netlify.app"
GAS_URL = "https://script.google.com/macros/s/AKfycbyDk-sDPisni6TJ4R14SEzh5W765oSpj0-3PuqE0PeLGkbMkSW3XahP82Q64XuFHKgGTQ/exec"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ORDERS_FILE = "orders_data.json"

def load_orders():
    if os.path.exists(ORDERS_FILE):
        try:
            with open(ORDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"⚠ Ошибка чтения {ORDERS_FILE}: {e}")
            return {}
    return {}

def save_orders(data):
    try:
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠ Ошибка сохранения {ORDERS_FILE}: {e}")

orders_db = load_orders()

# Вспомогательный поиск заказа по ID или admin_message_id
def find_order_in_db(order_id, client_id=None):
    str_oid = str(order_id)
    if client_id and str(client_id) in orders_db:
        for o in orders_db[str(client_id)]:
            if str(o.get("id")) == str_oid or str(o.get("admin_message_id")) == str_oid:
                return str(client_id), o
    for uid, user_orders in orders_db.items():
        for o in user_orders:
            if str(o.get("id")) == str_oid or str(o.get("admin_message_id")) == str_oid:
                return uid, o
    return None, None

# Резервная клавиатура для Telegram (если понадобится)
def build_admin_keyboard_from_lines(lines):
    bullet_indices = [i for i, l in enumerate(lines) if l.strip().startswith("•")]
    if not bullet_indices:
        return None

    kb = [[InlineKeyboardButton(text="✅ Подтвердить / Заказ выдан", callback_data="order_done")]]
    for btn_idx, line_idx in enumerate(bullet_indices):
        line_text = lines[line_idx]
        m = re.search(r"\[(?:Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт", line_text)
        if m:
            var_name = html.unescape(m.group(1).strip())
            qty = int(m.group(2))
            btn_title = f"🔙 Вернуть 1 шт: {var_name} ({qty} шт)" if qty > 1 else f"🔙 Вернуть на склад: {var_name}"
            kb.append([InlineKeyboardButton(text=btn_title, callback_data=f"ret1_{btn_idx}")])
    kb.append([InlineKeyboardButton(text="❌ Отменить заказ полностью", callback_data="ret_all")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

# Команда /start
@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    if user_id == ADMIN_CHAT_ID:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Открыть витрину (Каталог)", web_app=WebAppInfo(url=WEBAPP_URL))],
            [InlineKeyboardButton(text="👑 Панель управления заказами", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
        await message.answer(
            f"👑 <b>АВТОРИЗОВАН КАК ГЛАВНЫЙ АДМИНИСТРАТОР!</b>\n\n"
            f"🆔 Твой ID: <code>{user_id}</code>\n"
            f"Все заказы, склад и панель управления активны.\n"
            f"Для работы с заказами нажми кнопку ниже 👇",
            reply_markup=kb,
            parse_mode="HTML"
        )
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚡ Открыть витрину", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])
        await message.answer(
            f"👋 <b>Добро пожаловать!</b>\n\n"
            f"Жми кнопку ниже, чтобы открыть каталог товаров и оформить заказ:",
            reply_markup=kb,
            parse_mode="HTML"
        )

# Автоответчик на текстовые сообщения
@dp.message()
async def any_text_handler(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Открыть витрину", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer("Жми кнопку ниже, чтобы перейти в витрину магазина 👇", reply_markup=kb)

# --- AIOHTTP WEB-ОБРАБОТЧИКИ (СИНХРОНИЗАЦИЯ С САЙТОМ) ---

# 1. Отдача активных заказов покупателю
async def handle_get_orders(request):
    user_id = request.query.get("user_id", "")
    user_orders = orders_db.get(str(user_id), [])
    return web.Response(
        text=json.dumps(user_orders),
        content_type="application/json"
    )

# 2. Удаление заказа покупателем из истории
async def handle_order_delete(request):
    try:
        data = await request.json()
        order_id = str(data.get("order_id", ""))
        user_id = str(data.get("user_id", ""))
        
        found = False
        for uid, user_orders in orders_db.items():
            if not user_id or uid == user_id:
                for idx, o in enumerate(user_orders):
                    if str(o.get("id")) == order_id or str(o.get("admin_message_id")) == order_id:
                        user_orders.pop(idx)
                        found = True
                        break
        if found:
            save_orders(orders_db)
        return web.Response(text=json.dumps({"status": "ok"}), content_type="application/json")
    except Exception as e:
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json")

# 3. Прием нового заказа с витрины
async def handle_order_post(request):
    try:
        data = await request.json()
        items = data.get("items", [])
        total = data.get("total", 0)
        user_info = data.get("user", {})
        delivery_type = data.get("delivery_type", "pickup")
        delivery_addr = html.escape(data.get("delivery_address", "").strip())

        client_id = user_info.get("id") or 0
        raw_username = user_info.get("username")
        username_str = f"@{raw_username}" if raw_username else "Не указан"
        client_name = html.escape(user_info.get("name") or "Покупатель")

        # Группировка товаров
        counts = Counter()
        for it in items:
            t = it.get("title", "Товар")
            v = it.get("variant") or it.get("cleanName") or "Стандарт"
            p = int(it.get("price") or 0)
            tl = it.get("typeLabel") or "Вкус"
            counts[(t, v, p, tl)] += 1

        items_for_gas = []
        clean_items_list = []

        for (t, v, p, tl), count in counts.items():
            items_for_gas.append({"title": t, "variant": v, "qty": count})
            for _ in range(count):
                clean_items_list.append({"title": t, "variant": v, "price": p, "typeLabel": tl})

        # Списание остатков в Google Таблице (Two-Phase Commit)
        async with aiohttp.ClientSession() as session:
            async with session.post(GAS_URL, json={"action": "deduct", "items": items_for_gas}, allow_redirects=True) as resp:
                resp_text = await resp.text()
                try:
                    gas_res = json.loads(resp_text)
                    if gas_res.get("status") == "error":
                        return web.Response(
                            status=400,
                            text=json.dumps({"status": "error", "message": gas_res.get("message", "Товар закончился")}),
                            content_type="application/json"
                        )
                except Exception:
                    pass

        order_id = int(datetime.datetime.now().timestamp() * 1000)
        order_short_id = str(order_id)[-4:]

        if delivery_type == "delivery":
            delivery_text = f"🚗 <b>Способ:</b> Доставка (150–400 ₽)\n📍 <b>Адрес:</b> {delivery_addr or 'Уточнить при связи'}"
            buyer_delivery = f"🚗 <b>Доставка:</b> 150–400 ₽ (адрес: {delivery_addr or 'Уточнить при связи'})"
        else:
            delivery_text = "🏬 <b>Способ:</b> Самовывоз"
            buyer_delivery = "🏬 <b>Способ получения:</b> Самовывоз"

        # БЕЗОПАСНОЕ УВЕДОМЛЕНИЕ АДМИНИСТРАТОРУ (без названий запрещенных товаров в открытом чате)
        admin_msg = (
            f"🔔 <b>НОВЫЙ ЗАКАЗ #{order_short_id}!</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} ({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"{delivery_text}\n\n"
            f"💵 <b>Сумма к оплате:</b> {total} ₽\n"
            f"📦 <b>Позиций в заказе:</b> {len(clean_items_list)} шт.\n\n"
            f"👑 <i>Полный состав заказа и кнопки управления (возврат, выдача, отмена) доступны во вкладке «Админка» внутри витрины.</i>"
        )

        admin_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="👑 Открыть панель управления", web_app=WebAppInfo(url=WEBAPP_URL))]
        ])

        sent_admin_msg = await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_msg,
            reply_markup=admin_kb,
            parse_mode="HTML"
        )

        order_entry = {
            "id": order_id,
            "admin_message_id": sent_admin_msg.message_id,
            "date": datetime.datetime.now().strftime("%H:%M"),
            "created_at": datetime.datetime.now().strftime("%d.%m %H:%M"),
            "user": {
                "id": client_id,
                "name": client_name,
                "username": raw_username or ""
            },
            "items": clean_items_list,
            "total": total,
            "delivery_type": delivery_type,
            "delivery_address": delivery_addr,
            "status": "active"
        }

        str_cid = str(client_id)
        if str_cid not in orders_db:
            orders_db[str_cid] = []
        orders_db[str_cid].insert(0, order_entry)
        save_orders(orders_db)

        # БЕЗОПАСНЫЙ СТЕЛС-ЧЕК ПОКУПАТЕЛЮ В TELEGRAM
        if client_id and client_id != ADMIN_CHAT_ID:
            try:
                buyer_msg = (
                    f"✅ <b>Ваш заказ #{order_short_id} успешно оформлен!</b>\n\n"
                    f"{buyer_delivery}\n"
                    f"💵 <b>Сумма:</b> {total} ₽\n\n"
                    f"📦 <i>Полный состав и актуальный статус заказа доступны внутри витрины во вкладке «Активные заказы».</i>\n\n"
                    f"Администратор уже получил заявку и свяжется с вами."
                )
                await bot.send_message(chat_id=client_id, text=buyer_msg, parse_mode="HTML")
            except Exception:
                pass

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Новый заказ #{order_short_id} от {client_name} на {total} ₽")

        return web.Response(
            text=json.dumps({"status": "ok", "admin_message_id": sent_admin_msg.message_id, "order_id": order_id}),
            content_type="application/json"
        )
    except Exception as e:
        print(f"❌ Ошибка в handle_order_post: {e}")
        return web.Response(
            status=500,
            text=json.dumps({"status": "error", "message": str(e)}),
            content_type="application/json"
        )

# 4. Редактирование заказа покупателем с витрины
async def handle_order_edit(request):
    try:
        data = await request.json()
        admin_message_id = data.get("admin_message_id")
        old_items = data.get("old_items", [])
        new_items = data.get("new_items", [])
        total = data.get("total", 0)
        user_info = data.get("user", {})
        delivery_type = data.get("delivery_type", "pickup")
        delivery_addr = html.escape(data.get("delivery_address", "").strip())

        client_id = user_info.get("id") or 0
        username_str = f"@{user_info.get('username')}" if user_info.get("username") else "Не указан"
        client_name = html.escape(user_info.get("name") or "Покупатель")

        old_counts = Counter((it.get("variant") or it.get("cleanName")) for it in old_items)
        new_counts = Counter((it.get("variant") or it.get("cleanName")) for it in new_items)

        to_add = []
        to_deduct = []
        for v in set(old_counts.keys()).union(set(new_counts.keys())):
            if not v:
                continue
            diff = new_counts[v] - old_counts[v]
            if diff > 0:
                to_deduct.append({"variant": v, "qty": diff})
            elif diff < 0:
                to_add.append({"variant": v, "qty": abs(diff)})

        async with aiohttp.ClientSession() as session:
            if to_add:
                await session.post(GAS_URL, json={"action": "add", "items": to_add}, allow_redirects=True)
            if to_deduct:
                await session.post(GAS_URL, json={"action": "deduct", "items": to_deduct}, allow_redirects=True)

        counts = Counter()
        for it in new_items:
            t = it.get("title", "Товар")
            v = it.get("variant") or it.get("cleanName") or "Стандарт"
            p = int(it.get("price") or 0)
            tl = it.get("typeLabel") or "Вкус"
            counts[(t, v, p, tl)] += 1

        clean_new_items = []
        for (t, v, p, tl), count in counts.items():
            for _ in range(count):
                clean_new_items.append({"title": t, "variant": v, "price": p, "typeLabel": tl})

        # Обновление в базе
        str_cid = str(client_id)
        if str_cid in orders_db:
            for o in orders_db[str_cid]:
                if o.get("admin_message_id") == admin_message_id:
                    o["items"] = clean_new_items
                    o["total"] = total
                    o["delivery_type"] = delivery_type
                    o["delivery_address"] = delivery_addr
            save_orders(orders_db)

        # Безопасное уведомление админу об изменении
        if admin_message_id:
            try:
                updated_admin_text = (
                    f"🔄 <b>ЗАКАЗ ОБНОВЛЕН ПОКУПАТЕЛЕМ!</b>\n\n"
                    f"👤 <b>Покупатель:</b> {client_name} ({html.escape(username_str)})\n"
                    f"🆔 ID: <code>{client_id}</code>\n\n"
                    f"💵 <b>Новая сумма:</b> {total} ₽\n"
                    f"📦 <b>Позиций:</b> {len(clean_new_items)} шт.\n\n"
                    f"👑 <i>Откройте панель администратора для проверки.</i>"
                )
                admin_kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👑 Открыть панель управления", web_app=WebAppInfo(url=WEBAPP_URL))]
                ])
                await bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=admin_message_id,
                    text=updated_admin_text,
                    reply_markup=admin_kb,
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"⚠ Ошибка обновления сообщения админа: {e}")

        return web.Response(text=json.dumps({"status": "ok"}), content_type="application/json")
    except Exception as e:
        print(f"❌ Ошибка в handle_order_edit: {e}")
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json")


# --- ЭНДПОИНТЫ ПАНЕЛИ УПРАВЛЕНИЯ (АДМИНКА ВНУТРИ WEBAPP) ---

# 5. Получение всех заказов для админки
async def handle_admin_get_orders(request):
    admin_id = request.query.get("admin_id") or request.query.get("user_id")
    if str(admin_id) != str(ADMIN_CHAT_ID):
        return web.Response(status=403, text=json.dumps({"status": "error", "message": "Доступ запрещен"}), content_type="application/json")

    all_orders = []
    for uid, user_orders in orders_db.items():
        for order in user_orders:
            o_copy = dict(order)
            if "user" not in o_copy or not o_copy["user"]:
                o_copy["user"] = {"id": uid, "name": "Покупатель", "username": ""}
            all_orders.append(o_copy)

    # Сортировка: новые заказы сверху
    all_orders.sort(key=lambda x: x.get("id", 0), reverse=True)
    return web.Response(text=json.dumps(all_orders), content_type="application/json")

# 6. Поштучный возврат позиции из админки WebApp
async def handle_admin_return_one(request):
    try:
        data = await request.json()
        admin_id = str(data.get("admin_id", ""))
        if admin_id != str(ADMIN_CHAT_ID):
            return web.Response(status=403, text=json.dumps({"status": "error", "message": "Доступ запрещен"}), content_type="application/json")

        order_id = data.get("order_id")
        client_id = data.get("client_id")
        variant = str(data.get("variant", "")).strip()
        title = str(data.get("title", "")).strip()
        price = int(data.get("price") or 0)

        if not variant:
            return web.Response(status=400, text=json.dumps({"status": "error", "message": "Не указан вариант для возврата"}), content_type="application/json")

        # 1. Возврат в Google Таблицу (action: add)
        gas_item = {"variant": variant, "qty": 1}
        if title:
            gas_item["title"] = title

        async with aiohttp.ClientSession() as session:
            await session.post(GAS_URL, json={"action": "add", "items": [gas_item]}, allow_redirects=True)

        # 2. Обновление заказа в базе
        uid, order = find_order_in_db(order_id, client_id)
        if not order:
            return web.Response(status=404, text=json.dumps({"status": "error", "message": "Заказ не найден"}), content_type="application/json")

        # Удаляем ровно 1 экземпляр товара
        found_item = False
        for it in order.get("items", []):
            it_var = str(it.get("variant") or it.get("cleanName") or "").strip()
            if it_var.lower() == variant.lower():
                order["items"].remove(it)
                found_item = True
                break

        order["total"] = max(0, int(order.get("total", 0)) - price)
        if len(order.get("items", [])) == 0 or order["total"] == 0:
            order["status"] = "canceled"

        save_orders(orders_db)
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Админ вернул 1 шт. ({variant}) по заказу #{order_id}")

        return web.Response(text=json.dumps({"status": "ok", "order": order}), content_type="application/json")
    except Exception as e:
        print(f"❌ Ошибка в handle_admin_return_one: {e}")
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json")

# 7. Полная отмена заказа из админки WebApp
async def handle_admin_return_all(request):
    try:
        data = await request.json()
        admin_id = str(data.get("admin_id", ""))
        if admin_id != str(ADMIN_CHAT_ID):
            return web.Response(status=403, text=json.dumps({"status": "error", "message": "Доступ запрещен"}), content_type="application/json")

        order_id = data.get("order_id")
        client_id = data.get("client_id")

        uid, order = find_order_in_db(order_id, client_id)
        if not order:
            return web.Response(status=404, text=json.dumps({"status": "error", "message": "Заказ не найден"}), content_type="application/json")

        # Собираем все позиции для возврата в Таблицу
        items_counts = Counter()
        for it in order.get("items", []):
            t = it.get("title", "")
            v = it.get("variant") or it.get("cleanName") or ""
            if v:
                items_counts[(t, v)] += 1

        items_to_gas = [{"title": t, "variant": v, "qty": q} for (t, v), q in items_counts.items()]

        if items_to_gas:
            async with aiohttp.ClientSession() as session:
                await session.post(GAS_URL, json={"action": "add", "items": items_to_gas}, allow_redirects=True)

        order["status"] = "canceled"
        order["total"] = 0
        save_orders(orders_db)

        # Оповещение покупателю
        buyer_uid = uid or client_id
        if buyer_uid and str(buyer_uid) != str(ADMIN_CHAT_ID):
            try:
                await bot.send_message(
                    chat_id=int(buyer_uid),
                    text="❌ <b>Ваш заказ был отменен администратором.</b>\nЕсли у вас есть вопросы, свяжитесь с поддержкой.",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Админ полностью аннулировал заказ #{order_id}")
        return web.Response(text=json.dumps({"status": "ok", "order": order}), content_type="application/json")
    except Exception as e:
        print(f"❌ Ошибка в handle_admin_return_all: {e}")
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json")

# 8. Завершение заказа (выдача) из админки WebApp
async def handle_admin_complete(request):
    try:
        data = await request.json()
        admin_id = str(data.get("admin_id", ""))
        if admin_id != str(ADMIN_CHAT_ID):
            return web.Response(status=403, text=json.dumps({"status": "error", "message": "Доступ запрещен"}), content_type="application/json")

        order_id = data.get("order_id")
        client_id = data.get("client_id")

        uid, order = find_order_in_db(order_id, client_id)
        if not order:
            return web.Response(status=404, text=json.dumps({"status": "error", "message": "Заказ не найден"}), content_type="application/json")

        order["status"] = "completed"
        save_orders(orders_db)

        # Оповещение покупателю
        buyer_uid = uid or client_id
        if buyer_uid and str(buyer_uid) != str(ADMIN_CHAT_ID):
            try:
                await bot.send_message(
                    chat_id=int(buyer_uid),
                    text="🎉 <b>Ваш заказ успешно выдан!</b>\nСпасибо за покупку. Ждем вас снова!",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Админ завершил заказ #{order_id}")
        return web.Response(text=json.dumps({"status": "ok", "order": order}), content_type="application/json")
    except Exception as e:
        print(f"❌ Ошибка в handle_admin_complete: {e}")
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json")


# --- ГЛОБАЛЬНЫЙ CORS MIDDLEWARE И ЗАПУСК СЕРВЕРА ---

@web.middleware
async def cors_middleware(request, handler):
    if request.method == "OPTIONS":
        return web.Response(headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS, DELETE, PUT",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Requested-With"
        })
    response = await handler(request)
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS, DELETE, PUT"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    return response

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application(middlewares=[cors_middleware])
    
    # Публичные роуты для витрины
    app.router.add_get("/", handle_ping)
    app.router.add_get("/orders", handle_get_orders)
    app.router.add_post("/order", handle_order_post)
    app.router.add_post("/order/edit", handle_order_edit)
    app.router.add_post("/order/delete", handle_order_delete)

    # Защищенные роуты для панели администратора (WebApp)
    app.router.add_get("/admin/orders", handle_admin_get_orders)
    app.router.add_post("/admin/order/return-one", handle_admin_return_one)
    app.router.add_post("/admin/order/return-all", handle_admin_return_all)
    app.router.add_post("/admin/order/complete", handle_admin_complete)

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🚀 Веб-сервер запущен на порту {port} (включая защищенные роуты админки)")

async def main():
    await start_web_server()
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            print("🤖 Бот запущен в безопасном стелс-режиме и слушает Telegram...")
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            print(f"⚠ Сбой соединения с Telegram: {e}. Рестарт через 5 сек...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
