// =============================
//   Signal v6 — WebRTC клієнт
// =============================

let socket = null;
let pc = null;
let localStream = null;

const btnConnect = document.getElementById("btn-connect");
const btnCall = document.getElementById("btn-call");
const btnHangup = document.getElementById("btn-hangup");
const myIdInput = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");
const localVideo = document.getElementById("localVideo");
const remoteVideo = document.getElementById("remoteVideo");

// ⚙️ Налаштування STUN/TURN
// Для продакшну — сюди додаєш свій TURN-сервер (coturn / paid)
const rtcConfig = {
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" }
    // { urls: "turn:YOUR_TURN_SERVER:3478", username: "user", credential: "pass" }
  ]
};

btnConnect.onclick = async () => {
  const myId = myIdInput.value.trim();
  if (!myId) {
    alert("Введи свій user_id (з /register).");
    return;
  }

  // 1. WebSocket на бек
  const wsUrl = `${location.origin.replace(/^http/, "ws")}/call/${myId}`;
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log("🔌 WebSocket connected");
    btnCall.disabled = false;
  };

  socket.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    console.log("📨 Signal:", msg);

    const type = msg.type;
    const from = msg.from;
    const data = msg.data;

    if (!pc) {
      await createPeerConnection(from);
    }

    if (type === "offer") {
      await pc.setRemoteDescription(new RTCSessionDescription(data));
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);

      sendSignal("answer", from, answer);
    } else if (type === "answer") {
      await pc.setRemoteDescription(new RTCSessionDescription(data));
    } else if (type === "ice") {
      try {
        await pc.addIceCandidate(new RTCIceCandidate(data));
      } catch (err) {
        console.error("Error adding ICE:", err);
      }
    } else if (type === "hangup") {
      endCall();
    }
  };

  socket.onclose = () => {
    console.log("🔌 WebSocket closed");
    btnCall.disabled = true;
    btnHangup.disabled = true;
  };
};

btnCall.onclick = async () => {
  const peerId = peerIdInput.value.trim();
  if (!peerId) {
    alert("Введи peer_id співрозмовника.");
    return;
  }

  await createPeerConnection(peerId);

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  sendSignal("offer", peerId, offer);

  btnHangup.disabled = false;
};

btnHangup.onclick = () => {
  const peerId = peerIdInput.value.trim();
  if (peerId && socket && socket.readyState === WebSocket.OPEN) {
    sendSignal("hangup", peerId, {});
  }
  endCall();
};

async function createPeerConnection(peerId) {
  if (pc) return;

  pc = new RTCPeerConnection(rtcConfig);

  // Локальний медіа-потік
  if (!localStream) {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: true
      });
      localVideo.srcObject = localStream;
    } catch (err) {
      console.error("getUserMedia error:", err);
      alert("Не вдалося отримати доступ до камери/мікрофона");
      return;
    }
  }

  localStream.getTracks().forEach((track) => {
    pc.addTrack(track, localStream);
  });

  pc.onicecandidate = (event) => {
    if (event.candidate) {
      sendSignal("ice", peerId, event.candidate);
    }
  };

  pc.ontrack = (event) => {
    console.log("📺 Remote track");
    remoteVideo.srcObject = event.streams[0];
  };

  pc.onconnectionstatechange = () => {
    console.log("PC state:", pc.connectionState);
    if (pc.connectionState === "failed" || pc.connectionState === "disconnected") {
      endCall();
    }
  };

  btnHangup.disabled = false;
}

function sendSignal(type, to, data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;

  const myId = myIdInput.value.trim();

  const msg = {
    type,
    from: myId,
    to,
    data
  };

  socket.send(JSON.stringify(msg));
}

function endCall() {
  if (pc) {
    pc.ontrack = null;
    pc.onicecandidate = null;
    pc.onconnectionstatechange = null;
    pc.getSenders().forEach((s) => s.track && s.track.stop());
    pc.close();
    pc = null;
  }
  remoteVideo.srcObject = null;
  btnHangup.disabled = true;
}