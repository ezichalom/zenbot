import os
import asyncio
from telegram import Bot

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

async def main():
    await bot.send_message(chat_id=CHAT_ID, text="🚀 BOT FUNCIONANDO")
    print("enviado")

asyncio.run(main())
