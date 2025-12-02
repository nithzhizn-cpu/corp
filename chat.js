// Спрощена версія chat.js (логіка повна, стиль мінімальний)
const API_BASE = "";

const usernameInput = document.getElementById("username");
const btnRegister = document.getElementById("btn-register");
const myIdInput = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");
const btnInitSession = document.getElementById("btn-init-session");
const statusEl = document.getElementById("status");
const chatLog = document.getElementById("chat-log");
const msgInput = document.getElementById("msg-input");
const btnSend = document.getElementById("btn-send");

let myId = null;
let pollTimer = null;

function apiUrl(path) {
  return API_BASE + path;
}

function setStatus(text) {
  statusEl.textContent = text || "";
}

function appendMsg(text, fromMe = false) {
  const div = document.createElement("div");
  div.textContent = (fromMe ? "Я: " : "Він/вона: ") + text;
  chatLog.appendChild(div);
  chatLog.scrollTop = chatLog.scrollHeight;
}

function saveMyId(id) {
  myId = id;
  myIdInput.value = id;
  localStorage.setItem("signal_v6_user_id", id);
}

function loadMyId() {
  const stored = localStorage.getItem("signal_v6_user_id");
  if (stored) {
    saveMyId(stored);
  }
}

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
      body: JSON.stringify({ username }),
    });
    const data = await res.json();
    if (data.user_id) {
      saveMyId(data.user_id);
      setStatus("Зареєстровано, user_id збережено.");
      startPolling();
    } else {
      console.error(data);
      setStatus("Помилка реєстрації.");
    }
  } catch (e) {
    console.error(e);
    setStatus("Помилка мережі.");
  }
};

btnInitSession.onclick = async () => {
  if (!myId) {
    alert("Спочатку зареєструйся.");
    return;
  }
  const peerId = peerIdInput.value.trim();
  if (!peerId) {
    alert("Введи user_id співрозмовника.");
    return;
  }
  try {
    setStatus("Створення сесії...");
    const res = await fetch(apiUrl("/session/init"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sender_id: myId, receiver_id: peerId }),
    });
    const data = await res.json();
    if (data.status === "session_established") {
      setStatus("Сесія створена. Можна писати.");
    } else {
      console.error(data);
      setStatus("Не вдалось створити сесію.");
    }
  } catch (e) {
    console.error(e);
    setStatus("Помилка мережі при створенні сесії.");
  }
};

btnSend.onclick = async () => {
  if (!myId) {
    alert("Спочатку зареєструйся.");
    return;
  }
  const peerId = peerIdInput.value.trim();
  if (!peerId) {
    alert("Введи user_id співрозмовника.");
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
        text,
      }),
    });
    const data = await res.json();
    if (data.status === "sent") {
      appendMsg(text, true);
      msgInput.value = "";
    } else {
      console.error(data);
      setStatus("Не вдалось надіслати.");
    }
  } catch (e) {
    console.error(e);
    setStatus("Помилка мережі при відправленні.");
  }
};

msgInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    btnSend.click();
  }
});

async function pollOnce() {
  if (!myId) return;
  try {
    const res = await fetch(apiUrl(`/message/poll/${myId}`));
    const data = await res.json();
    const messages = data.messages || [];
    messages.forEach((m) => {
      appendMsg(m.text, false);
    });
  } catch (e) {
    console.error("poll error", e);
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(pollOnce, 2000);
}

loadMyId();
if (myId) {
  startPolling();
}
