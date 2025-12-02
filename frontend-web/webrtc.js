const btnConnect = document.getElementById("btn-connect");
const btnCall = document.getElementById("btn-call");
const btnHangup = document.getElementById("btn-hangup");
const myIdInput = document.getElementById("my-id");
const peerIdInput = document.getElementById("peer-id");
const localVideo = document.getElementById("localVideo");
const remoteVideo = document.getElementById("remoteVideo");

let socket = null;
let pc = null;
let localStream = null;

const rtcConfig = {
  iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
};

btnConnect.onclick = () => {
  const myId = myIdInput.value.trim();
  if (!myId) {
    alert("Введи свій user_id");
    return;
  }
  const loc = window.location;
  const wsBase = (loc.protocol === "https:" ? "wss://" : "ws://") + loc.host;
  const wsUrl = wsBase + "/call/" + encodeURIComponent(myId);
  socket = new WebSocket(wsUrl);

  socket.onopen = () => {
    console.log("WebSocket connected");
    btnCall.disabled = false;
  };

  socket.onmessage = async (event) => {
    const msg = JSON.parse(event.data);
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
        console.error("ICE add error", err);
      }
    } else if (type === "hangup") {
      endCall();
    }
  };

  socket.onclose = () => {
    console.log("WebSocket closed");
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

  if (!localStream) {
    try {
      localStream = await navigator.mediaDevices.getUserMedia({
        audio: true,
        video: true,
      });
      localVideo.srcObject = localStream;
    } catch (err) {
      console.error("getUserMedia error", err);
      alert("Не вдалося отримати камеру/мікрофон.");
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
    remoteVideo.srcObject = event.streams[0];
  };

  pc.onconnectionstatechange = () => {
    if (
      pc.connectionState === "failed" ||
      pc.connectionState === "disconnected" ||
      pc.connectionState === "closed"
    ) {
      endCall();
    }
  };
}

function sendSignal(type, to, data) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const myId = myIdInput.value.trim();
  const msg = { type, from: myId, to, data };
  socket.send(JSON.stringify(msg));
}

function endCall() {
  if (pc) {
    pc.getSenders().forEach((s) => s.track && s.track.stop());
    pc.close();
    pc = null;
  }
  remoteVideo.srcObject = null;
  btnHangup.disabled = true;
}
