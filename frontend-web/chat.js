// ===============================
//   Signal v6.2 — Chat Frontend (with usernames)
// ===============================

const API = "https://corp-production-0ac7.up.railway.app";

// DOM
const btnRegister = document.getElementById("btn-register");
const btnSession  = document.getElementById("btn-session");
const btnSend     = document.getElementById("send-btn");

const nickInput   = document.getElementById("nick-input");
const myIdInput   = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");

const textInput   = document.getElementById("text-input");
const messagesBox = document.getElementById("messages");

const headerMyName   = document.getElementById("header-my-name");       // 👈 ДОДАЙ В HTML
const headerPeerName = document.getElementById("header-peer-name");     // 👈 ДОДАЙ В HTML

let myId = null;
let myName = null;

let peerId = null;
let peerName = null;

// ===============================
//  Helpers
// ===============================
function addMessage(text, mine = false) {
    const div = document.createElement("div");
    div.className = "msg " + (mine ? "me" : "other");
    div.textContent = text;
    messagesBox.appendChild(div);
    messagesBox.scrollTop = messagesBox.scrollHeight;
}

// ===============================
// 1. Registration with nickname
// ===============================
btnRegister.onclick = async () => {
    const username = (nickInput.value || "").trim();

    if (!username) {
        alert("Введи нікнейм!");
        return;
    }

    try {
        const res = await fetch(`${API}/register`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username })
        });

        const data = await res.json();

        if (!data.user_id) {
            alert("❌ Помилка реєстрації");
            return;
        }

        myId = data.user_id;
        myName = username;

        myIdInput.value = myId;
        headerMyName.textContent = username;     // 👈 ПОКАЗУЄМО НІК В ХЕДЕРІ

        btnSession.disabled = false;

        addMessage(`✔ Зареєстровано!\nНік: ${username}\nID: ${myId}`);
    }
    catch (e) {
        console.error(e);
        alert("Помилка мережі");
    }
};

// ===============================
// 2. Init Secure Session
// ===============================
btnSession.onclick = async () => {
    myId = myIdInput.value.trim();
    peerId = peerIdInput.value.trim();

    if (!myId || !peerId) {
        alert("Введи свій та чужий ID");
        return;
    }

    // ❗️ Витягуємо нік співрозмовника
    const bundleRes = await fetch(`${API}/bundle/${peerId}`);
    const bundleData = await bundleRes.json();

    peerName = bundleData.username || "Співрозмовник";

    headerPeerName.textContent = peerName;     // 👈 показуємо нік зверху

    // Ініціалізуємо сесію
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

    addMessage(`🔐 Захищена сесія встановлена з ${peerName}`);

    btnSend.disabled = false;
    textInput.disabled = false;

    startPolling();
};

// ===============================
// 3. Send message
// ===============================
btnSend.onclick = async () => {
    const text = textInput.value.trim();
    if (!text) return;

    addMessage(`${myName}: ${text}`, true);

    await fetch(`${API}/message/send`, {
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

// ===============================
// 4. Poll messages
// ===============================
async function pollMessages() {
    if (!myId) return;

    try {
        const res = await fetch(`${API}/message/poll/${myId}`);
        if (!res.ok) return;

        const data = await res.json();

        (data.messages || []).forEach(m => {
            const name = m.from_name || m.from || "???";
            addMessage(`${name}: ${m.text}`, false);
        });
    }
    catch (e) {
        console.log("Polling error", e);
    }
}

function startPolling() {
    setInterval(pollMessages, 1200);
}