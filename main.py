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

# Load environment variables
load_dotenv()

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
SESSION_NAME = os.getenv("SESSION_NAME", "anon")
GROUP_ID_RAW = os.getenv("GROUP_ID")
GROUP_ID = int(GROUP_ID_RAW) if GROUP_ID_RAW.startswith("-") or GROUP_ID_RAW.isdigit() else GROUP_ID_RAW

# Configure logging
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
    message_text = f"/top {token_id}"
    logging.info(f"📨 Sending message: {message_text}")

    try:
        entity = await client.get_input_entity(GROUP_ID)
        msg = await client.send_message(entity, message_text)
    except Exception as e:
        logging.error(f"❌ Error sending message: {e}")
        return {"status": "error", "details": str(e)}

    background_tasks.add_task(wait_and_callback, msg, token_id, callback_url)
    return {"status": "queued", "token_id": token_id}

async def wait_and_callback(original_message, token_id, callback_url, timeout=240):
    payload = {
        "token_id": token_id,
        "status": "unknown"
    }

    try:
        result = await wait_for_reply(original_message, timeout)

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

import datetime

import datetime

async def wait_for_reply(original_message, timeout=180):
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    start_time = datetime.datetime.now(datetime.timezone.utc)

    logging.info(f"🕐 Listening for reply after: {start_time.isoformat()}")
    logging.info(f"🧵 Waiting for message in chat ID: {original_message.chat_id}")

    async def reply_handler(event):
        msg = event.message
        msg_time = msg.date
        msg_sender = msg.sender_id
        msg_text = msg.message

        logging.info("📥 Incoming message detected:")
        logging.info(f"    From chat: {event.chat_id}")
        logging.info(f"    Sender ID: {msg_sender}")
        logging.info(f"    Timestamp: {msg_time.isoformat()}")
        logging.info(f"    Text:\n{msg_text}")

        if event.chat_id == original_message.chat_id and msg_time >= start_time:
            logging.info("✅ Message matches expected time and chat. Parsing...")
            client.remove_event_handler(reply_handler, events.NewMessage(chats=GROUP_ID))

            if not future.done():
                result = extract_top_holders(msg_text)
                future.set_result(result)
        else:
            logging.info("❌ Message ignored (not from expected chat or too early)")

    client.add_event_handler(reply_handler, events.NewMessage(chats=GROUP_ID))

    try:
        return await asyncio.wait_for(future, timeout)
    except asyncio.TimeoutError:
        client.remove_event_handler(reply_handler, events.NewMessage(chats=GROUP_ID))
        logging.warning("⏱️ Timeout: no reply received in time window.")
        return {"error": "Timed out waiting for reply"}



def extract_top_holders(text: str):
    result = {
        "raw": text,
        "top_holder_summary": "",
        "noteworthy_holders": [],
        "top1_name": "", "top1_value": "", "top1_rank": "", "top1_sol": "",
        "top2_name": "", "top2_value": "", "top2_rank": "", "top2_sol": "",
        "top3_name": "", "top3_value": "", "top3_rank": "", "top3_sol": ""
    }

    # Match "3/35 top holders"
    summary_match = re.search(r"(\d+/\d+)\s+top holders", text, re.IGNORECASE)
    if summary_match:
        result["top_holder_summary"] = summary_match.group(1)

    # Match lines like: "#2 (427 SOL) (...) | ⚠️ NAME ($226.6K)"
    holder_lines = re.findall(
        r"#(\d+)\s+\(([\d.]+)\s+SOL\).*?\|\s+.*?([A-Za-z0-9]+)\s+\(\$([\d.,]+)[kK]?\)",
        text
    )

    for index, (rank, sol, name, value) in enumerate(holder_lines):
        try:
            dollar_value = float(value.replace(",", ""))
            sol_value = float(sol)
            rank_value = int(rank)

            holder_data = {
                "rank": rank_value,
                "sol": sol_value,
                "name": name,
                "value": dollar_value
            }

            result["noteworthy_holders"].append(holder_data)

            # Flatten top 3
            if index < 3:
                i = index + 1
                result[f"top{i}_name"] = name
                result[f"top{i}_value"] = dollar_value
                result[f"top{i}_rank"] = rank_value
                result[f"top{i}_sol"] = sol_value

        except Exception as e:
            logging.warning(f"⚠️ Failed to parse one holder entry: {e}")

    return result


