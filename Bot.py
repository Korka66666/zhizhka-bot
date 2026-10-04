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
GAS_URL = "https://script.google.com/macros/s/AKfycbyDk-sDPisni6TJ4R14SEzh5W765oSpj0-3PuqE0PeLGkbMkSW3XahP82Q64XuFHKgGTQ/exec"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

ORDERS_FILE = "orders_data.json"

def load_orders():
    if os.path.exists(ORDERS_FILE):
        try:
            with open(ORDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_orders(data):
    try:
        with open(ORDERS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Ошибка сохранения заказов: {e}")

orders_db = load_orders()

def build_admin_keyboard_from_lines(lines):
    bullet_indices = [i for i, l in enumerate(lines) if l.startswith("• ")]
    if not bullet_indices:
        return None

    kb = [[InlineKeyboardButton(text="✅ Подтвердить / Заказ выдан", callback_data="order_done")]]
    for btn_idx, line_idx in enumerate(bullet_indices):
        line_text = lines[line_idx]
        m = re.search(r"\[(?:Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт", line_text)
        if m:
            var_name, qty = m.group(1), int(m.group(2))
            btn_title = f"🔙 Вернуть 1 шт: {var_name} ({qty} шт)" if qty > 1 else f"🔙 Вернуть на склад: {var_name}"
            kb.append([InlineKeyboardButton(text=btn_title, callback_data=f"ret1_{btn_idx}")])
    kb.append([InlineKeyboardButton(text="❌ Отменить заказ полностью", callback_data="ret_all")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Открыть витрину ЖИЖКА", web_app=WebAppInfo(url=WEBAPP_URL))]
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

# Отдача активных заказов сайту
async def handle_get_orders(request):
    user_id = request.query.get("user_id", "")
    user_orders = orders_db.get(str(user_id), [])
    return web.Response(
        text=json.dumps(user_orders),
        content_type="application/json",
        headers={"Access-Control-Allow-Origin": "*"}
    )

# Прием нового заказа
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

        counts = Counter(f"{it['title']} | {it['variant']} | {it['price']} | {it.get('typeLabel', 'Вкус')}" for it in items)
        
        items_for_gas = []
        order_lines = []
        for line, count in counts.items():
            title, variant, price, tlabel = line.split(" | ")
            items_for_gas.append({"variant": variant, "qty": count})
            order_lines.append(f"• <b>{html.escape(title)}</b> [{tlabel}: {html.escape(variant)}] — {count} шт. по {price} ₽")

        # 1. Списание в таблице
        async with aiohttp.ClientSession() as session:
            async with session.post(GAS_URL, json={"action": "deduct", "items": items_for_gas}, allow_redirects=True) as resp:
                resp_text = await resp.text()
                try:
                    gas_res = json.loads(resp_text)
                    if gas_res.get("status") == "error":
                        return web.Response(
                            status=400,
                            text=json.dumps({"status": "error", "message": gas_res.get("message")}),
                            content_type="application/json",
                            headers={"Access-Control-Allow-Origin": "*"}
                        )
                except Exception:
                    pass

        items_text = "\n".join(order_lines)
        if delivery_type == "delivery":
            delivery_text = f"🚗 <b>Способ:</b> Доставка (150–400 ₽)\n📍 <b>Адрес:</b> {delivery_addr or 'Уточнить при связи'}"
        else:
            delivery_text = "🏬 <b>Способ:</b> Самовывоз"

        admin_msg = (
            f"🚨 <b>НОВЫЙ ЗАКАЗ В ШОПЕ «ЖИЖКА»!</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} ({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"{delivery_text}\n\n"
            f"📦 <b>Состав заказа:</b>\n{items_text}\n\n"
            f"💵 <b>Итого за товары:</b> {total} ₽"
        )

        admin_markup = build_admin_keyboard_from_lines(admin_msg.split("\n"))
        sent_admin_msg = await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, reply_markup=admin_markup, parse_mode="HTML")

        # Сохранение в базу заказов
        order_entry = {
            "id": int(datetime.datetime.now().timestamp() * 1000),
            "admin_message_id": sent_admin_msg.message_id,
            "date": datetime.datetime.now().strftime("%H:%M"),
            "items": items,
            "total": total,
            "delivery_type": delivery_type,
            "delivery_address": delivery_addr,
            "status": "active"
        }
        str_cid = str(client_id)
        if str_cid not in orders_db:
            orders_db[str_cid] = []
        orders_db[str_cid].unshift if hasattr(orders_db[str_cid], 'unshift') else orders_db[str_cid].insert(0, order_entry)
        save_orders(orders_db)

        # Чек покупателю
        if client_id and client_id != ADMIN_CHAT_ID:
            try:
                buyer_delivery = "🚗 Доставка: 150–400 ₽ (администратор согласует точную сумму)" if delivery_type == "delivery" else "🏬 Самовывоз"
                buyer_msg = (
                    f"✅ <b>Ваш заказ успешно оформлен!</b>\n\n"
                    f"{buyer_delivery}\n"
                    f"📦 <b>Товары:</b>\n{items_text}\n\n"
                    f"💵 <b>Сумма:</b> {total} ₽\n\n"
                    f"Администратор уже получил заявку и свяжется с вами."
                )
                await bot.send_message(chat_id=client_id, text=buyer_msg, parse_mode="HTML")
            except Exception:
                pass

        return web.Response(
            text=json.dumps({"status": "ok", "admin_message_id": sent_admin_msg.message_id}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        return web.Response(
            status=500,
            text=json.dumps({"status": "error", "message": str(e)}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )

# Редактирование заказа покупателем с витрины
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

        # Корректировка остатков в Google Таблице
        old_counts = Counter(it["variant"] for it in old_items)
        new_counts = Counter(it["variant"] for it in new_items)

        to_add = []
        to_deduct = []
        for v in set(old_counts.keys()).union(set(new_counts.keys())):
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

        counts = Counter(f"{it['title']} | {it['variant']} | {it['price']} | {it.get('typeLabel', 'Вкус')}" for it in new_items)
        order_lines = []
        for line, count in counts.items():
            title, variant, price, tlabel = line.split(" | ")
            order_lines.append(f"• <b>{html.escape(title)}</b> [{tlabel}: {html.escape(variant)}] — {count} шт. по {price} ₽")

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
            await bot.edit_message_text(chat_id=ADMIN_CHAT_ID, message_id=admin_message_id, text=updated_msg, reply_markup=admin_markup, parse_mode="HTML")

        # Обновление в базе
        str_cid = str(client_id)
        if str_cid in orders_db:
            for o in orders_db[str_cid]:
                if o.get("admin_message_id") == admin_message_id:
                    o["items"] = new_items
                    o["total"] = total
                    o["delivery_type"] = delivery_type
                    o["delivery_address"] = delivery_addr
            save_orders(orders_db)

        return web.Response(text=json.dumps({"status": "ok"}), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})
    except Exception as e:
        return web.Response(status=500, text=json.dumps({"status": "error", "message": str(e)}), content_type="application/json", headers={"Access-Control-Allow-Origin": "*"})

# Подтверждение выдачи
@dp.callback_query(F.data == "order_done")
async def handle_order_done(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор!", show_alert=True)
    
    # Помечаем в базе как завершенный
    for user_orders in orders_db.values():
        for o in user_orders:
            if o.get("admin_message_id") == call.message.message_id:
                o["status"] = "completed"
    save_orders(orders_db)

    new_text = call.message.text + "\n\n🎉 <b>ЗАКАЗ УСПЕШНО ВЫПОЛНЕН И ВЫДАН!</b>"
    await call.message.edit_text(new_text, reply_markup=None, parse_mode="HTML")
    await call.answer("✅ Заказ подтвержден и закрыт!")

# Поштучный возврат
@dp.callback_query(F.data.startswith("ret1_"))
async def handle_return_one(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор!", show_alert=True)

    target_btn_idx = int(call.data.split("_")[1])
    lines = call.message.text.split("\n")
    bullet_indices = [i for i, l in enumerate(lines) if l.startswith("• ")]

    if target_btn_idx >= len(bullet_indices):
        return await call.answer("Позиция уже возвращена!")

    line_idx = bullet_indices[target_btn_idx]
    line_text = lines[line_idx]

    m = re.search(r"•\s*(.*?)\s*\[(Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт\.\s*по\s*(\d+)", line_text)
    if not m:
        return await call.answer("Ошибка парсинга строки")

    title, tlabel, variant, qty, price = m.group(1), m.group(2), m.group(3), int(m.group(4)), int(m.group(5))

    # Возврат 1 шт в Google Таблицу
    async with aiohttp.ClientSession() as session:
        await session.post(GAS_URL, json={"action": "add", "items": [{"variant": variant, "qty": 1}]}, allow_redirects=True)

    new_qty = qty - 1
    if new_qty > 0:
        lines[line_idx] = f"• {title} [{tlabel}: {variant}] — {new_qty} шт. по {price} ₽"
    else:
        lines[line_idx] = f"❌ <s>{title} [{tlabel}: {variant}] — 1 шт.</s> (Возвращено)"

    # Пересчет суммы
    total_match = re.search(r"(?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого):\s*(\d+)", call.message.text)
    if total_match:
        old_total = int(total_match.group(1))
        new_total = max(0, old_total - price)
        lines = [re.sub(r"((?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого):\s*)\d+", rf"\g<1>{new_total}", l) for l in lines]

    # Корректировка в базе
    for user_orders in orders_db.values():
        for o in user_orders:
            if o.get("admin_message_id") == call.message.message_id:
                o["total"] = max(0, o.get("total", 0) - price)
                for item in o.get("items", []):
                    if item.get("variant") == variant:
                        o["items"].remove(item)
                        break
    save_orders(orders_db)

    new_kb = build_admin_keyboard_from_lines(lines)
    await call.message.edit_text("\n".join(lines), reply_markup=new_kb, parse_mode="HTML")
    await call.answer(f"✅ 1 шт. ({variant}) возвращена на склад!")

# Полная отмена
@dp.callback_query(F.data == "ret_all")
async def handle_return_all(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор!", show_alert=True)

    lines = call.message.text.split("\n")
    items_to_return = []
    for idx, line in enumerate(lines):
        if line.startswith("• "):
            m = re.search(r"\[(?:Вкус|Цвет):\s*(.*?)\]\s*—\s*(\d+)\s*шт", line)
            if m:
                items_to_return.append({"variant": m.group(1).strip(), "qty": int(m.group(2))})
                lines[idx] = line.replace("• ", "❌ <s>").replace("шт.", "шт.</s> (Отменено)")

    if items_to_return:
        async with aiohttp.ClientSession() as session:
            await session.post(GAS_URL, json={"action": "add", "items": items_to_return}, allow_redirects=True)

    lines = [re.sub(r"((?:Итого к оплате|Итого за товары|Новый итог к оплате|Новый итог|Итого):\s*)\d+", r"\g<1>0", l) for l in lines]
    lines.append("\n❌ <b>ЗАКАЗ ПОЛНОСТЬЮ АННУЛИРОВАН</b>")

    # Помечаем в базе как отмененный
    for user_orders in orders_db.values():
        for o in user_orders:
            if o.get("admin_message_id") == call.message.message_id:
                o["status"] = "canceled"
    save_orders(orders_db)

    await call.message.edit_text("\n".join(lines), reply_markup=None, parse_mode="HTML")
    await call.answer("✅ Весь заказ отменен, остатки возвращены!")

async def handle_options(request):
    return web.Response(headers={
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type"
    })

async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/orders", handle_get_orders)
    app.router.add_post("/order", handle_order_post)
    app.router.add_post("/order/edit", handle_order_edit)
    app.router.add_options("/order", handle_options)
    app.router.add_options("/order/edit", handle_options)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    await start_web_server()
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            print("Бот шопа ЖИЖКА запущен и слушает Telegram...")
            await dp.start_polling(bot, handle_signals=False)
        except Exception as e:
            print(f"⚠ Сбой соединения с Telegram: {e}. Рестарт через 5 сек...")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())