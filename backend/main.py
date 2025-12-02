# ==============================================
#   SIGNAL MESSENGER V6 — BACKEND (FIXED)
#   FastAPI + X3DH + Symmetric Ratchet + ZeroTrace
# ==============================================

import uuid, os, json, base64
from typing import Dict, Optional, List, Tuple

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from crypto.signal_core import (
    generate_identity,
    generate_onetime_prekeys,
    generate_ephemeral_key_b64,
    x3dh_sender,
    RatchetState,
    ratchet_encrypt,
    ratchet_decrypt
)

# ==============================================
#   APP + CORS
# ==============================================

app = FastAPI(title="Signal v6 Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # В проді вкажи свій домен
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================
#   ZERO-TRACE RAM STORAGE
# ==============================================

USERS: Dict[str, dict] = {}
PREKEYS: Dict[str, List[dict]] = {}
SESSIONS: Dict[Tuple[str, str], RatchetState] = {}
INBOX: Dict[str, List[dict]] = {}
CALL_CONNECTIONS: Dict[str, WebSocket] = {}

ZERO_TRACE_ADMIN_SECRET = "CHANGE_ME_ZERO_TRACE_SECRET"


# ==============================================
#   MODELS
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

class MessagePayload(BaseModel):
    sender_id: str
    receiver_id: str
    ciphertext: dict

class WipePayload(BaseModel):
    admin_secret: str


# ==============================================
#   REGISTRATION
# ==============================================

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


# ==============================================
#   FETCH BUNDLE
# ==============================================

@app.get("/bundle/{user_id}")
def get_bundle(user_id: str):
    if user_id not in USERS:
        return {"error": "invalid user"}

    return {
        "identity_pub": USERS[user_id]["identity_pub_b64"],
        "signed_prekey_pub": USERS[user_id]["signed_prekey_pub_b64"],
        "signed_prekey_sig": USERS[user_id]["signed_prekey_sig_b64"],
        "onetime_prekeys": [pk["pub_b64"] for pk in PREKEYS[user_id]]
    }


# ==============================================
#   SESSION INIT (X3DH)
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

    # One-time prekey
    onetime_prekey_pub_b64 = None
    if PREKEYS.get(receiver):
        pk = PREKEYS[receiver].pop(0)
        onetime_prekey_pub_b64 = pk["pub_b64"]

    master_secret = x3dh_sender(
        identity_priv_b64=USERS[sender]["identity_priv_b64"],
        eph_priv_b64=generate_ephemeral_key_b64(),
        recv_bundle=recv_bundle,
        onetime_prekey_pub_b64=onetime_prekey_pub_b64
    )

    # Стартовий стан ратчета
    init_state = RatchetState(
        root_key=master_secret,
        chain_key_send=master_secret,
        chain_key_recv=master_secret
    )

    SESSIONS[(sender, receiver)] = init_state

    return {
        "status": "session_established",
        "used_one_time_prekey": bool(onetime_prekey_pub_b64)
    }


# ==============================================
#   SEND MESSAGE (ENCRYPT)
# ==============================================

@app.post("/message/send")
def message_send(data: MessageSendPayload):
    key = (data.sender_id, data.receiver_id)

    if key not in SESSIONS:
        return {"error": "session not initialized"}

    packet = ratchet_encrypt(SESSIONS[key], data.text)

    INBOX.setdefault(data.receiver_id, []).append({
        "from": data.sender_id,
        "packet": packet
    })

    return {"status": "sent"}


# ==============================================
#   POLL MESSAGE (DECRYPT)
# ==============================================

@app.get("/message/poll/{user_id}")
def message_poll(user_id: str):
    msgs = INBOX.get(user_id, [])
    result = []

    for item in msgs:
        sender = item["from"]
        packet = item["packet"]

        key = (sender, user_id)
        if key not in SESSIONS:
            continue

        plaintext, new_state = ratchet_decrypt(SESSIONS[key], packet)
        SESSIONS[key] = new_state

        result.append({"from": sender, "text": plaintext})

    INBOX[user_id] = []
    return {"messages": result}


# ==============================================
#   RAW RECEIVE (debug only)
# ==============================================

@app.post("/message/receive")
def receive_message(data: MessagePayload):
    key = (data.sender_id, data.receiver_id)
    if key not in SESSIONS:
        return {"error": "session not initialized"}

    plaintext, new_state = ratchet_decrypt(SESSIONS[key], data.ciphertext)
    SESSIONS[key] = new_state

    return {"plaintext": plaintext}


# ==============================================
#   WebRTC CALL SIGNALING (WS)
# ==============================================

@app.websocket("/call/{user_id}")
async def call_socket(ws: WebSocket, user_id: str):
    await ws.accept()
    CALL_CONNECTIONS[user_id] = ws

    try:
        while True:
            msg = await ws.receive_text()
            payload = json.loads(msg)
            target = payload.get("to")

            if target in CALL_CONNECTIONS:
                await CALL_CONNECTIONS[target].send_text(msg)

    except WebSocketDisconnect:
        CALL_CONNECTIONS.pop(user_id, None)

@app.post("/message/decrypt")
def message_decrypt(data: dict):
    sender = data.get("sender_id")
    receiver = data.get("receiver_id")
    packet = data.get("package")

    key = (sender, receiver)

    if key not in SESSIONS:
        return {"error": "session not initialized"}

    state = SESSIONS[key]

    try:
        plaintext, new_state = ratchet_decrypt(state, packet)
        SESSIONS[key] = new_state
        return {"plaintext": plaintext}

    except Exception as e:
        print("Decrypt error:", e)
        return {"error": "decrypt_failed"}
# ==============================================
#   ZERO TRACE WIPE
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
#   RUN (dev)
# ==============================================

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)