# ============================================================
#   SIGNAL MESSENGER v7 — BACKEND
#   FastAPI + X3DH + Symmetric Double Ratchet + ZeroTrace RAM
#   Secure Messaging + WebRTC Signaling Server
# ============================================================

import uuid
import json
from typing import Dict, List, Tuple

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from crypto.signal_core import (
    generate_identity,
    generate_onetime_prekeys,
    generate_ephemeral_key_b64,
    generate_ephemeral_keypair,
    x3dh_sender,
    RatchetState,
    ratchet_encrypt,
    ratchet_decrypt,
)

# ============================================================
#   APP + CORS
# ============================================================

app = FastAPI(title="Signal v7 Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*",  # 🔥 дозвіл для всіх фронтів (простий запуск)
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#   ZERO-TRACE STORAGE (RAM only)
# ============================================================

USERS: Dict[str, dict] = {}
PREKEYS: Dict[str, List[dict]] = {}
SESSIONS: Dict[Tuple[str, str], RatchetState] = {}
INBOX: Dict[str, List[dict]] = {}
CALL_CONNECTIONS: Dict[str, WebSocket] = {}

ZERO_TRACE_SECRET = "SET_YOUR_SECRET"


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
#   REGISTER USER
# ============================================================

@app.post("/register")
def register(data: RegisterPayload):
    user_id = str(uuid.uuid4())

    ident = generate_identity()
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
        "onetime_prekeys": [pk["pub_b64"] for pk in prekeys]
    }


# ============================================================
#   GET BUNDLE
# ============================================================

@app.get("/bundle/{user_id}")
def get_bundle(user_id: str):
    if user_id not in USERS:
        return {"error": "invalid user"}

    return {
        "identity_pub": USERS[user_id]["identity_pub_b64"],
        "signed_prekey_pub": USERS[user_id]["signed_prekey_pub_b64"],
        "signed_prekey_sig": USERS[user_id]["signed_prekey_sig_b64"],
        "onetime_prekeys": [pk["pub_b64"] for pk in PREKEYS.get(user_id, [])]
    }


# ============================================================
#   INIT SIGNAL SESSION (X3DH + Double Ratchet)
# ============================================================

@app.post("/session/init")
def session_init(data: InitiateSessionPayload):
    s = data.sender_id
    r = data.receiver_id

    if s not in USERS or r not in USERS:
        return {"error": "invalid sender/receiver"}

    recv_bundle = {
        "identity_pub_b64": USERS[r]["identity_pub_b64"],
        "signed_prekey_pub_b64": USERS[r]["signed_prekey_pub_b64"],
        "signed_prekey_sig_b64": USERS[r]["signed_prekey_sig_b64"],
    }

    onetime = None
    if PREKEYS.get(r):
        pk = PREKEYS[r].pop(0)
        onetime = pk["pub_b64"]

    master_secret = x3dh_sender(
        identity_priv_b64=USERS[s]["identity_priv_b64"],
        eph_priv_b64=generate_ephemeral_key_b64(),
        recv_bundle=recv_bundle,
        onetime_prekey_pub_b64=onetime
    )

    dh_pub, dh_priv = generate_ephemeral_keypair()

    SESSIONS[(s, r)] = RatchetState(
        root_key=master_secret,
        chain_key_send=master_secret + b"A",
        chain_key_recv=master_secret + b"B",
    )
    SESSIONS[(r, s)] = RatchetState(
        root_key=master_secret,
        chain_key_send=master_secret + b"B",
        chain_key_recv=master_secret + b"A",
    )

    return {"status": "session_established"}


# ============================================================
#   ENCRYPT & SEND MESSAGE
# ============================================================

@app.post("/message/send")
def message_send(data: MessageSendPayload):
    key = (data.sender_id, data.receiver_id)
    if key not in SESSIONS:
        return {"error": "session not initialized"}

    packet = ratchet_encrypt(SESSIONS[key], data.text)

    INBOX[data.receiver_id].append({
        "from": data.sender_id,
        "packet": packet
    })

    return {"status": "sent"}


# ============================================================
#   POLL MESSAGES (DELIVER & DECRYPT)
# ============================================================

@app.get("/message/poll/{user_id}")
def poll(user_id: str):
    msgs = INBOX.get(user_id, [])
    result = []

    for item in msgs:
        sender = item["from"]
        packet = item["packet"]

        key = (sender, user_id)
        if key in SESSIONS:
            plaintext, new_state = ratchet_decrypt(SESSIONS[key], packet)
            SESSIONS[key] = new_state
            result.append({"from": sender, "text": plaintext})

    INBOX[user_id] = []  # zero-trace inbox
    return {"messages": result}


# ============================================================
#   CALL SIGNALING (WebRTC)
# ============================================================

@app.websocket("/call/{user_id}")
async def call_socket(ws: WebSocket, user_id: str):
    await ws.accept()
    CALL_CONNECTIONS[user_id] = ws

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            target = msg.get("to")

            if target in CALL_CONNECTIONS:
                await CALL_CONNECTIONS[target].send_text(raw)

    except WebSocketDisconnect:
        CALL_CONNECTIONS.pop(user_id, None)


# ============================================================
#   ZERO-TRACE WIPE
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
    uvicorn.run("main:app", host="0.0.0.0", port=8000)