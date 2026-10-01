import os
import json
import asyncio
from collections import Counter
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiohttp import web

BOT_TOKEN = "8737856125:AAFDS38fdormQawDeeI0-f87J1jfjK4zLig"
ADMIN_CHAT_ID = 1299750536
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
        "Нажмите кнопку ниже, чтобы открыть каталог и собрать заказ.",
        reply_markup=kb,
        parse_mode="Markdown"
    )

@dp.message(F.web_app_data)
async def handle_order(message: types.Message):
    try:
        data = json.loads(message.web_app_data.data)
        items = data.get("items", [])
        total = data.get("total", 0)

        item_counts = Counter(f"{it['title']} | {it['variant']} | {it['price']} ₽" for it in items)
        
        order_lines = []
        for line, count in item_counts.items():
            title, variant, price = line.split(" | ")
            order_lines.append(f"• **{title}** ({variant}) — {count} шт. по {price}")

        items_text = "\n".join(order_lines)
        user = message.from_user
        username = f"@{user.username}" if user.username else f"[Написать клиенту](tg://user?id={user.id})"

        await message.answer(
            f"✅ **Заказ принят!**\n\n"
            f"📋 **Позиции:**\n{items_text}\n\n"
            f"💰 **Итого:** {total} ₽\n\n"
            f"Администратор скоро свяжется с вами для подтверждения заказа.",
            parse_mode="Markdown"
        )

        admin_msg = (
            f"🚨 **НОВЫЙ ЗАКАЗ В ШОПЕ «ЖИЖКА»!**\n\n"
            f"👤 **Покупатель:** {user.full_name} ({username})\n"
            f"🆔 ID: `{user.id}`\n\n"
            f"📦 **Состав:**\n{items_text}\n\n"
            f"💵 **Сумма:** {total} ₽"
        )
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_msg, parse_mode="Markdown")

    except Exception as e:
        print(f"Ошибка заказа: {e}")

# Простой веб-сервер для бесплатного тарифа Render
async def handle_ping(request):
    return web.Response(text="Bot is running!")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
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