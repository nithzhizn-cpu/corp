// ==========================================
//   Signal v6.2 — WebRTC Voice/Video Client
// ==========================================

// ⚠️ ВСТАВ СВІЙ backend WebSocket домен
const WS_BACKEND = "wss://YOUR_BACKEND_DOMAIN/call";

let socket = null;
let pc = null;
let localStream = null;

const myIdInput = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");

const btnConnect = document.getElementById("btn-connect");
const btnCall = document.getElementById("btn-call");
const btnHangup = document.getElementById("btn-hangup");

const localVideo = document.getElementById("localVideo");
const remoteVideo = document.getElementById("remoteVideo");


// =============== STUN (TURN додаєш свій) ==================
const rtcConfig = {
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" }
  ]
};


// ===============================================================
//   CONNECT TO BACKEND WS
// ===============================================================
btnConnect.onclick = () => {
  const myId = myIdInput.value.trim();
  if (!myId) {
    alert("Введи свій user_id");
    return;
  }

  socket = new WebSocket(`${WS_BACKEND}/${myId}`);

  socket.onopen = () => {
    console.log("WS connected");
    btnCall.disabled = false;
  };

  socket.onerror = (e) => {
    console.error("WS error:", e);
  };

  socket.onclose = () => {
    console.log("WS closed");
    btnCall.disabled = true;
    btnHangup.disabled = true;
  };

  socket.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
    console.log("WS message:", msg);

    const { type, from, data } = msg;

    if (!pc) {
      await createPeer(from);
    }

    if (type === "offer") {
      await pc.setRemoteDescription(new RTCSessionDescription(data));

      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);

      sendSignal("answer", from, answer);
    }

    else if (type === "answer") {
      await pc.setRemoteDescription(new RTCSessionDescription(data));
    }

    else if (type === "ice") {
      if (data) {
        try {
          await pc.addIceCandidate(new RTCIceCandidate(data));
        } catch (err) {
          console.error("ICE error:", err);
        }
      }
    }

    else if (type === "hangup") {
      endCall();
    }
  };
};


// ===============================================================
//   CREATE PEER CONNECTION
// ===============================================================
async function createPeer(peerId) {
  if (pc) return;

  pc = new RTCPeerConnection(rtcConfig);

  // Камера + мікрофон
  if (!localStream) {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        video: true,
        audio: true
      });

      localVideo.srcObject = localStream;
    } catch (e) {
      console.error("Media error:", e);
      alert("Немає доступу до камери/мікрофона");
      return;
    }
  }

  localStream.getTracks().forEach(track => {
    pc.addTrack(track, localStream);
  });

  pc.ontrack = (event) => {
    console.log("Remote stream received");
    remoteVideo.srcObject = event.streams[0];
  };

  pc.onicecandidate = (event) => {
    if (event.candidate) {
      sendSignal("ice", peerId, event.candidate);
    }
  };

  pc.onconnectionstatechange = () => {
    console.log("PC state:", pc.connectionState);
    if (
      pc.connectionState === "failed" ||
      pc.connectionState === "disconnected" ||
      pc.connectionState === "closed"
    ) {
      endCall();
    }
  };
}


// ===============================================================
//   CALL BUTTON — SEND OFFER
// ===============================================================
btnCall.onclick = async () => {
  const peerId = peerIdInput.value.trim();
  if (!peerId) {
    alert("Введи peer_id");
    return;
  }

  await createPeer(peerId);

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  sendSignal("offer", peerId, offer);

  btnHangup.disabled = false;
};


// ===============================================================
//   HANGUP
// ===============================================================
btnHangup.onclick = () => {
  const peerId = peerIdInput.value.trim();

  if (peerId && socket && socket.readyState === WebSocket.OPEN) {
    sendSignal("hangup", peerId, {});
  }

  endCall();
};


// ===============================================================
//   SEND WS SIGNAL
// ===============================================================
function sendSignal(type, to, data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;

  const from = myIdInput.value.trim();

  socket.send(
    JSON.stringify({
      type,
      from,
      to,
      data
    })
  );
}


// ===============================================================
//   END CALL
// ===============================================================
function endCall() {
  if (pc) {
    pc.getSenders().forEach(s => {
      try { s.track && s.track.stop(); } catch {}
    });

    pc.ontrack = null;
    pc.onicecandidate = null;

    pc.close();
    pc = null;
  }

  remoteVideo.srcObject = null;
  btnHangup.disabled = true;

  console.log("Call ended");
}