import os
import asyncio
import logging
import re
from fastapi import FastAPI, Query, BackgroundTasks
from dotenv import load_dotenv
from telethon import TelegramClient, events
from telethon.errors import TypeNotFoundError
from contextlib import asynccontextmanager
import httpx

# Load env vars
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "anon")
GROUP_ID_RAW = os.getenv("GROUP_ID")
GROUP_ID = int(GROUP_ID_RAW) if GROUP_ID_RAW.startswith("-") or GROUP_ID_RAW.isdigit() else GROUP_ID_RAW

logging.basicConfig(level=logging.INFO)
client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

@asynccontextmanager
async def lifespan(app: FastAPI):
    await client.start()
    logging.info("✅ Telegram client started")
    yield
    await client.disconnect()
    logging.info("🛑 Telegram client disconnected")

app = FastAPI(lifespan=lifespan)

@app.get("/send")
async def send_token(
    token_id: str = Query(...),
    callback_url: str = Query(...),
    background_tasks: BackgroundTasks = None
):
    message_text = token_id
    logging.info(f"📨 Sending message: {message_text}")

    try:
        entity = await client.get_input_entity(GROUP_ID)
        msg = await client.send_message(entity, message_text)
    except Exception as e:
        logging.error(f"❌ Error sending message: {e}")
        return {"status": "error", "details": str(e)}

    # Queue the background task
    background_tasks.add_task(wait_and_callback, msg, token_id, callback_url)
    return {"status": "queued", "token_id": token_id}

async def wait_and_callback(original_message, token_id, callback_url, timeout=240):
    payload = {
        "token_id": token_id,
        "status": "unknown"
    }

    try:
        result = await wait_for_reply_and_click(original_message, timeout)

        if "error" in result:
            payload["status"] = "timeout"
        else:
            payload.update(result)
            payload["status"] = "success"

    except Exception as e:
        logging.error(f"❌ Background error: {e}")
        payload["status"] = "error"
        payload["error_message"] = str(e)

    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(callback_url, json=payload)
            logging.info(f"📬 Posted result to {callback_url} ({response.status_code})")
    except Exception as e:
        logging.error(f"❌ Failed to send callback: {e}")

async def wait_for_reply_and_click(original_message, timeout=180):
    loop = asyncio.get_event_loop()
    future = loop.create_future()

    async def first_reply_handler(event):
        if event.is_reply and event.reply_to_msg_id == original_message.id:
            client.remove_event_handler(first_reply_handler, events.NewMessage(chats=GROUP_ID))
            first_reply = event.message
            logging.info(f"💬 First reply:\n{first_reply.message}")

            all_buttons = [btn for row in (first_reply.buttons or []) for btn in row]
            logging.info(f"🔘 Found {len(all_buttons)} buttons in reply")

            if len(all_buttons) >= 3:
                try:
                    await first_reply.click(2)
                    logging.info("🖱️ Clicked 3rd button — waiting for second reply...")

                    async def second_reply_handler(event2):
                        if event2.is_reply and event2.reply_to_msg_id == first_reply.id:
                            client.remove_event_handler(second_reply_handler, events.NewMessage(chats=GROUP_ID))
                            second_reply = event2.message
                            logging.info(f"🔁 Reply to button click:\n{second_reply.message}")
                            if not future.done():
                                result = extract_wallet_metrics(second_reply.message)
                                future.set_result(result)

                    client.add_event_handler(second_reply_handler, events.NewMessage(chats=GROUP_ID))
                except Exception as e:
                    logging.error(f"❌ Error clicking 3rd button: {e}")
                    if not future.done():
                        result = extract_wallet_metrics(first_reply.message)
                        future.set_result(result)
            else:
                if not future.done():
                    result = extract_wallet_metrics(first_reply.message)
                    future.set_result(result)

    client.add_event_handler(first_reply_handler, events.NewMessage(chats=GROUP_ID))

    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        client.remove_event_handler(first_reply_handler, events.NewMessage(chats=GROUP_ID))
        return {"error": "Timed out waiting for second reply"}


def extract_wallet_metrics(text: str):
    metrics = {
        "raw": text,
        "jeets": {"count": "", "percent": ""},
        "chads": {"count": "", "percent": ""},
        "fresh_wallets": {"count": "", "percent": ""}
    }

    jeets = re.search(r"\b(\d+)\s+Jeets?\s+\(([\d.]+)%", text, re.IGNORECASE)
    chads = re.search(r"\b(\d+)\s+Chads?\s+\(([\d.]+)%", text, re.IGNORECASE)
    fresh = re.search(r"\b(\d+)\s+Fresh Wallets?\s+\(([\d.]+)%", text, re.IGNORECASE)

    if jeets:
        metrics["jeets"] = {
            "count": int(jeets.group(1)),
            "percent": float(jeets.group(2)) / 100
        }
    if chads:
        metrics["chads"] = {
            "count": int(chads.group(1)),
            "percent": float(chads.group(2)) / 100
        }
    if fresh:
        metrics["fresh_wallets"] = {
            "count": int(fresh.group(1)),
            "percent": float(fresh.group(2)) / 100
        }

    return metrics
