// =============================
//   Signal v6 — WebRTC клієнт (FIXED v6.2)
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

// STUN/TURN
const rtcConfig = {
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" }
  ]
};

// ------------------------------------
// CONNECT
// ------------------------------------
btnConnect.onclick = async () => {
  const myId = myIdInput.value.trim();
  if (!myId) return alert("Введи свій user_id");

  const WS_URL = "wss://corp-production-0ac7.up.railway.app/call";

  socket = new WebSocket(`${WS_URL}/${myId}`);

  socket.onopen = () => {
    console.log("🔌 WS connected");
    btnCall.disabled = false;
  };

  socket.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    console.log("📨 Signal:", msg);

    const { type, from, data } = msg;

    if (!pc) await createPeerConnection(from);

    if (type === "offer") {
      await pc.setRemoteDescription(new RTCSessionDescription(data));
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);

      sendSignal("answer", from, answer);

    } else if (type === "answer") {
      await pc.setRemoteDescription(new RTCSessionDescription(data));

    } else if (type === "ice") {
      if (data) {
        await pc.addIceCandidate(new RTCIceCandidate(data));
      }

    } else if (type === "hangup") {
      endCall();
    }
  };

  socket.onclose = () => {
    console.log("🔌 WS closed");
    endCall();
  };
};

// ------------------------------------
// CALL
// ------------------------------------
btnCall.onclick = async () => {
  const peerId = peerIdInput.value.trim();
  if (!peerId) return alert("Введи peer_id");

  await createPeerConnection(peerId);

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  sendSignal("offer", peerId, offer);
  btnHangup.disabled = false;
};

// ------------------------------------
// HANGUP
// ------------------------------------
btnHangup.onclick = () => {
  const peerId = peerIdInput.value.trim();
  sendSignal("hangup", peerId, {});
  endCall();
};

// ------------------------------------
// PEER CONNECTION
// ------------------------------------
async function createPeerConnection(peerId) {
  if (pc) return;

  pc = new RTCPeerConnection(rtcConfig);

  // Local media
  if (!localStream) {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true
      });
      localVideo.srcObject = localStream;
    } catch (e) {
      console.error("Media error", e);
      alert("Камера/мікрофон недоступні");
      return;
    }
  }

  localStream.getTracks().forEach(track =>
    pc.addTrack(track, localStream)
  );

  pc.onicecandidate = (ev) => {
    if (ev.candidate) {
      sendSignal("ice", peerId, ev.candidate);
    }
  };

  pc.ontrack = (ev) => {
    remoteVideo.srcObject = ev.streams[0];
  };

  pc.onconnectionstatechange = () => {
    if (["failed", "disconnected", "closed"].includes(pc.connectionState)) {
      endCall();
    }
  };

  btnHangup.disabled = false;
}

// ------------------------------------
// SEND SIGNAL
// ------------------------------------
function sendSignal(type, to, data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;

  const from = myIdInput.value.trim();

  socket.send(JSON.stringify({ type, from, to, data }));
}

// ------------------------------------
// END CALL
// ------------------------------------
function endCall() {
  if (pc) {
    pc.getSenders().forEach(s => s.track && s.track.stop());
    pc.close();
    pc = null;
  }
  remoteVideo.srcObject = null;
  btnHangup.disabled = true;
}