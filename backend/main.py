# ==============================================
#   SIGNAL MESSENGER V6 — BACKEND
#   FastAPI + X3DH + Double Ratchet + ZeroTrace
# ==============================================

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import Dict, Optional, List
import os
import uuid
import json

from crypto.signal_core import (
    generate_identity,
    generate_onetime_prekeys,
    x3dh_sender,
    RatchetState,
    ratchet_encrypt,
    ratchet_decrypt
)

app = FastAPI(title="Signal v6 Backend")
from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # в проді звузити до свого домену
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ============================================================
# In-memory storage (Zero-Trace)
# ============================================================

USERS = {}              # user_id → user profile
PREKEYS = {}            # user_id → prekeys
SESSIONS = {}           # (sender, receiver) → RatchetState
CALL_CONNECTIONS = {}   # user_id → websocket
INBOX = {}  # user_id -> list of {"from": sender_id, "packet": {...}}
# ============================================================
# MODELS
# ============================================================

class RegisterPayload(BaseModel):
    username: str


class BundleResponse(BaseModel):
    identity_pub: str
    signed_prekey_pub: str
    signed_prekey_sig: str
    onetime_prekeys: list


class InitiateSessionPayload(BaseModel):
    sender_id: str
    receiver_id: str


class MessagePayload(BaseModel):
    sender_id: str
    receiver_id: str
    ciphertext: dict

class WipePayload(BaseModel):
    admin_secret: str


ZERO_TRACE_ADMIN_SECRET = "CHANGE_ME_ZERO_TRACE_SECRET"  # заміни в проді
class MessageSendPayload(BaseModel):
    sender_id: str
    receiver_id: str
    text: str  # звич
# ============================================================
# REGISTRATION
# ============================================================

@app.post("/register")
def register(data: RegisterPayload):
    user_id = str(uuid.uuid4())

    ident = generate_identity()
    onetime = generate_onetime_prekeys(20)

    USERS[user_id] = {
        "username": data.username,
        "identity_priv": ident.identity_priv,
        "identity_pub": ident.identity_pub,
        "signed_prekey_priv": ident.signed_prekey_priv,
        "signed_prekey_pub": ident.signed_prekey_pub,
        "signed_prekey_sig": ident.signed_prekey_sig,
    }

    PREKEYS[user_id] = onetime

    return {
        "user_id": user_id,
        "identity_pub": ident.identity_pub.hex(),
        "signed_prekey_pub": ident.signed_prekey_pub.hex(),
        "signed_prekey_sig": ident.signed_prekey_sig.hex(),
        "onetime_prekeys": [p["pub"] for p in onetime],
    }


# ============================================================
# FETCH BUNDLE (receiver keys)
# ============================================================

@app.get("/bundle/{user_id}")
def get_bundle(user_id: str):
    if user_id not in USERS:
        return {"error": "invalid user"}

    return {
        "identity_pub": USERS[user_id]["identity_pub"],
        "signed_prekey_pub": USERS[user_id]["signed_prekey_pub"],
        "signed_prekey_sig": USERS[user_id]["signed_prekey_sig"],
        "onetime_prekeys": [p["pub"] for p in PREKEYS[user_id]]
    }


# ============================================================
# SESSION INIT (X3DH)
# ============================================================

@app.post("/session/init")
def session_init(data: InitiateSessionPayload):
    sender = data.sender_id
    receiver = data.receiver_id

    if sender not in USERS or receiver not in USERS:
        return {"error": "invalid sender/receiver"}

    recv_bundle = {
        "identity_pub": USERS[receiver]["identity_pub"],
        "signed_prekey_pub": USERS[receiver]["signed_prekey_pub"],
        "signed_prekey_sig": USERS[receiver]["signed_prekey_sig"]
    }

    # 🔐 Візьмемо перший доступний one-time prekey (як у Signal)
    onetime_prekey_pub_bytes: Optional[bytes] = None

    if PREKEYS.get(receiver):
        # беремо і одразу видаляємо → одноразовий
        pk = PREKEYS[receiver].pop(0)
        onetime_prekey_pub_bytes = base64.b64decode(pk["pub"].encode())

    # sender ephemeral + identity priv
    eph_priv = os.urandom(32)
    IKs = USERS[sender]["identity_priv"]

    master_secret = x3dh_sender(
        IKs,
        eph_priv,
        recv_bundle,
        onetime_prekey_pub_b=onetime_prekey_pub_bytes
    )

    # INITIAL DOUBLE RATCHET STATE
    init_state = RatchetState(
        root_key=master_secret,
        chain_key_send=master_secret,
        chain_key_recv=master_secret,
        dh_priv=eph_priv,
        dh_pub=None,
        their_dh_pub=None
    )

    SESSIONS[(sender, receiver)] = init_state

    return {
        "status": "session_established",
        "used_one_time_prekey": bool(onetime_prekey_pub_bytes)
    }

