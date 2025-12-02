// ===============================
//   Signal v7 — Chat Frontend
//   Fully Fixed + CORS Safe
// ===============================

// ⚠️ Пропиши URL бекенду:
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

// -------------------------------
//  UI Helper
// -------------------------------
function addMessage(text, mine = false) {
    const div = document.createElement("div");
    div.className = "msg " + (mine ? "me" : "other");
    div.textContent = text;
    messagesBox.appendChild(div);
    messagesBox.scrollTop = messagesBox.scrollHeight;
}

// -------------------------------
//  1. Registration
// -------------------------------
btnRegister.onclick = async () => {
    const username = "user-" + Math.floor(Math.random() * 99999);

    try {
        const res = await fetch(`${API}/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username })
        });

        if (!res.ok) throw new Error("Registration failed");

        const data = await res.json();

        myId = data.user_id;
        myIdInput.value = myId;
        btnSession.disabled = false;

        addMessage("✔ Реєстрація успішна! Ваш ID:\n" + myId);
    } catch (err) {
        console.error(err);
        alert("❌ Помилка реєстрації (CORS або бекенд не працює)");
    }
};

// -------------------------------
//  2. Create X3DH + Double Ratchet session
// -------------------------------
btnSession.onclick = async () => {
    myId = myIdInput.value.trim();
    peerId = peerIdInput.value.trim();

    if (!myId || !peerId) {
        alert("Введи свій ID і ID абонента");
        return;
    }

    try {
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
            alert("❌ Помилка створення сесії: " + data.error);
            return;
        }

        addMessage("🔐 Secure Session Established → " + peerId);

        textInput.disabled = false;
        btnSend.disabled = false;

        startPolling();
    } catch (err) {
        console.error(err);
        alert("❌ Помилка встановлення сесії (CORS або бекенд)");
    }
};

// -------------------------------
//  3. Send encrypted message
// -------------------------------
btnSend.onclick = async () => {
    const text = textInput.value.trim();
    if (!text) return;

    addMessage(text, true);

    try {
        await fetch(`${API}/message/send`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                sender_id: myId,
                receiver_id: peerId,
                text
            })
        });
    } catch (err) {
        console.error(err);
        alert("❌ Помилка надсилання");
    }

    textInput.value = "";
};

// -------------------------------
//  4. Poll incoming messages
// -------------------------------
async function pollMessages() {
    if (!myId) return;

    try {
        const res = await fetch(`${API}/message/poll/${myId}`, {
            method: "GET",
            headers: { "Accept": "application/json" }
        });

        if (!res.ok) {
            console.warn("❌ pollMessages error:", res.status);
            return;
        }

        const data = await res.json();
        const msgs = data.messages || [];

        msgs.forEach(m => {
            addMessage(`${m.from}: ${m.text}`, false);
        });
    } catch (err) {
        console.warn("Polling error:", err);
    }
}

function startPolling() {
    setInterval(pollMessages, 1200);
}