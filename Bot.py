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
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Открыть витрину", web_app=WebAppInfo(url=WEBAPP_URL))]
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
            f"👋 <b>Добро пожаловать!</b>\n\n"
            f"Жми кнопку ниже, чтобы бы продолжить:",
            reply_markup=kb,
            parse_mode="HTML"
        )

# Автоответчик на текстовые сообщения
@dp.message()
async def any_text_handler(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Открыть витрину", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer("Жми кнопку ниже, чтобы перейти на сайт 👇", reply_markup=kb)

# --- AIOHTTP WEB-ОБРАБОТЧИКИ (СИНХРОНИЗАЦИЯ С САЙТОМ) ---

# Отдача заказов сайту
async def handle_get_orders(request):
    user_id = request.query.get("user_id", "")
    user_orders = orders_db.get(str(user_id), [])
    return web.Response(
        text=json.dumps(user_orders),
        content_type="application/json"
    )

# Удаление заказа клиентом через витрину
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

# Прием нового заказа с сайта
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

        # Группировка одинаковых товаров с защитой от undefined
        counts = Counter()
        for it in items:
            t = it.get("title", "Товар")
            v = it.get("variant") or it.get("cleanName") or "Стандарт"
            p = int(it.get("price") or 0)
            tl = it.get("typeLabel") or "Вкус"
            counts[(t, v, p, tl)] += 1

        items_for_gas = []
        order_lines = []
        clean_items_list = []

        for (t, v, p, tl), count in counts.items():
            items_for_gas.append({"variant": v, "qty": count})
            order_lines.append(f"• <b>{html.escape(t)}</b> [{tl}: {html.escape(v)}] — {count} шт. по {p} ₽")
            for _ in range(count):
                clean_items_list.append({"title": t, "variant": v, "price": p, "typeLabel": tl})

        # Списание остатков в Google Таблице
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

        items_text = "\n".join(order_lines)
        if delivery_type == "delivery":
            delivery_text = f"🚗 <b>Способ:</b> Доставка (150–400 ₽)\n📍 <b>Адрес:</b> {delivery_addr or 'Уточнить при связи'}"
        else:
            delivery_text = "🏬 <b>Способ:</b> Самовывоз"

        admin_msg = (
            f"🚨 <b>НОВЫЙ ЗАКАЗ</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} ({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"{delivery_text}\n\n"
            f"📦 <b>Состав:</b>\n{items_text}\n\n"
            f"💵 <b>Итого:</b> {total} ₽"
        )

        admin_markup = build_admin_keyboard_from_lines(admin_msg.split("\n"))
        sent_admin_msg = await bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=admin_msg,
            reply_markup=admin_markup,
            parse_mode="HTML"
        )

        order_entry = {
            "id": int(datetime.datetime.now().timestamp() * 1000),
            "admin_message_id": sent_admin_msg.message_id,
            "date": datetime.datetime.now().strftime("%H:%M"),
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

        # Чек клиенту в Telegram
        if client_id and client_id != ADMIN_CHAT_ID:
            try:
                buyer_delivery = "🚗 Доставка: 150–400 ₽ (администратор согласует точную сумму)" if delivery_type == "delivery" else "🏬 Самовывоз"
                buyer_msg = (
                    f"✅ <b>Ваш заказ успешно оформлен!</b>\n\n"
                    f"{buyer_delivery}\n"
                    f"📦 <b>Товары:</b>\n{items_text}\n\n"
                    f"💵 <b>Сумма к оплате:</b> {total} ₽\n\n"
                    f"Администратор уже получил заявку и свяжется с вами."
                )
                await bot.send_message(chat_id=client_id, text=buyer_msg, parse_mode="HTML")
            except Exception:
                pass

        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Новый заказ от {client_name} на сумму {total} ₽")

        return web.Response(
            text=json.dumps({"status": "ok", "admin_message_id": sent_admin_msg.message_id}),
            content_type="application/json"
        )
    except Exception as e:
        print(f"❌ Ошибка в handle_order_post: {e}")
        return web.Response(
            status=500,
            text=json.dumps({"status": "error", "message": str(e)}),
            content_type="application/json"
        )

# Изменение заказа покупателем
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

        order_lines = []
        clean_new_items = []
        for (t, v, p, tl), count in counts.items():
            order_lines.append(f"• <b>{html.escape(t)}</b> [{tl}: {html.escape(v)}] — {count} шт. по {p} ₽")
            for _ in range(count):
                clean_new_items.append({"title": t, "variant": v, "price": p, "typeLabel": tl})

        items_text = "\n".join(order_lines)
        if delivery_type == "delivery":
            delivery_text = f"🚗 <b>Способ:</b> Доставка (150–400 ₽)\n📍 <b>Адрес:</b> {delivery_addr or 'Уточнить при связи'}"
        else:
            delivery_text = "🏬 <b>Способ:</b> Самовывоз"

        updated_msg = (
            f"🔄 <b>ЗАКАЗ ИЗМЕНЕН ПОКУПАТЕЛЕМ!</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} ({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"{delivery_text}\n\n"
            f"📦 <b>Актуальный состав заказа:</b>\n{items_text}\n\n"
            f"💵 <b>Новый итог:</b> {total} ₽"
        )

        admin_markup = build_admin_keyboard_from_lines(updated_msg.split("\n"))
        if admin_message_id:
            try:
                await bot.edit_message_text(
                    chat_id=ADMIN_CHAT_ID,
                    message_id=admin_message_id,
                    text=updated_msg,
                    reply_markup=admin_markup,
                    parse_mode="HTML"
                )
            except Exception as e:
                print(f"⚠ Ошибка обновления сообщения админа: {e}")

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

        return web.Response(text=json.dumps({"status": "ok"}), content_type="application/json")
    except Exception as e:
        print(f"❌ Ошибка в handle_order_edit: {e}")
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json")

# --- КНОПКИ АДМИНИСТРАТОРА В TELEGRAM ---

# Подтверждение выполнения заказа
@dp.callback_query(F.data == "order_done")
async def handle_order_done(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор!", show_alert=True)
    
    buyer_id = None
    for uid, user_orders in orders_db.items():
        for o in user_orders:
            if o.get("admin_message_id") == call.message.message_id:
                o["status"] = "completed"
                buyer_id = uid
    save_orders(orders_db)

    orig_html = call.message.html_text or call.message.text
    new_text = orig_html + "\n\n🎉 <b>ЗАКАЗ УСПЕШНО ВЫПОЛНЕН И ВЫДАН!</b>"
    await call.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    await call.answer("✅ Заказ подтвержден и закрыт!")

    # Оповещение покупателю
    if buyer_id and buyer_id != str(ADMIN_CHAT_ID):
        try:
            await bot.send_message(
                chat_id=int(buyer_id),
                text="🎉 <b>Ваш заказ успешно выдан!</b>\nСпасибо за покупку. Ждем вас снова.",
                parse_mode="HTML"
            )
        except Exception:
            pass

# Поштучный возврат позиции на склад
@dp.callback_query(F.data.startswith("ret1_"))
async def handle_return_one(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор!", show_alert=True)

    target_btn_idx = int(call.data.split("_")[1])
    raw_text = call.message.html_text or call.message.text
    lines = raw_text.split("\n")
    bullet_indices = [i for i, l in enumerate(lines) if l.strip().startswith("•")]

    if target_btn_idx >= len(bullet_indices):
        return await call.answer("Позиция уже возвращена!", show_alert=True)

    line_idx = bullet_indices[target_btn_idx]
    line_text = lines[line_idx]

    pattern = r"•\s*(?:<b>)?(.*?)(?:</b>)?\s*\[(Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт\.\s*по\s*(\d+)"
    m = re.search(pattern, line_text)
    if not m:
        return await call.answer("Ошибка парсинга строки", show_alert=True)

    title = html.unescape(m.group(1).strip())
    tlabel = m.group(2).strip()
    variant = html.unescape(m.group(3).strip())
    qty = int(m.group(4))
    price = int(m.group(5))

    # Возврат 1 шт в Google Таблицу
    async with aiohttp.ClientSession() as session:
        await session.post(GAS_URL, json={"action": "add", "items": [{"variant": variant, "qty": 1}]}, allow_redirects=True)

    new_qty = qty - 1
    if new_qty > 0:
        lines[line_idx] = f"• <b>{html.escape(title)}</b> [{tlabel}: {html.escape(variant)}] — {new_qty} шт. по {price} ₽"
    else:
        lines[line_idx] = f"❌ <s><b>{html.escape(title)}</b> [{tlabel}: {html.escape(variant)}] — 1 шт.</s> (Возвращено)"

    # Пересчет суммы с сохранением тегов
    total_pattern = r"((?:<b>)?(?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого):?(?:</b>)?:?\s*)(\d+)"
    total_match = re.search(total_pattern, raw_text)
    if total_match:
        old_total = int(total_match.group(2))
        new_total = max(0, old_total - price)
        lines = [re.sub(total_pattern, rf"\g<1>{new_total}", l) for l in lines]

    # Корректировка в базе
    for user_orders in orders_db.values():
        for o in user_orders:
            if o.get("admin_message_id") == call.message.message_id:
                o["total"] = max(0, o.get("total", 0) - price)
                for item in o.get("items", []):
                    item_var = item.get("variant") or item.get("cleanName")
                    if item_var == variant:
                        o["items"].remove(item)
                        break
                if not o.get("items") or o["total"] == 0:
                    o["status"] = "canceled"
    save_orders(orders_db)

    new_kb = build_admin_keyboard_from_lines(lines)
    await call.message.edit_text("\n".join(lines), reply_markup=new_kb, parse_mode="HTML")
    await call.answer(f"✅ 1 шт. ({variant}) возвращена на склад!")

# Полная отмена заказа
@dp.callback_query(F.data == "ret_all")
async def handle_return_all(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор!", show_alert=True)

    raw_text = call.message.html_text or call.message.text
    lines = raw_text.split("\n")
    items_to_return = []

    pattern = r"•\s*(?:<b>)?(.*?)(?:</b>)?\s*\[(?:Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт"
    for idx, line in enumerate(lines):
        if line.strip().startswith("•"):
            m = re.search(pattern, line)
            if m:
                var_name = html.unescape(m.group(2).strip())
                items_to_return.append({"variant": var_name, "qty": int(m.group(3))})
                lines[idx] = re.sub(r"^•\s*", "❌ <s>", line) + "</s> (Отменено)"

    # Возврат всех позиций в Google Таблицу
    if items_to_return:
        async with aiohttp.ClientSession() as session:
            await session.post(GAS_URL, json={"action": "add", "items": items_to_return}, allow_redirects=True)

    total_pattern = r"((?:<b>)?(?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого):?(?:</b>)?:?\s*)\d+"
    lines = [re.sub(total_pattern, r"\g<1>0", l) for l in lines]
    lines.append("\n❌ <b>ЗАКАЗ ПОЛНОСТЬЮ АННУЛИРОВАН</b>")

    buyer_id = None
    for uid, user_orders in orders_db.items():
        for o in user_orders:
            if o.get("admin_message_id") == call.message.message_id:
                o["status"] = "canceled"
                o["total"] = 0
                buyer_id = uid  # Точный ID покупателя
    save_orders(orders_db)

    await call.message.edit_text("\n".join(lines), reply_markup=None, parse_mode="HTML")
    await call.answer("✅ Весь заказ отменен, остатки возвращены!")

    # Оповещение покупателю
    if buyer_id and buyer_id != str(ADMIN_CHAT_ID):
        try:
            await bot.send_message(
                chat_id=int(buyer_id),
                text="❌ <b>Ваш заказ был отменен администратором.</b>\nТовары возвращены на склад.",
                parse_mode="HTML"
            )
        except Exception:
            pass

# Глобальный CORS Middleware
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
    app.router.add_get("/", handle_ping)
    app.router.add_get("/orders", handle_get_orders)
    app.router.add_post("/order", handle_order_post)
    app.router.add_post("/order/edit", handle_order_edit)
    app.router.add_post("/order/delete", handle_order_delete)  # Роут удаления заказа

    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"🚀 Веб-сервер запущен на порту {port}")

async def main():
    await start_web_server()
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            print("🤖 Бот запущен и слушает Telegram...")
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            print(f"⚠ Сбой соединения с Telegram: {e}. Рестарт через 5 сек...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())