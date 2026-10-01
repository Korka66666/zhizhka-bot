import os
import json
import asyncio
from collections import Counter
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

BOT_TOKEN = "8737856125:AAFDS38fdormQawDeeI0-f87J1jfjK4zLig"
ADMIN_CHAT_ID = 8651846848
WEBAPP_URL = "https://regal-parfait-e29c47.netlify.app"

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚡ Открыть витрину ЖИЖКА", web_app=WebAppInfo(url=WEBAPP_URL))]
    ])
    await message.answer(
        "👋 **Добро пожаловать в шоп ЖИЖКА!**\n\n"
        "Жми кнопку ниже, чтобы открыть каталог и собрать заказ.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

# Приём заказа с витрины напрямую через POST
async def handle_order_post(request):
    try:
        data = await request.json()
        items = data.get("items", [])
        total = data.get("total", 0)
        user_info = data.get("user", {})

        counts = Counter(f"{it['title']} | {it['variant']} | {it['price']} ₽" for it in items)
        
        order_lines = []
        for line, count in counts.items():
            title, variant, price = line.split(" | ")
            order_lines.append(f"• **{title}** ({variant}) — {count} шт. по {price}")

        items_text = "\n".join(order_lines) if order_lines else "Пустой заказ"
        
        username = f"@{user_info.get('username')}" if user_info.get('username') else "Не указан"
        client_name = user_info.get('name') or "Покупатель с сайта"
        client_id = user_info.get('id') or "Неизвестен"

        admin_msg = (
            f"🚨 **НОВЫЙ ЗАКАЗ В ШОПЕ «ЖИЖКА»!**\n\n"
            f"👤 **Покупатель:** {client_name} ({username})\n"
            f"🆔 ID: `{client_id}`\n\n"
            f"📦 **Состав заказа:**\n{items_text}\n\n"
            f"💵 **Итого к оплате:** {total} ₽"
        )
        
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode="Markdown")
        return web.Response(
            text=json.dumps({"status": "ok"}),
            content_type="application/json",
            headers={"Access-Control-Allow-Origin": "*"}
        )
    except Exception as e:
        print(f"Ошибка заказа: {e}")
        return web.Response(status=500, text=str(e), headers={"Access-Control-Allow-Origin": "*"})

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
    print("Бот шопа ЖИЖКА запущен с новым токеном...")
    await start_web_server()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())