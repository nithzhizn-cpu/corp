from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import uuid, json
from typing import Dict, List, Tuple

from crypto.signal_core import (
    generate_identity,
    generate_onetime_prekeys,
    generate_ephemeral_key_b64,
    x3dh_sender,
    RatchetState,
    ratchet_encrypt,
    ratchet_decrypt
)

# ============================================================
#   APP + CORS (ПРАЦЮЮЧА ВЕРСІЯ)
# ============================================================

app = FastAPI()

# 🔥 CORS — працює для ВСІХ методів AUTOMATICALLY
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
#   ZERO TRACE STORAGE
# ============================================================

USERS: Dict[str, dict] = {}
PREKEYS: Dict[str, List[dict]] = {}
SESSIONS: Dict[Tuple[str, str], RatchetState] = {}
INBOX: Dict[str, List[dict]] = {}
CALLS: Dict[str, WebSocket] = {}

# ============================================================
#   MODELS
# ============================================================

from pydantic import BaseModel

class RegisterPayload(BaseModel):
    username: str

class InitiateSessionPayload(BaseModel):
    sender_id: str
    receiver_id: str

class MessageSendPayload(BaseModel):
    sender_id: str
    receiver_id: str
    text: str

# ============================================================
#   REGISTER
# ============================================================

@app.post("/register")
def register(data: RegisterPayload):
    user_id = str(uuid.uuid4())

    ident = generate_identity()
    prekeys = generate_onetime_prekeys(20)

    USERS[user_id] = ident
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
#   INIT SESSION
# ============================================================

@app.post("/session/init")
def session_init(data: InitiateSessionPayload):
    s = data.sender_id
    r = data.receiver_id

    recv_bundle = {
        "identity_pub_b64": USERS[r]["identity_pub_b64"],
        "signed_prekey_pub_b64": USERS[r]["signed_prekey_pub_b64"],
        "signed_prekey_sig_b64": USERS[r]["signed_prekey_sig_b64"],
    }

    onetime = None
    if PREKEYS[r]:
        onetime = PREKEYS[r].pop(0)["pub_b64"]

    secret = x3dh_sender(
        identity_priv_b64=USERS[s]["identity_priv_b64"],
        eph_priv_b64=generate_ephemeral_key_b64(),
        recv_bundle=recv_bundle,
        onetime_prekey_pub_b64=onetime
    )

    SESSIONS[(s, r)] = RatchetState(
        root_key=secret,
        chain_key_send=secret + b"A",
        chain_key_recv=secret + b"B"
    )
    SESSIONS[(r, s)] = RatchetState(
        root_key=secret,
        chain_key_send=secret + b"B",
        chain_key_recv=secret + b"A"
    )

    return {"status": "session_established"}

# ============================================================
#   SEND MESSAGE
# ============================================================

@app.post("/message/send")
def send_message(data: MessageSendPayload):
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
#   POLL MESSAGES  (🔥 FIXED CORS)
# ============================================================

@app.get("/message/poll/{user_id}")
def poll(user_id: str):
    msgs = INBOX.get(user_id, [])
    out = []

    for item in msgs:
        sender = item["from"]
        packet = item["packet"]
        session_key = (sender, user_id)

        if session_key in SESSIONS:
            plaintext, new_state = ratchet_decrypt(SESSIONS[session_key], packet)
            SESSIONS[session_key] = new_state
            out.append({"from": sender, "text": plaintext})

    INBOX[user_id] = []
    return {"messages": out}

# ============================================================
#   RUN SERVER
# ============================================================

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000)