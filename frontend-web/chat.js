// =============================
//   Signal v6 — Chat frontend
// =============================

// Якщо бекенд на тому ж домені: залишаємо порожнім
// Якщо окремий домен на Railway — вкажеш типу "https://your-backend.up.railway.app"
const API_BASE = "";

// DOM
const usernameInput = document.getElementById("username");
const btnRegister = document.getElementById("btn-register");
const myIdInput = document.getElementById("my-id");
const myIdBadge = document.getElementById("my-id-badge");
const peerIdInput = document.getElementById("peer-id");
const btnInitSession = document.getElementById("btn-init-session");
const statusEl = document.getElementById("status");
const chatLog = document.getElementById("chat-log");
const msgInput = document.getElementById("msg-input");
const btnSend = document.getElementById("btn-send");

let myId = null;
let pollTimer = null;

// ------------------------------
// Helpers
// ------------------------------
function apiUrl(path) {
  return API_BASE + path;
}

function setStatus(text) {
  statusEl.textContent = text || "";
}

function appendMsg(text, fromMe = false) {
  const div = document.createElement("div");
  div.className = "msg " + (fromMe ? "me" : "them");

  const meta = document.createElement("div");
  meta.className = "msg-meta";
  meta.textContent = fromMe ? "Ти" : "Співрозмовник";

  const body = document.createElement("div");
  body.textContent = text;

  div.appendChild(meta);
  div.appendChild(body);

  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function saveMyId(id) {
  myId = id;
  myIdInput.value = id;
  myIdBadge.textContent = "user_id: " + id;
  localStorage.setItem("signal_v6_user_id", id);
}

function loadMyId() {
  const stored = localStorage.getItem("signal_v6_user_id");
  if (stored) {
    saveMyId(stored);
  }
}

// ------------------------------
// Register
// ------------------------------
btnRegister.onclick = async () => {
  const username = usernameInput.value.trim();
  if (!username) {
    alert("Введи username");
    return;
  }

  try {
    setStatus("Реєстрація...");
    const res = await fetch(apiUrl("/register"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username })
    });

    const data = await res.json();
    if (data.user_id) {
      saveMyId(data.user_id);
      setStatus("Зареєстровано. Збережи свій user_id для корпоративного доступу.");
      btnInitSession.disabled = false;
      msgInput.disabled = false;
      btnSend.disabled = false;
      startPolling();
    } else {
      console.error(data);
      setStatus("Помилка реєстрації.");
    }
  } catch (err) {
    console.error(err);
    setStatus("Помилка мережі.");
  }
};

// ------------------------------
// Init session (X3DH)
// ------------------------------
btnInitSession.onclick = async () => {
  if (!myId) {
    alert("Спочатку зареєструйся.");
    return;
  }
  const peerId = peerIdInput.value.trim();
  if (!peerId) {
    alert("Введи ID співрозмовника.");
    return;
  }

  try {
    setStatus("Створення сесії (X3DH)...");
    const res = await fetch(apiUrl("/session/init"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender_id: myId,
        receiver_id: peerId
      })
    });

    const data = await res.json();
    if (data.status === "session_established") {
      setStatus("Сесія створена. Можна писати зашифровані повідомлення.");
      msgInput.disabled = false;
      btnSend.disabled = false;
    } else {
      console.error(data);
      setStatus("Не вдалося створити сесію.");
    }
  } catch (err) {
    console.error(err);
    setStatus("Помилка мережі при створенні сесії.");
  }
};

// ------------------------------
// Send message
// ------------------------------
btnSend.onclick = async () => {
  if (!myId) {
    alert("Спочатку зареєструйся.");
    return;
  }
  const peerId = peerIdInput.value.trim();
  if (!peerId) {
    alert("Введи ID співрозмовника.");
    return;
  }
  const text = msgInput.value.trim();
  if (!text) return;

  try {
    const res = await fetch(apiUrl("/message/send"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender_id: myId,
        receiver_id: peerId,
        text
      })
    });

    const data = await res.json();
    if (data.status === "sent") {
      appendMsg(text, true);
      msgInput.value = "";
    } else {
      console.error(data);
      setStatus("Не вдалося надіслати повідомлення.");
    }
  } catch (err) {
    console.error(err);
    setStatus("Помилка мережі при відправленні.");
  }
};

msgInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    btnSend.click();
  }
});

// ------------------------------
// Poll incoming messages
// ------------------------------
async function pollOnce() {
  if (!myId) return;

  try {
    const res = await fetch(apiUrl(`/message/poll/${myId}`));
    const data = await res.json();
    const messages = data.messages || [];
    messages.forEach((m) => {
      appendMsg(m.text, false);
    });
  } catch (err) {
    console.error("poll error:", err);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, 2000);
}

// ------------------------------
// Init on load
// ------------------------------
loadMyId();
if (myId) {
  btnInitSession.disabled = false;
  msgInput.disabled = false;
  btnSend.disabled = false;
  startPolling();
}