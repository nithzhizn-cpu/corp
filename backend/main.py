# ============================================================
#   SIGNAL MESSENGER v7 — BACKEND (SYNCED WITH signal_core v7)
#   FastAPI + X25519 + X3DH-подібний master_secret
#   Спрощений Symmetric "Ratchet" + ZeroTrace RAM
#   Secure Messaging + WebRTC Signaling
# ============================================================

import uuid
import json
from typing import Dict, List, Tuple

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from cryptography.exceptions import InvalidTag

from crypto.signal_core import (
    generate_identity,
    generate_onetime_prekeys,
    generate_ephemeral_key_b64,
    x3dh_sender,
    RatchetState,        # dataclass з signal_core
    ratchet_encrypt,
    ratchet_decrypt,
)

# ============================================================
#   APP + CORS
# ============================================================

app = FastAPI(title="Signal v7 Backend")

# ⚠ У проді краще вказати конкретні домени фронтенду
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,        # з "*" краще без credentials
    allow_methods=["*"],
    allow_headers=["*"],
)

# Додатковий preflight-хендлер (на всякий випадок)
@app.options("/{path:path}")
async def preflight_handler(path: str):
    return JSONResponse(
        status_code=200,
        content={"ok": True},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
            "Access-Control-Allow-Headers": "*",
        },
    )

# ============================================================
#   ZERO-TRACE STORAGE (RAM only)
# ============================================================

USERS: Dict[str, dict] = {}
PREKEYS: Dict[str, List[dict]] = {}
SESSIONS: Dict[Tuple[str, str], RatchetState] = {}
INBOX: Dict[str, List[dict]] = {}
CALL_CONNECTIONS: Dict[str, WebSocket] = {}

ZERO_TRACE_SECRET = "SET_YOUR_SECRET"  # поміняй на свій

# ============================================================
#   Pydantic Models
# ============================================================

class RegisterPayload(BaseModel):
    username: str


class InitiateSessionPayload(BaseModel):
    sender_id: str
    receiver_id: str


class MessageSendPayload(BaseModel):
    sender_id: str
    receiver_id: str
    text: str


class MessagePayload(BaseModel):
    sender_id: str
    receiver_id: str
    ciphertext: dict


class WipePayload(BaseModel):
    admin_secret: str


# ============================================================
#   HEALTHCHECK (зручно тестити Railway)
# ============================================================

@app.get("/health")
def health():
    return {"status": "ok"}


# ============================================================
#   REGISTER USER
# ============================================================

@app.post("/register")
def register(data: RegisterPayload):
    """
    Реєстрація нового юзера:
    - генеруємо identity + signed prekey + prekeys
    - кладемо все в RAM
    - повертаємо user_id + bundle (на майбутнє)
    """
    user_id = str(uuid.uuid4())

    ident = generate_identity()          # повертає *_b64
    prekeys = generate_onetime_prekeys(20)

    USERS[user_id] = {
        "username": data.username,
        "identity_priv_b64": ident["identity_priv_b64"],
        "identity_pub_b64": ident["identity_pub_b64"],
        "signed_prekey_priv_b64": ident["signed_prekey_priv_b64"],
        "signed_prekey_pub_b64": ident["signed_prekey_pub_b64"],
        "signed_prekey_sig_b64": ident["signed_prekey_sig_b64"],
    }

    PREKEYS[user_id] = prekeys
    INBOX[user_id] = []

    return {
        "user_id": user_id,
        "identity_pub": ident["identity_pub_b64"],
        "signed_prekey_pub": ident["signed_prekey_pub_b64"],
        "signed_prekey_sig": ident["signed_prekey_sig_b64"],
        "onetime_prekeys": [pk["pub_b64"] for pk in prekeys],
    }


# ============================================================
#   GET BUNDLE (якщо треба буде на фронті)
# ============================================================

@app.get("/bundle/{user_id}")
def get_bundle(user_id: str):
    if user_id not in USERS:
        return {"error": "invalid user"}

    return {
        "identity_pub": USERS[user_id]["identity_pub_b64"],
        "signed_prekey_pub": USERS[user_id]["signed_prekey_pub_b64"],
        "signed_prekey_sig": USERS[user_id]["signed_prekey_sig_b64"],
        "onetime_prekeys": [pk["pub_b64"] for pk in PREKEYS.get(user_id, [])],
    }


# ============================================================
#   INIT SIGNAL SESSION (X3DH → shared root_key)
# ============================================================

