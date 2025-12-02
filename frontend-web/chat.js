// ===============================
//   Signal v6.2 — Chat Frontend
// ===============================

// ⚠️ Пропиши тут свій бекенд:
const API = "https://corp-production-0ac7.up.railway.app";

// DOM
const btnRegister = document.getElementById("btn-register");
const btnSession = document.getElementById("btn-session");
const btnSend = document.getElementById("send-btn");

const myIdInput = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");
const textInput = document.getElementById("text-input");
const messagesBox = document.getElementById("messages");

let myId = null;
let peerId = null;

// ---------------
//  UI Helpers
// ---------------
function addMessage(text, mine = false) {
    const div = document.createElement("div");
    div.className = "msg " + (mine ? "me" : "other");
    div.textContent = text;
    messagesBox.appendChild(div);
    messagesBox.scrollTop = messagesBox.scrollHeight;
}

// -------------------------------
//   1. Registration
// -------------------------------
btnRegister.onclick = async () => {
    const username = "user-" + Math.floor(Math.random() * 99999);

    const res = await fetch(`${API}/register`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username })
    });

    if (!res.ok) {
        alert("❌ Помилка реєстрації");
        return;
    }

    const data = await res.json();
    myId = data.user_id;
    myIdInput.value = myId;

    btnSession.disabled = false;

    addMessage("✔ Реєстрація успішна! Ваш ID:\n" + myId);
};

// -------------------------------
//   2. X3DH + Double Ratchet Init
// -------------------------------
btnSession.onclick = async () => {
    myId = myIdInput.value.trim();
    peerId = peerIdInput.value.trim();

    if (!myId || !peerId) {
        alert("Введи свій та чужий ID");
        return;
    }

    const res = await fetch(`${API}/session/init`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            sender_id: myId,
            receiver_id: peerId
        })
    });

    const data = await res.json();
    if (data.error) {
        alert("Не вдалося створити сесію: " + data.error);
        return;
    }

    addMessage("🔐 Secure session established with: " + peerId);

    // даємо змогу писати
    textInput.disabled = false;
    btnSend.disabled = false;

    // запускаємо polling
    startPolling();
};

// -------------------------------
//   3. Sending encrypted message
// -------------------------------
btnSend.onclick = async () => {
    const text = textInput.value.trim();
    if (!text) return;

    addMessage(text, true);

    const res = await fetch(`${API}/message/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            sender_id: myId,
            receiver_id: peerId,
            text
        })
    });

    textInput.value = "";
};

// -------------------------------
//   4. Polling incoming messages
// -------------------------------
async function pollMessages() {
    if (!myId) return;

    const res = await fetch(`${API}/message/poll/${myId}`);
    if (!res.ok) return;

    const data = await res.json();
    const msgs = data.messages || [];

    msgs.forEach(m => {
        addMessage(m.from + ": " + m.text, false);
    });
}

function startPolling() {
    setInterval(pollMessages, 1200);
}