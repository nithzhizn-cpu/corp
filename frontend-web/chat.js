// ===============================
//   Signal v6.2 — Chat Frontend
// ===============================

// Пропиши свій бекенд:
const API = "https://corp-production-0ac7.up.railway.app";

// DOM
const btnRegister = document.getElementById("btn-register");
const btnSession  = document.getElementById("btn-session");
const btnSend     = document.getElementById("send-btn");

const myIdInput   = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");
const nickInput   = document.getElementById("nick-input");   // 👈 інпут з ніком

const textInput   = document.getElementById("text-input");
const messagesBox = document.getElementById("messages");

let myId   = null;
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
//   1. Registration by nickname
// -------------------------------
btnRegister.onclick = async () => {
  const username = (nickInput.value || "").trim();

  if (!username) {
    alert("Введи нікнейм перед реєстрацією");
    return;
  }

  try {
    const res = await fetch(`${API}/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username })
    });

    if (!res.ok) {
      alert("❌ Помилка реєстрації (бекенд недоступний)");
      return;
    }

    const data = await res.json();
    if (!data.user_id) {
      console.error(data);
      alert("❌ Помилка реєстрації (немає user_id)");
      return;
    }

    myId = data.user_id;
    myIdInput.value = myId;

    btnSession.disabled = false;

    addMessage(`✔ Реєстрація успішна! Нік: ${username}\nID: ${myId}`);
  } catch (e) {
    console.error(e);
    alert("❌ Помилка мережі при реєстрації");
  }
};

// -------------------------------
//   2. X3DH + Double Ratchet Init
// -------------------------------
btnSession.onclick = async () => {
  myId   = myIdInput.value.trim();
  peerId = peerIdInput.value.trim();

  if (!myId || !peerId) {
    alert("Введи свій ID і ID співрозмовника");
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
      alert("Не вдалося створити сесію: " + data.error);
      return;
    }

    addMessage("🔐 Secure Session Established → " + peerId);

    textInput.disabled = false;
    btnSend.disabled = false;

    startPolling();
  } catch (e) {
    console.error(e);
    alert("❌ Помилка мережі при створенні сесії");
  }
};

// -------------------------------
//   3. Sending encrypted message
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
  } catch (e) {
    console.error(e);
    addMessage("⚠ Не вдалося надіслати (мережа)", true);
  }

  textInput.value = "";
};

// -------------------------------
//   4. Polling incoming messages
// -------------------------------
async function pollMessages() {
  if (!myId) return;

  try {
    const res = await fetch(`${API}/message/poll/${myId}`);
    if (!res.ok) return;

    const data = await res.json();
    const msgs = data.messages || [];

    msgs.forEach(m => {
      const name = m.from_name || m.from || "unknown";
      addMessage(`${name}: ${m.text}`, false);
    });
  } catch (e) {
    console.error("Polling error:", e);
  }
}

function startPolling() {
  setInterval(pollMessages, 1200);
}