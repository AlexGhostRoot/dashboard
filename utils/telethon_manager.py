import asyncio
from collections import defaultdict
from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User, Chat, Channel, InputPeerUser, InputPeerChat, InputPeerChannel
from config import Config

mongo_client = AsyncIOMotorClient(Config.MONGO_URI)
db = mongo_client.alex_dashboard
sessions = db[Config.SESSION_COLLECTION]

class TelethonManager:
    def __init__(self):
        self.clients = {}           # phone → TelegramClient
        self.event_handlers = defaultdict(list)  # phone → list of event callbacks

    async def get_client(self, phone: str, api_id: int = None, api_hash: str = None, force_reconnect=False):
        phone = phone.strip()
        if phone in self.clients and not force_reconnect:
            return self.clients[phone]

        doc = await sessions.find_one({"phone": phone})

        if doc:
            stored_api_id = doc.get("api_id")
            stored_api_hash = doc.get("api_hash")
            session_str = doc.get("session", "")
            if not api_id:
                api_id = stored_api_id
                api_hash = stored_api_hash
        else:
            if not api_id or not api_hash:
                raise ValueError("API ID and Hash required for new session")

        client = TelegramClient(
            StringSession(session_str),
            api_id,
            api_hash,
            connection_retries=None,
            retry_delay=1
        )

        await client.connect()

        if not await client.is_user_authorized():
            if session_str == "":
                await client.send_code_request(phone)
                return client, "code_needed"

            return client, "password_needed"  # assume 2FA if session but not authorized

        # authorized → save/update
        session_str = client.session.save()
        await sessions.update_one(
            {"phone": phone},
            {"$set": {
                "api_id": api_id,
                "api_hash": api_hash,
                "session": session_str,
                "last_active": asyncio.get_event_loop().time()
            }},
            upsert=True
        )

        # Add global new message handler for real-time
        @client.on(events.NewMessage)
        async def new_msg_handler(event):
            for cb in self.event_handlers[phone]:
                asyncio.create_task(cb(event))

        self.clients[phone] = client
        return client, "authorized"

    async def sign_in_code(self, phone: str, code: str):
        client = self.clients.get(phone)
        if not client:
            return False, "No active client session"

        try:
            await client.sign_in(phone, code)
            session_str = client.session.save()
            await sessions.update_one({"phone": phone}, {"$set": {"session": session_str}})
            return True, "success"
        except Exception as e:
            return False, str(e)

    async def sign_in_password(self, phone: str, password: str):
        client = self.clients.get(phone)
        if not client:
            return False, "No active client session"

        try:
            await client.sign_in(password=password)
            session_str = client.session.save()
            await sessions.update_one({"phone": phone}, {"$set": {"session": session_str}})
            return True, "success"
        except Exception as e:
            return False, str(e)

    def add_message_listener(self, phone: str, callback):
        self.event_handlers[phone].append(callback)

manager = TelethonManager()
