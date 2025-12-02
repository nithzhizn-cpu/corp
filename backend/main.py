# ==============================================
#   Signal v6 — Backend
#   FastAPI + X3DH-style KDF + Symmetric Ratchet
#   Zero-Trace (in-memory only)
# ==============================================

import json
from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from crypto.signal_core import (
    generate_identity,
    generate_onetime_prekeys,
    x3dh_sender,
    RatchetState,
    ratchet_encrypt,
    ratchet_decrypt,
)

app = FastAPI(title="Signal v6 Backend")

# CORS (спростимо для MVP; в проді звузь до свого домену)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================
# In-memory storage (Zero-Trace)
# ==============================================

USERS: Dict[str, dict] = {}            # user_id -> profile + keys
PREKEYS: Dict[str, List[dict]] = {}    # user_id -> list of one-time prekeys
SESSIONS: Dict[Tuple[str, str], RatchetState] = {}  # (sender, receiver) -> ratchet state
INBOX: Dict[str, List[dict]] = {}      # user_id -> list of {"from": sender_id, "packet": {...}}
CALL_CONNECTIONS: Dict[str, WebSocket] = {}  # user_id -> websocket

# секрет для wipe (в проді винеси в ENV)
ZERO_TRACE_ADMIN_SECRET = "CHANGE_ME_ZERO_TRACE_SECRET"


# ==============================================
# Models
# ==============================================

class RegisterPayload(BaseModel):
    username: str


class InitiateSessionPayload(BaseModel):
    sender_id: str
    receiver_id: str


class MessageSendPayload(BaseModel):
    sender_id: str
    receiver_id: str
    text: str


class WipePayload(BaseModel):
    admin_secret: str


# ==============================================
# Health
# ==============================================

@app.get("/health")
def health():
    return {"status": "ok"}


# ==============================================
# Registration
# ==============================================

@app.post("/register")
def register(data: RegisterPayload):
    """
    Створює нового користувача:
    - identity key pair
    - signed prekey
    - one-time prekeys
    """
    import uuid

    user_id = str(uuid.uuid4())

    ident = generate_identity()
    onetime = generate_onetime_prekeys(20)

    USERS[user_id] = {
        "username": data.username,
        "identity_priv_b64": ident.identity_priv_b64,
        "identity_pub_b64": ident.identity_pub_b64,
        "signed_prekey_priv_b64": ident.signed_prekey_priv_b64,
        "signed_prekey_pub_b64": ident.signed_prekey_pub_b64,
        "signed_prekey_sig_b64": ident.signed_prekey_sig_b64,
    }

    PREKEYS[user_id] = onetime
    INBOX[user_id] = []

    return {
        "user_id": user_id,
        "identity_pub_b64": ident.identity_pub_b64,
        "signed_prekey_pub_b64": ident.signed_prekey_pub_b64,
        "signed_prekey_sig_b64": ident.signed_prekey_sig_b64,
        "onetime_prekeys_pub_b64": [p["pub_b64"] for p in onetime],
    }


# ==============================================
# Bundle (отримати публічний набір ключів)
# ==============================================

@app.get("/bundle/{user_id}")
def get_bundle(user_id: str):
    if user_id not in USERS:
        return {"error": "invalid user"}

    u = USERS[user_id]
    return {
        "identity_pub_b64": u["identity_pub_b64"],
        "signed_prekey_pub_b64": u["signed_prekey_pub_b64"],
        "signed_prekey_sig_b64": u["signed_prekey_sig_b64"],
        "onetime_prekeys_pub_b64": [p["pub_b64"] for p in PREKEYS.get(user_id, [])],
    }


# ==============================================
# Session init (X3DH-like)
# ==============================================

@app.post("/session/init")
def session_init(data: InitiateSessionPayload):
    sender = data.sender_id
    receiver = data.receiver_id

    if sender not in USERS or receiver not in USERS:
        return {"error": "invalid sender/receiver"}

    recv_bundle = {
        "identity_pub_b64": USERS[receiver]["identity_pub_b64"],
        "signed_prekey_pub_b64": USERS[receiver]["signed_prekey_pub_b64"],
        "signed_prekey_sig_b64": USERS[receiver]["signed_prekey_sig_b64"],
    }

    # беремо один одноразовий prekey для отримувача
    onetime_prekey_pub_b64 = None
    if PREKEYS.get(receiver):
        pk = PREKEYS[receiver].pop(0)
        onetime_prekey_pub_b64 = pk["pub_b64"]

    # sender identity priv + eph priv
    IKs_priv_b64 = USERS[sender]["identity_priv_b64"]

    from crypto.signal_core import generate_ephemeral_key_b64
    eph_priv_b64 = generate_ephemeral_key_b64()

    master_secret = x3dh_sender(
        identity_priv_b64=IKs_priv_b64,
        eph_priv_b64=eph_priv_b64,
        recv_bundle=recv_bundle,
        onetime_prekey_pub_b64=onetime_prekey_pub_b64,
    )

    # початковий стан ратчета (симетричний)
    init_state = RatchetState(
        root_key=master_secret,
        chain_key_send=master_secret,
        chain_key_recv=master_secret,
    )

    SESSIONS[(sender, receiver)] = init_state

    return {
        "status": "session_established",
        "used_one_time_prekey": bool(onetime_prekey_pub_b64),
    }


# ==============================================
# Encrypted messaging (send / poll)
# ==============================================

@app.post("/message/send")
def message_send(data: MessageSendPayload):
    key = (data.sender_id, data.receiver_id)

    if key not in SESSIONS:
        return {"error": "session not initialized"}

    state = SESSIONS[key]

    packet = ratchet_encrypt(state, data.text)

    if data.receiver_id not in INBOX:
        INBOX[data.receiver_id] = []

    INBOX[data.receiver_id].append({
        "from": data.sender_id,
        "packet": packet,
    })

    return {"status": "sent"}


@app.get("/message/poll/{user_id}")
def message_poll(user_id: str):
    msgs = INBOX.get(user_id, [])
    result: List[dict] = []

    for item in msgs:
        sender = item["from"]
        packet = item["packet"]
        key = (sender, user_id)

        if key not in SESSIONS:
            continue

        state = SESSIONS[key]
        plaintext, new_state = ratchet_decrypt(state, packet)
        SESSIONS[key] = new_state

        result.append({
            "from": sender,
            "text": plaintext,
        })

    INBOX[user_id] = []
    return {"messages": result}


# ==============================================
# Zero-Trace Wipe
# ==============================================

@app.post("/zerotrace/wipe")
def zerotrace_wipe(data: WipePayload):
    if data.admin_secret != ZERO_TRACE_ADMIN_SECRET:
        return {"error": "forbidden"}

    USERS.clear()
    PREKEYS.clear()
    SESSIONS.clear()
    INBOX.clear()
    CALL_CONNECTIONS.clear()

    return {"status": "wiped"}


# ==============================================
# WebRTC signaling (calls)
# ==============================================

@app.websocket("/call/{user_id}")
async def ws_call(websocket: WebSocket, user_id: str):
    await websocket.accept()
    CALL_CONNECTIONS[user_id] = websocket

    try:
        while True:
            msg = await websocket.receive_text()
            payload = json.loads(msg)
            target = payload.get("to")
            if not target:
                continue

            if target in CALL_CONNECTIONS:
                await CALL_CONNECTIONS[target].send_text(msg)
    except WebSocketDisconnect:
        CALL_CONNECTIONS.pop(user_id, None)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
