# app.py

from quart import Quart, render_template, request, redirect, url_for, flash, Response, session
from quart_auth import (
    AuthUser,
    current_user,
    login_user,
    logout_user,
    basic_auth_required,
    Unauthorized
)
from utils.telethon_manager import manager
from utils.link_detector import detect_dangerous_links
from telethon.tl.types import User, Chat, Channel
import asyncio
import json

app = Quart(__name__)
app.secret_key = "dev-key-please-change-in-production-1234567890"

class UserId(AuthUser):
    def __init__(self, auth_id):
        super().__init__(auth_id)

@app.errorhandler(Unauthorized)
async def handle_unauthorized(e):
    # Redirect to setup/login page on 401
    return await render_template("setup.html"), 401


@app.route("/")
async def index():
    if current_user.auth_id:
        return redirect(url_for("dashboard"))
    return redirect(url_for("setup"))


@app.route("/setup", methods=["GET", "POST"])
async def setup():
    if current_user.auth_id:
        return redirect(url_for("dashboard"))

    if request.method == "GET":
        return await render_template("setup.html")

    form = await request.form
    phone    = form.get("phone",   "").strip()
    api_id_str = form.get("api_id",  "").strip()
    api_hash   = form.get("api_hash","").strip()

    if not phone.startswith("+"):
        phone = "+" + phone

    try:
        api_id = int(api_id_str)
    except ValueError:
        await flash("API ID must be a number", "error")
        return redirect(url_for("setup"))

    if len(api_hash) != 32:
        await flash("API Hash must be 32 characters", "error")
        return redirect(url_for("setup"))

    session["phone"]   = phone
    session["api_id"]  = api_id
    session["api_hash"] = api_hash

    try:
        client, status = await manager.get_client(phone, api_id, api_hash)

        if status == "authorized":
            login_user(UserId(phone))
            return redirect(url_for("dashboard"))

        elif status == "code_needed":
            await flash("Code sent to Telegram", "info")
            return redirect(url_for("verify_code"))

        elif status == "password_needed":
            await flash("Enter your 2FA password", "warning")
            return redirect(url_for("verify_password"))

        elif status == "needs_credentials":
            await flash("Please provide API credentials", "error")
            return redirect(url_for("setup"))

    except Exception as e:
        await flash(f"Error: {str(e)}", "error")

    return redirect(url_for("setup"))


@app.route("/verify_code", methods=["GET", "POST"])
async def verify_code():
    if request.method == "GET":
        return await render_template("verify_code.html")

    form = await request.form
    code = form.get("code", "").strip()
    phone = session.get("phone")

    if not phone or not code:
        await flash("Missing data", "error")
        return redirect(url_for("verify_code"))

    success, msg = await manager.submit_code(phone, code)
    if success:
        login_user(UserId(phone))
        for k in ["phone", "api_id", "api_hash"]:
            session.pop(k, None)
        return redirect(url_for("dashboard"))
    else:
        await flash(f"Invalid code: {msg}", "error")
        return redirect(url_for("verify_code"))


@app.route("/verify_password", methods=["GET", "POST"])
async def verify_password():
    if request.method == "GET":
        return await render_template("verify_password.html")

    form = await request.form
    password = form.get("password", "")
    phone = session.get("phone")

    success, msg = await manager.submit_password(phone, password)
    if success:
        login_user(UserId(phone))
        for k in ["phone", "api_id", "api_hash"]:
            session.pop(k, None)
        return redirect(url_for("dashboard"))
    else:
        await flash(f"Wrong password: {msg}", "error")
        return redirect(url_for("verify_password"))


@app.route("/dashboard")
@basic_auth_required()
async def dashboard():
    phone = current_user.auth_id
    client, _ = await manager.get_client(phone)

    dialogs = []
    async for dialog in client.iter_dialogs(limit=60):
        entity = dialog.entity
        name = getattr(entity, "title", getattr(entity, "first_name", str(dialog.id)))
        dialogs.append({
            "id": dialog.id,
            "name": name,
            "unread": dialog.unread_count,
            "type": "user" if isinstance(entity, User) else "group/channel"
        })

    return await render_template("dashboard.html", dialogs=dialogs)


@app.route("/chat/<int:chat_id>")
@basic_auth_required()
async def chat(chat_id: int):
    phone = current_user.auth_id
    client, _ = await manager.get_client(phone)

    entity = await client.get_entity(chat_id)
    name = getattr(entity, "title", getattr(entity, "first_name", str(chat_id)))

    messages = []
    async for msg in client.iter_messages(entity, limit=60):
        dangers = await detect_dangerous_links(msg.message or "")
        messages.append({
            "id": msg.id,
            "text": msg.message or "",
            "date": msg.date.strftime("%Y-%m-%d %H:%M"),
            "out": msg.out,
            "dangers": dangers
        })

    messages.reverse()  # oldest → newest
    return await render_template("chat.html", chat_name=name, chat_id=chat_id, messages=messages)


@app.route("/send", methods=["POST"])
@basic_auth_required()
async def send_message():
    data = await request.get_json()
    chat_id = data.get("chat_id")
    text = (data.get("text") or "").strip()

    if not text or not chat_id:
        return {"error": "missing fields"}, 400

    phone = current_user.auth_id
    client, _ = await manager.get_client(phone)

    try:
        await client.send_message(int(chat_id), text)
        return {"status": "sent"}
    except Exception as e:
        return {"error": str(e)}, 500


@app.route("/events")
@basic_auth_required()
async def sse_events():
    phone = current_user.auth_id

    async def stream():
        queue = asyncio.Queue()

        async def callback(event):
            try:
                msg = event.message
                dangers = await detect_dangerous_links(msg.message or "")
                payload = {
                    "chat_id": msg.chat_id,
                    "msg_id": msg.id,
                    "text": msg.message or "",
                    "date": msg.date.strftime("%Y-%m-%d %H:%M"),
                    "out": msg.out,
                    "dangers": dangers
                }
                await queue.put(json.dumps(payload))
            except:
                pass

        manager.add_listener(phone, callback)

        try:
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass

    return Response(stream(), mimetype="text/event-stream")


@app.route("/logout")
async def logout():
    logout_user()
    return redirect(url_for("setup"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