# ============================================================
# SEND MESSAGE (E2EE)
# ============================================================


@app.post("/message/send")
def message_send(data: MessageSendPayload):
    key = (data.sender_id, data.receiver_id)

    if key not in SESSIONS:
        return {"error": "session not initialized"}

    state = SESSIONS[key]

    # 🔐 Шифруємо повідомлення через ратчет
    packet = ratchet_encrypt(state, data.text)

    # Кладемо зашифрований пакет у in-memory INBOX отримувача
    if data.receiver_id not in INBOX:
        INBOX[data.receiver_id] = []
    INBOX[data.receiver_id].append({
        "from": data.sender_id,
        "packet": packet
    })

    # Zero-Trace: нічого не зберігаємо на диск
    return {"status": "sent"}
    
@app.get("/message/poll/{user_id}")
def message_poll(user_id: str):
    """
    Отримати всі відкладені повідомлення для user_id.
    При цьому:
    - повідомлення дістаються з INBOX
    - дешифруються через Ratchet
    - INBOX очищається
    """
    msgs = INBOX.get(user_id, [])
    result: List[dict] = []

    for item in msgs:
        sender = item["from"]
        packet = item["packet"]

        key = (sender, user_id)
        if key not in SESSIONS:
            # немає сесії — пропускаємо
            continue

        state = SESSIONS[key]
        plaintext, new_state = ratchet_decrypt(state, packet)
        SESSIONS[key] = new_state

        result.append({
            "from": sender,
            "text": plaintext
        })

    # очищаємо inbox
    INBOX[user_id] = []

    return {"messages": result}

# ============================================================
# RECEIVE MESSAGE (E2EE)
# ============================================================

@app.post("/message/receive")
def receive_message(data: MessagePayload):
    key = (data.sender_id, data.receiver_id)

    if key not in SESSIONS:
        return {"error": "session not initialized"}

    state = SESSIONS[key]

    plaintext, new_state = ratchet_decrypt(state, data.ciphertext)

    SESSIONS[key] = new_state  # update state

    return {"plaintext": plaintext}


# ============================================================
# CALL SIGNALING OVER WEBSOCKET (WebRTC)
# ============================================================

from fastapi import WebSocket, WebSocketDisconnect
import json

CALL_CONNECTIONS = {}   # user_id → websocket

@app.websocket("/call/{user_id}")
async def ws_call(websocket: WebSocket, user_id: str):
    await websocket.accept()
    CALL_CONNECTIONS[user_id] = websocket

    try:
        while True:
            msg = await websocket.receive_text()
            payload = json.loads(msg)

            # Очікуємо структуру:
            # {
            #   "type": "offer"/"answer"/"ice"/"hangup",
            #   "from": "userA",
            #   "to": "userB",
            #   "data": {...}
            # }

            target = payload.get("to")
            if not target:
                continue

            if target in CALL_CONNECTIONS:
                await CALL_CONNECTIONS[target].send_text(msg)
            else:
                # опціонально: можна відправити назад помилку
                pass

    except WebSocketDisconnect:
        CALL_CONNECTIONS.pop(user_id, None)
        
        
        
проді


@app.post("/zerotrace/wipe")
def zerotrace_wipe(data: WipePayload):
    if data.admin_secret != ZERO_TRACE_ADMIN_SECRET:
        return {"error": "forbidden"}

    USERS.clear()
    PREKEYS.clear()
    SESSIONS.clear()
    CALL_CONNECTIONS.clear()

    return {"status": "wiped"}


    
