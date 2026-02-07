from quart import Quart, render_template, request, redirect, url_for, flash, session, Response, current_app
from quart_auth import AuthUser, current_user, login_user, logout_user, basic_auth_required, unauthorized
from utils.telethon_manager import manager
from utils.suspicious_links import check_message_for_danger
from telethon.tl.types import User, Chat, Channel
import asyncio
import json

app = Quart(__name__)
app.secret_key = "dev-key-change-me"  # use config in production

class UserId(AuthUser):
    def __init__(self, auth_id):
        super().__init__(auth_id)

@app.errorhandler(401)
async def unauthorized_handler(_):
    return await render_template("login.html"), 401

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
    phone = form.get("phone", "").strip()
    api_id_str = form.get("api_id", "").strip()
    api_hash = form.get("api_hash", "").strip()

    if not phone.startswith("+"):
        phone = "+" + phone.lstrip()

    try:
        api_id = int(api_id_str)
    except ValueError:
        await flash("API ID must be integer", "error")
        return redirect(url_for("setup"))

    if len(api_hash) != 32:
        await flash("API Hash must be 32 characters", "error")
        return redirect(url_for("setup"))

    session["setup_phone"] = phone
    session["setup_api_id"] = api_id
    session["setup_api_hash"] = api_hash

    try:
        _, status = await manager.get_client(phone, api_id, api_hash)
        if status == "authorized":
            login_user(UserId(phone))
            return redirect(url_for("dashboard"))
        elif status == "code_needed":
            await flash("Code sent. Check Telegram.", "info")
            return redirect(url_for("verify_code"))
        elif status == "password_needed":
            await flash("2FA password required.", "warning")
            return redirect(url_for("verify_password"))
    except Exception as e:
        await flash(f"Connection error: {str(e)}", "error")

    return redirect(url_for("setup"))

@app.route("/verify_code", methods=["GET", "POST"])
async def verify_code():
    if request.method == "GET":
        return await render_template("verify_code.html")

    form = await request.form
    code = form.get("code", "").strip()
    phone = session.get("setup_phone")

    if not phone or not code:
        await flash("Missing data", "error")
        return redirect(url_for("verify_code"))

    success, msg = await manager.sign_in_code(phone, code)
    if success:
        login_user(UserId(phone))
        session.pop("setup_phone", None)
        session.pop("setup_api_id", None)
        session.pop("setup_api_hash", None)
        return redirect(url_for("dashboard"))
    else:
        await flash(f"Error: {msg}", "error")
        return redirect(url_for("verify_code"))

@app.route("/verify_password", methods=["GET", "POST"])
async def verify_password():
    if request.method == "GET":
        return await render_template("verify_password.html")

    form = await request.form
    pw = form.get("password", "")
    phone = session.get("setup_phone")

    success, msg = await manager.sign_in_password(phone, pw)
    if success:
        login_user(UserId(phone))
        session.pop("setup_phone", None)
        session.pop("setup_api_id", None)
        session.pop("setup_api_hash", None)
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
    async for d in client.iter_dialogs(limit=60):
        e = d.entity
        name = getattr(e, "title", getattr(e, "first_name", getattr(e, "username", str(d.id))))
        dialogs.append({
            "id": d.id,
            "name": name,
            "unread": d.unread_count,
            "type": "user" if isinstance(e, User) else "group" if isinstance(e, Chat) else "channel"
        })

    return await render_template("dashboard.html", dialogs=dialogs)

@app.route("/chat/<int:peer_id>")
@basic_auth_required()
async def chat(peer_id: int):
    phone = current_user.auth_id
    client, _ = await manager.get_client(phone)

    entity = await client.get_entity(peer_id)
    name = getattr(entity, "title", getattr(entity, "first_name", str(peer_id)))

    messages = []
    async for msg in client.iter_messages(entity, limit=80):
        dangers = await check_message_for_danger(msg.message or "")
        messages.append({
            "id": msg.id,
            "text": msg.message,
            "date": msg.date.isoformat(),
            "out": msg.out,
            "dangers": dangers,
            "has_media": bool(msg.media)
        })

    messages.reverse()
    return await render_template("chat.html", chat_name=name, peer_id=peer_id, messages=messages)

@app.route("/send", methods=["POST"])
@basic_auth_required()
async def send():
    data = await request.get_json()
    peer_id = data.get("peer_id")
    text = data.get("text", "").strip()

    if not text or not peer_id:
        return {"error": "missing"}, 400

    phone = current_user.auth_id
    client, _ = await manager.get_client(phone)

    try:
        await client.send_message(int(peer_id), text)
        return {"status": "sent"}
    except Exception as e:
        return {"error": str(e)}, 500

@app.route("/events")
@basic_auth_required()
async def events():
    phone = current_user.auth_id

    async def event_stream():
        queue = asyncio.Queue()

        async def cb(event):
            try:
                msg = event.message
                dangers = await check_message_for_danger(msg.message or "")
                data = {
                    "id": msg.id,
                    "peer_id": msg.peer_id.channel_id or msg.peer_id.chat_id or msg.peer_id.user_id,
                    "text": msg.message,
                    "date": msg.date.isoformat(),
                    "out": msg.out,
                    "dangers": dangers
                }
                await queue.put(json.dumps(data))
            except:
                pass

        manager.add_message_listener(phone, cb)

        try:
            while True:
                data = await queue.get()
                yield f"data: {data}\n\n"
        except asyncio.CancelledError:
            pass

    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/logout")
async def logout():
    logout_user()
    return redirect(url_for("setup"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5050, debug=True)
