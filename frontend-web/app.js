// ========================================================
//   Signal Messenger v7 — App.js (оновлений під backend v7)
// ========================================================

// ⚙️ Backend URL
const API = "https://corp-production-0ac7.up.railway.app";
const WS_BACKEND = "wss://corp-production-0ac7.up.railway.app/call";

// ==============================
//   Global state
// ==============================
let myId = null;
let myName = null;

let peerId = null;
let peerName = null;

let ws = null;
let pc = null;
let localStream = null;

// ==============================
//   DOM elements
// ==============================
const ui = {
    // Auth
    btnRegister: document.getElementById("btn-register"),
    myIdInput: document.getElementById("my-id"),
    peerIdInput: document.getElementById("peer-id"),
    btnSession: document.getElementById("btn-session"),

    // Chat
    msgBox: document.getElementById("messages"),
    msgInput: document.getElementById("msg-input"),
    btnSend: document.getElementById("btn-send"),

    // WebRTC
    btnConnect: document.getElementById("btn-connect"),
    btnCall: document.getElementById("btn-call"),
    btnHangup: document.getElementById("btn-hangup"),
    localVideo: document.getElementById("localVideo"),
    remoteVideo: document.getElementById("remoteVideo"),
};

// ==============================
//   UI Helpers
// ==============================
function logMsg(text, mine = false) {
    const div = document.createElement("div");
    div.className = mine ? "msg me" : "msg other";
    div.innerText = text;
    ui.msgBox.appendChild(div);
    ui.msgBox.scrollTop = ui.msgBox.scrollHeight;
}

function error(msg) {
    alert("❌ " + msg);
}

// ==============================
//   1. Registration
// ==============================
ui.btnRegister.onclick = async () => {
    const username = prompt("Введи свій нікнейм:") || ("user-" + Math.floor(Math.random() * 99999));

    const res = await fetch(`${API}/register`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ username })
    });

    if (!res.ok) return error("Помилка реєстрації");

    const data = await res.json();
    
    myId = data.user_id;
    myName = username;

    ui.myIdInput.value = myId;
    ui.btnSession.disabled = false;

    logMsg(`✔ Зареєстровано як ${username}\nВаш ID: ${myId}`);
};

// ==============================
//   2. Session Init (X3DH)
// ==============================
ui.btnSession.onclick = async () => {
    myId = ui.myIdInput.value.trim();
    peerId = ui.peerIdInput.value.trim();

    if (!myId || !peerId) return error("Заповніть IDs");

    const res = await fetch(`${API}/session/init`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ sender_id: myId, receiver_id: peerId })
    });

    const data = await res.json();

    if (data.error) return error(data.error);

    ui.msgInput.disabled = false;
    ui.btnSend.disabled = false;

    logMsg("🔐 Secure session established with " + peerId);

    startPolling();
};

// ==============================
//   3. Sending messages
// ==============================
ui.btnSend.onclick = async () => {
    const text = ui.msgInput.value.trim();
    if (text === "") return;

    logMsg(`${myName}: ${text}`, true);

    await fetch(`${API}/message/send`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
            sender_id: myId,
            receiver_id: peerId,
            text
        })
    });

    ui.msgInput.value = "";
};

// ==============================
//   4. Polling incoming messages
// ==============================
async function poll() {
    if (!myId) return;

    const res = await fetch(`${API}/message/poll/${myId}`);
    if (!res.ok) return;

    const data = await res.json();

    for (const msg of data.messages) {
        logMsg(`${msg.from_name}: ${msg.text}`, false);
    }
}

function startPolling() {
    setInterval(poll, 1200);
}

// ======================================================
//   5. WebRTC (Video + Audio Calls)
// ======================================================
const rtcConfig = {
    iceServers: [
        { urls: "stun:stun.l.google.com:19302" }
    ]
};

ui.btnConnect.onclick = () => {
    if (!myId) return error("Спочатку зареєструйся");

    ws = new WebSocket(`${WS_BACKEND}/${myId}`);

    ws.onopen = () => {
        console.log("WS connected");
        ui.btnCall.disabled = false;
    };

    ws.onmessage = async (event) => {
        const msg = JSON.parse(event.data);

        if (!pc) await createPeer(msg.from);

        if (msg.type === "offer") {
            await pc.setRemoteDescription(msg.data);
            const answer = await pc.createAnswer();
            await pc.setLocalDescription(answer);
            sendSignal("answer", msg.from, answer);

        } else if (msg.type === "answer") {
            await pc.setRemoteDescription(msg.data);

        } else if (msg.type === "ice") {
            if (msg.data) pc.addIceCandidate(msg.data);

        } else if (msg.type === "hangup") {
            endCall();
        }
    };
};

// -------------------------
//   Create Peer Connection
// -------------------------
async function createPeer(targetId) {
    peerId = targetId;

    pc = new RTCPeerConnection(rtcConfig);

    localStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    ui.localVideo.srcObject = localStream;

    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));

    pc.onicecandidate = (e) => {
        if (e.candidate) sendSignal("ice", peerId, e.candidate);
    };

    pc.ontrack = (e) => {
        ui.remoteVideo.srcObject = e.streams[0];
    };
}

// -------------------------
//   Start Call
// -------------------------
ui.btnCall.onclick = async () => {
    if (!peerIdInput.value.trim()) return error("Введи peer ID");

    peerId = peerIdInput.value.trim();

    await createPeer(peerId);

    const offer = await pc.createOffer();
    await pc.setLocalDescription(offer);

    sendSignal("offer", peerId, offer);

    ui.btnHangup.disabled = false;
};

// -------------------------
//   End Call
// -------------------------
ui.btnHangup.onclick = () => {
    sendSignal("hangup", peerId, {});
    endCall();
};

function endCall() {
    if (pc) {
        pc.close();
        pc = null;
    }

    ui.remoteVideo.srcObject = null;
    ui.btnHangup.disabled = true;
}

// -------------------------
//   Send WebRTC Signal
// -------------------------
function sendSignal(type, to, data) {
    if (!ws || ws.readyState !== WebSocket.OPEN) return;

    ws.send(JSON.stringify({
        type,
        from: myId,
        to,
        data
    }));
}