@app.post("/session/init")
def session_init(data: InitiateSessionPayload):
    """
    Ініціалізація сесії між sender_id (s) і receiver_id (r).

    Ми робимо X3DH «від імені» sender'а, отримуємо master_secret
    і цей же master_secret використовуємо для обох напрямків
    (s→r і r→s) як root_key, щоб не було розʼїзду ключів.
    """
    s = data.sender_id
    r = data.receiver_id

    if s not in USERS or r not in USERS:
        return {"error": "invalid sender/receiver"}

    recv_bundle = {
        "identity_pub_b64": USERS[r]["identity_pub_b64"],
        "signed_prekey_pub_b64": USERS[r]["signed_prekey_pub_b64"],
        "signed_prekey_sig_b64": USERS[r]["signed_prekey_sig_b64"],
    }

    # One-time prekey (з'їдається один раз)
    onetime = None
    if PREKEYS.get(r):
        pk = PREKEYS[r].pop(0)
        onetime = pk["pub_b64"]

    # X3DH master secret (байти)
    master_secret = x3dh_sender(
        identity_priv_b64=USERS[s]["identity_priv_b64"],
        eph_priv_b64=generate_ephemeral_key_b64(),
        recv_bundle=recv_bundle,
        onetime_prekey_pub_b64=onetime,
    )

    # Спрощений "ratchet": один root_key, без chain_key_send/recv
    SESSIONS[(s, r)] = RatchetState(root_key=master_secret)
    SESSIONS[(r, s)] = RatchetState(root_key=master_secret)

    return {
        "status": "session_established",
        "used_one_time_prekey": bool(onetime),
    }


# ============================================================
#   ENCRYPT & SEND MESSAGE
# ============================================================

@app.post("/message/send")
def message_send(data: MessageSendPayload):
    """
    Шифруємо повідомлення від sender → receiver
    і кладемо в INBOX[receiver] як packet (nonce+ct).
    """
    session_key = (data.sender_id, data.receiver_id)
    if session_key not in SESSIONS:
        return {"error": "session not initialized"}

    packet = ratchet_encrypt(SESSIONS[session_key], data.text)

    INBOX.setdefault(data.receiver_id, []).append(
        {
            "from": data.sender_id,
            "packet": packet,
        }
    )

    return {"status": "sent"}


# ============================================================
#   POLL MESSAGES (DELIVER & DECRYPT ON SERVER)
# ============================================================

@app.get("/message/poll/{user_id}")
def poll(user_id: str):
    """
    Клієнт періодично опитує цей endpoint.
    Ми:
      - забираємо всі пакети з INBOX[user_id],
      - для кожного:
          • знаходимо сесію (sender,user_id),
          • пробуємо decrypt,
          • якщо все ок — додаємо у result.
      - INBOX[user_id] очищаємо (zero-trace).
    """
    msgs = INBOX.get(user_id, [])
    result: List[dict] = []

    for item in msgs:
        sender = item["from"]
        packet = item["packet"]

        session_key = (sender, user_id)
        if session_key not in SESSIONS:
            # немає сесії – просто скіпаємо
            continue

        try:
            plaintext, new_state = ratchet_decrypt(SESSIONS[session_key], packet)
            # new_state зараз такий самий (root_key не міняється),
            # але на всякий випадок оновимо:
            SESSIONS[session_key] = new_state

            result.append(
                {
                    "from": sender,
                    "text": plaintext,
                }
            )
        except InvalidTag:
            # хтось змінив пакет / ключі не співпали – скіпаємо
            print(f"[WARN] InvalidTag decrypt from={sender} to={user_id}")
            continue
        except Exception as e:
            print(f"[ERROR] decrypt error from={sender} to={user_id}: {e}")
            continue

    # Zero-trace після доставки
    INBOX[user_id] = []

    return {"messages": result}


# ============================================================
#   RAW RECEIVE (debug only) — необов'язково використовувати
# ============================================================

@app.post("/message/receive")
def receive_message(data: MessagePayload):
    session_key = (data.sender_id, data.receiver_id)
    if session_key not in SESSIONS:
        return {"error": "session not initialized"}

    try:
        plaintext, new_state = ratchet_decrypt(SESSIONS[session_key], data.ciphertext)
        SESSIONS[session_key] = new_state
        return {"plaintext": plaintext}
    except Exception as e:
        return {"error": str(e)}


# ============================================================
#   CALL SIGNALING (WebRTC)
# ============================================================

@app.websocket("/call/{user_id}")
async def call_socket(ws: WebSocket, user_id: str):
    """
    Простий сигналінг для WebRTC:
    - кожен юзер відкриває ws /call/{user_id}
    - повідомлення типу { type, from, to, data } ретранслюються
      на інший ws з тим самим "to".
    """
    await ws.accept()
    CALL_CONNECTIONS[user_id] = ws
    print(f"[WS] connected: {user_id}")

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            target = msg.get("to")

            if target in CALL_CONNECTIONS:
                await CALL_CONNECTIONS[target].send_text(raw)
    except WebSocketDisconnect:
        print(f"[WS] disconnected: {user_id}")
        CALL_CONNECTIONS.pop(user_id, None)
    except Exception as e:
        print(f"[WS] error for {user_id}: {e}")
        CALL_CONNECTIONS.pop(user_id, None)


# ============================================================
#   ZERO-TRACE WIPE (admin)
# ============================================================

@app.post("/zerotrace/wipe")
def wipe(data: WipePayload):
    if data.admin_secret != ZERO_TRACE_SECRET:
        return {"error": "invalid"}

    USERS.clear()
    PREKEYS.clear()
    SESSIONS.clear()
    INBOX.clear()
    CALL_CONNECTIONS.clear()

    return {"status": "wiped"}


# ============================================================
#   START SERVER (dev only)
# ============================================================

if __name__ == "__main__":
    # локально запускаєш так:
    #   python main.py
    uvicorn.run("main:app", host="0.0.0.0", port=8000)