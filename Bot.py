import os
import json
import asyncio
import re
import html
import aiohttp
from collections import Counter
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

BOT_TOKEN = "8737856125:AAEiomJycU_ymRkp60gNYQa-AJdRx6N6W_Q"
ADMIN_CHAT_ID = 8651846848
WEBAPP_URL = "https://regal-parfait-e29c47.netlify.app"
GAS_URL = "https://script.google.com/macros/s/AKfycbyDk-sDPisni6TJ4R14SEzh5W765oSpj0-3PuqE0PeLGkbMkSW3XahP82Q64XuFHKgGTQ/exec"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Открыть витрину ЖИЖКА", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "👋 <b>Добро пожаловать в шоп ЖИЖКА!</b>\n\n"
        "Нажмите кнопку ниже, чтобы открыть каталог и собрать заказ.",
        reply_markup=kb,
        parse_mode="HTML"
    )

# Прием POST-запроса от WebApp сайта
async def handle_order_post(request):
    try:
        data = await request.json()
        items = data.get("items", [])
        total = data.get("total", 0)
        user_info = data.get("user", {})

        counts = Counter(f"{it['title']} | {it['variant']} | {it['price']} ₽" for it in items)
        
        items_for_gas = []
        order_lines = []
        for line, count in counts.items():
            title, variant, price = line.split(" | ")
            items_for_gas.append({"variant": variant, "qty": count})
            order_lines.append(f"• <b>{html.escape(title)}</b> [Вкус: {html.escape(variant)}] — {count} шт. по {price}")

        # 1. Отправляем в Google Таблицу на проверку и списание
        gas_payload = {"action": "deduct", "items": items_for_gas}
        async with aiohttp.ClientSession() as session:
            async with session.post(GAS_URL, json=gas_payload, allow_redirects=True) as resp:
                resp_text = await resp.text()
                try:
                    gas_res = json.loads(resp_text)
                    if gas_res.get("status") == "error":
                        return web.Response(
                            status=400,
                            text=json.dumps({"message": gas_res.get("message")}),
                            content_type="application/json",
                            headers={"Access-Control-Allow-Origin": "*"}
                        )
                except Exception:
                    pass

        # 2. Формируем чек для отправки админу
        items_text = "\n".join(order_lines) if order_lines else "Пустой заказ"
        raw_username = user_info.get("username")
        username_str = f"@{raw_username}" if raw_username else "Не указан"
        client_name = html.escape(user_info.get("name") or "Покупатель")
        client_id = user_info.get("id") or "Неизвестен"

        admin_msg = (
            f"🚨 <b>НОВЫЙ ЗАКАЗ В ШОПЕ «ЖИЖКА»!</b>\n\n"
            f"👤 <b>Покупатель:</b> {client_name} ({html.escape(username_str)})\n"
            f"🆔 ID: <code>{client_id}</code>\n\n"
            f"📦 <b>Состав заказа:</b>\n{items_text}\n\n"
            f"💵 <b>Итого к оплате:</b> {total} ₽"
        )

        # 3. Инлайн-кнопки CRM для возврата товара
        markup = InlineKeyboardMarkup(inline_keyboard=[])
        for idx, item in enumerate(items_for_gas):
            markup.inline_keyboard.append([
                InlineKeyboardButton(text=f"🔙 Вернуть на склад: {item['variant']}", callback_data=f"ret_{idx}")
            ])
        markup.inline_keyboard.append([
            InlineKeyboardButton(text="❌ Отменить заказ полностью", callback_data="ret_all")
        ])

        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, reply_markup=markup, parse_mode="HTML")
        return web.Response(
            text=json.dumps({"status": "ok"}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        print(f"Ошибка заказа: {e}")
        return web.Response(status=500, text=str(e), headers={"Access-Control-Allow-Origin": "*"})

# Обработка нажатий на кнопки отмены/возврата (строго для админа)
@dp.callback_query(F.data.startswith("ret_"))
async def handle_return(call: types.CallbackQuery):
    if call.from_user.id != ADMIN_CHAT_ID:
        return await call.answer("❌ Только администратор может отменять заказы!", show_alert=True)

    lines = call.message.text.split("\n")
    bullet_indices = [i for i, line in enumerate(lines) if line.startswith("• ")]

    items_to_return = []
    lines_to_modify = []

    action = call.data.split("_")[1]
    if action == "all":
        lines_to_modify = bullet_indices
    else:
        target_idx = int(action)
        if target_idx < len(bullet_indices):
            lines_to_modify = [bullet_indices[target_idx]]

    for line_idx in lines_to_modify:
        line_text = lines[line_idx]
        match = re.search(r"\[Вкус:\s*(.*?)\]\s*—\s*(\d+)\s*шт", line_text)
        if match:
            items_to_return.append({"variant": match.group(1).strip(), "qty": int(match.group(2))})
            lines[line_idx] = line_text.replace("• ", "❌ <s>").replace("шт.", "шт.</s> (Отменено)")

    # Отправляем в Google Таблицу на возврат товара
    if items_to_return:
        gas_payload = {"action": "add", "items": items_to_return}
        async with aiohttp.ClientSession() as session:
            await session.post(GAS_URL, json=gas_payload)

    # Удаляем нажатые кнопки
    new_kb = []
    for row in call.message.reply_markup.inline_keyboard:
        for btn in row:
            if btn.callback_data == call.data or call.data == "ret_all":
                continue
            new_kb.append([btn])

    await call.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=new_kb), parse_mode="HTML")
    await call.answer("✅ Товар успешно возвращен в таблицу!")

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
    app.router.add_post("/order", handle_order_post)
    app.router.add_options("/order", handle_options)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 8080))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

async def main():
    print("Бот шопа ЖИЖКА запущен...")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())