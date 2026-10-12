from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from flask_sock import Sock
from simple_websocket import ConnectionClosed

import asyncio
import json
import queue
import threading
import time

import websocket

from aiortc import ( RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer
)

import os
import urllib.request

import cv2
import numpy as np


# =====================================================
# CONFIGURATION  (everything you may want to tune)
# =====================================================

# --- Server (plain HTTP; ngrok provides the HTTPS) ---
PORT = 5000

# --- ESP32 (PRESERVED) ---
ESP32_HOST = "esp32-robot.local"
ESP32_WS_URL = "ws://" + ESP32_HOST + ":81/"

# --- WebRTC ---
# Standard public STUN server. Set to None for LAN-only (no internet needed).
STUN_URL = "stun:stun.l.google.com:19302"

# --- Face detection (OpenCV YuNet; falls back to a Haar cascade if the model is unavailable) ---
# The YuNet model (~230 KB) is downloaded automatically on first start and saved next to server.py.
# If the download fails, download it by hand from the URL below and place it next to server.py.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FACE_MODEL_PATH = os.path.join(BASE_DIR, "face_detection_yunet_2023mar.onnx")
FACE_MODEL_URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
FACE_SCORE_THRESHOLD = 0.6  # higher = fewer false detections, lower = more sensitive
DETECT_MAX_WIDTH = 640      # frames wider than this are shrunk before detection (faster)
TARGET_MAX_JUMP = 0.25      # same face if it moved less than this fraction of the frame width between frames

# --- Face recognition (OpenCV SFace; YuNet supplies the face landmarks) ---
SFACE_MODEL_PATH = os.path.join(BASE_DIR, "face_recognition_sface_2021dec.onnx")
SFACE_MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/"
    "ba91a3b91d00d76e86540d4013f944bd6b514e39/"
    "models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
)
FACE_GALLERY_PATH = os.path.join(BASE_DIR, "face_gallery.json")
RECOGNITION_THRESHOLD = 0.363  # OpenCV's recommended cosine similarity threshold for SFace
ENROLL_HOLD_S = 2.0             # keep the motors stopped after a face is enrolled

# --- Steering direction ---
# WebRTC frames from Safari's front camera arrive UNMIRRORED, but the preview you watch is
# mirrored. True = the robot turns toward the person as SHOWN on the mirrored preview
# (you on the left of the screen -> robot turns left). Only the steering is flipped;
# "offset" in telemetry stays in raw camera coordinates.
FLIP_HORIZONTAL = True

# --- Steering ---
KP = 0.7
DEAD_ZONE = 0.08
MAX_STEERING = 1.0

# Turn-stop-turn-stop: while you are outside the dead zone the robot turns for TURN_PULSE_S,
# stops for TURN_PAUSE_S so the (delayed) camera image catches up, then re-checks.
# Longer pause / shorter pulse = less overshoot but slower centering.
TURN_PULSE_S = 0.15
TURN_PAUSE_S = 0.35

# --- Distance (face-box height / frame height: RELATIVE proximity only) ---
# Now measured on the FACE box (was the whole-body box), so the numbers are much smaller.
# Roughly: 0.12 = about 2 m away, 0.45 = about 0.6 m away. Tune to taste.
# Face height ratios: larger face = closer person. These values are relative to the frame.
TOO_FAR_HEIGHT_RATIO = 0.15     # approach sooner when the face is still relatively small
TOO_CLOSE_HEIGHT_RATIO = 0.35   # higher = allow the target to approach closer before stopping
FOLLOW_SPEED = 0.35
APPROACH_SPEED = 0.45
TOO_CLOSE_SPEED = 0.0       # 0 = stop. Negative (e.g. -0.2) = back away.

# --- Target loss ---
# Seconds the target may be missing before the ID is dropped and a new target may be picked.
# The robot stops immediately while it is missing.
TARGET_LOST_TIMEOUT = 0.75

# --- Control rate ---
CONTROL_HZ = 25


# =====================================================
# FLASK
# =====================================================

app = Flask(__name__)
CORS(app)
sock = Sock(app)

esp32_socket = None
esp32_lock = threading.Lock()

ALLOWED_COMMANDS = {"LEFT", "RIGHT", "FORWARD", "BACKWARD", "STOP"}


# =====================================================
# ESP32 CONNECTION (PRESERVED)
# =====================================================

def connect_to_esp32():

    global esp32_socket

    while True:

        try:

            print()
            print("[ESP32] Connecting to:", ESP32_WS_URL)

            ws = websocket.create_connection(ESP32_WS_URL, timeout=2)
            ws.settimeout(None)

            with esp32_lock:
                esp32_socket = ws

            print("[ESP32] WebSocket connected")

            try:
                ws.send("STOP")
            except Exception as error:
                print("[ESP32] Initial STOP failed:", error)

            while True:

                try:

                    message = ws.recv()

                    if message is None:
                        break

                    print("[ESP32 -> FLASK]", str(message).strip().upper())

                except websocket.WebSocketConnectionClosedException:
                    break

                except Exception as error:
                    print("[ESP32] Receive error:", error)
                    break

        except Exception as error:
            print("[ESP32] Connection failed:", error)

        finally:

            with esp32_lock:

                if esp32_socket is not None:

                    try:
                        esp32_socket.close()
                    except Exception:
                        pass

                    esp32_socket = None

            print("[ESP32] Disconnected")

        print("[ESP32] Reconnecting in 1 second...")

        time.sleep(1)


def send_to_esp32(command):

    command = str(command).upper().strip()

    if command not in ALLOWED_COMMANDS:
        print("[FLASK] Invalid command:", command)
        return False

    with esp32_lock:

        if esp32_socket is None:
            print("[FLASK] ESP32 is not connected")
            return False

        try:
            esp32_socket.send(command)
            print("[FLASK -> ESP32]", command)
            return True

        except Exception as error:
            print("[FLASK] Send error:", error)
            return False


def send_tracking_to_esp32(payload):

    message = json.dumps(payload, separators=(",", ":"))

    with esp32_lock:

        if esp32_socket is None:
            return False

        try:
            esp32_socket.send(message)
            return True

        except Exception as error:
            print("[FLASK] Tracking send error:", error)
            return False


# =====================================================
# BROWSER TELEMETRY BROADCAST
# =====================================================

browser_clients = set()
browser_clients_lock = threading.Lock()


def broadcast_telemetry(payload):

    message = json.dumps(payload, separators=(",", ":"))

    with browser_clients_lock:

        for client_queue in browser_clients:

            try:
                client_queue.put_nowait(message)

            except queue.Full:

                # Drop the OLDEST message so the browser always shows fresh data.
                try:
                    client_queue.get_nowait()
                    client_queue.put_nowait(message)
                except Exception:
                    pass


# =====================================================
# ROUTES (PRESERVED)
# =====================================================

@app.route("/")
def home():
    return send_from_directory(".", "face_follower.html")


@app.route("/esp32-status", methods=["GET"])
def esp32_status():

    with esp32_lock:
        connected = esp32_socket is not None

    return jsonify({
        "connected": connected,
        "esp32": ESP32_HOST,
        "websocket": ESP32_WS_URL
    })


@app.route("/command", methods=["POST"])
def command():

    data = request.get_json(silent=True)

    if not data or "cmd" not in data:
        return jsonify({"success": False, "error": "Missing command"}), 400

    cmd = str(data["cmd"]).upper().strip()

    if cmd not in ALLOWED_COMMANDS:
        return jsonify({"success": False, "error": "Invalid command"}), 400

    if not send_to_esp32(cmd):
        return jsonify({"success": False, "error": "ESP32 is not connected"}), 502

    return jsonify({"success": True, "command": cmd})


@app.route("/recognition/status", methods=["GET"])
def recognition_status():

    with face_gallery_lock:
        names = sorted(face_gallery)

    return jsonify({
        "available": face_recognizer is not None,
        "names": names,
        "threshold": RECOGNITION_THRESHOLD
    })


@app.route("/recognition/enroll", methods=["POST"])
def recognition_enroll():

    global control_hold_until

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()

    if not name or len(name) > 64:
        return jsonify({"success": False, "error": "Enter a name up to 64 characters"}), 400

    with face_gallery_lock:
        if face_gallery:
            return jsonify({
                "success": False,
                "error": "A face is already enrolled. Forget it before enrolling another face."
            }), 409

    with latest_recognition_lock:
        sample = latest_recognition

    if face_recognizer is None:
        return jsonify({"success": False, "error": "SFace is unavailable"}), 503
    if sample is None or sample.get("embedding") is None:
        return jsonify({"success": False, "error": "No usable face is currently visible"}), 409

    with face_gallery_lock:
        face_gallery[name] = sample["embedding"]
        save_face_gallery()

    control_hold_until = time.time() + ENROLL_HOLD_S

    return jsonify({"success": True, "name": name})


@app.route("/recognition/forget", methods=["POST"])
def recognition_forget():

    data = request.get_json(silent=True) or {}
    name = str(data.get("name", "")).strip()

    with face_gallery_lock:
        if name not in face_gallery:
            return jsonify({"success": False, "error": "Name is not enrolled"}), 404
        del face_gallery[name]
        save_face_gallery()

    return jsonify({"success": True, "name": name})


@sock.route("/ws")
def browser_websocket(ws):

    print()
    print("[BROWSER] WebSocket connected")

    client_queue = queue.Queue(maxsize=20)

    with browser_clients_lock:
        browser_clients.add(client_queue)

    try:

        while True:

            # Manual legacy command (bench testing) - short wait so telemetry flushes quickly.
            try:
                message = ws.receive(timeout=0.02)

            except ConnectionClosed:
                break

            except Exception:
                message = None

            if message is not None:

                command = str(message).strip().upper()

                print("[BROWSER -> FLASK]", command)

                if command in ALLOWED_COMMANDS:
                    ws.send(command if send_to_esp32(command) else "ERROR")
                else:
                    ws.send("ERROR")

            try:
                while True:
                    ws.send(client_queue.get_nowait())

            except queue.Empty:
                pass

    except Exception as error:
        print("[BROWSER] WebSocket error:", error)

    finally:

        with browser_clients_lock:
            browser_clients.discard(client_queue)

        print("[BROWSER] WebSocket disconnected")


# =====================================================
# WEBRTC (iPhone camera -> PC)
#
# Only the small SDP handshake goes through HTTP/ngrok
# (POST /offer). The video itself flows peer-to-peer.
# =====================================================

webrtc_loop = None
active_peer_connections = set()

frame_lock = threading.Lock()
frame_event = threading.Event()     # wakes the tracker when a NEW frame arrives
latest_frame = None                 # newest BGR frame only - never a queue
latest_frame_id = 0

RTC_CONFIG = RTCConfiguration(
    iceServers=[RTCIceServer(urls=STUN_URL)] if STUN_URL else []
)


def start_webrtc_loop():

    global webrtc_loop

    webrtc_loop = asyncio.new_event_loop()

    def runner():
        asyncio.set_event_loop(webrtc_loop)
        webrtc_loop.run_forever()

    threading.Thread(target=runner, daemon=True).start()


async def consume_video(track):

    global latest_frame, latest_frame_id

    while True:

        try:
            frame = await track.recv()
        except Exception as error:
            print("[WEBRTC] Track ended:", error)
            break

        # Keep the raw decoded frame; the tracker thread converts only the frames it actually
        # uses, so this network thread is never slowed down by unused conversions.
        with frame_lock:
            latest_frame = frame         # overwrite: stale frames are simply discarded
            latest_frame_id += 1

        frame_event.set()


async def handle_offer(offer_sdp, offer_type):

    # Only one camera at a time: close older connections (e.g. after a page reload).
    for old in list(active_peer_connections):
        active_peer_connections.discard(old)
        await old.close()

    pc = RTCPeerConnection(RTC_CONFIG)
    active_peer_connections.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():

        print("[WEBRTC] Connection state:", pc.connectionState)

        if pc.connectionState in ("failed", "closed", "disconnected"):
            active_peer_connections.discard(pc)
            await pc.close()

    @pc.on("track")
    def on_track(track):

        print("[WEBRTC] Track received:", track.kind)

        if track.kind == "video":
            asyncio.ensure_future(consume_video(track), loop=webrtc_loop)

    await pc.setRemoteDescription(
        RTCSessionDescription(sdp=offer_sdp, type=offer_type)
    )

    await pc.setLocalDescription(await pc.createAnswer())

    # Wait (max 5 s) for ICE gathering so the answer contains all candidates.
    waited = 0.0

    while pc.iceGatheringState != "complete" and waited < 5.0:
        await asyncio.sleep(0.05)
        waited += 0.05

    return pc.localDescription.sdp, pc.localDescription.type


@app.route("/offer", methods=["POST"])
def offer():

    data = request.get_json(force=True)

    future = asyncio.run_coroutine_threadsafe(
        handle_offer(data["sdp"], data["type"]),
        webrtc_loop
    )

    try:
        sdp, type_ = future.result(timeout=15)

    except Exception as error:
        print("[WEBRTC] Offer handling failed:", error)
        return jsonify({"error": str(error)}), 500

    return jsonify({"sdp": sdp, "type": type_})


# =====================================================
# FACE DETECTION + TRACKING + STEERING
# =====================================================

face_detector = None        # cv2.FaceDetectorYN (YuNet) when available
haar_cascade = None         # fallback
face_recognizer = None      # cv2.FaceRecognizerSF (SFace) when available
face_gallery = {}
face_gallery_lock = threading.Lock()
latest_recognition = None
latest_recognition_lock = threading.Lock()
control_hold_until = 0.0

target_track_id = None
target_center = None        # (x, y) of the locked face in the last frame
next_track_id = 1
lost_since = None
turn_phase = None            # None (centered) / "TURN" / "PAUSE"
turn_phase_start = 0.0


def _model_file_ok():
    return os.path.exists(FACE_MODEL_PATH) and os.path.getsize(FACE_MODEL_PATH) > 100000


def _sface_model_file_ok():
    return os.path.exists(SFACE_MODEL_PATH) and os.path.getsize(SFACE_MODEL_PATH) > 100000


def load_face_gallery():

    global face_gallery

    try:
        with open(FACE_GALLERY_PATH, "r", encoding="utf-8") as gallery_file:
            loaded = json.load(gallery_file)
        if isinstance(loaded, dict):
            face_gallery = {
                str(name): [float(value) for value in embedding]
                for name, embedding in loaded.items()
                if isinstance(embedding, list) and embedding
            }
    except (OSError, ValueError, TypeError):
        face_gallery = {}


def save_face_gallery():

    temporary_path = FACE_GALLERY_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as gallery_file:
        json.dump(face_gallery, gallery_file, indent=2)
    os.replace(temporary_path, FACE_GALLERY_PATH)


def load_face_recognizer():

    global face_recognizer

    if not _sface_model_file_ok():
        try:
            print("[FACE] Downloading SFace recognition model...")
            urllib.request.urlretrieve(SFACE_MODEL_URL, SFACE_MODEL_PATH)
        except Exception as error:
            print("[FACE] SFace download failed:", error)

    if not _sface_model_file_ok():
        if os.path.exists(SFACE_MODEL_PATH):
            os.remove(SFACE_MODEL_PATH)
        print("[FACE] SFace unavailable; recognition is disabled")
        return

    try:
        face_recognizer = cv2.FaceRecognizerSF.create(SFACE_MODEL_PATH, "")
        print("[FACE] Using SFace recognition")
    except Exception as error:
        print("[FACE] Could not load SFace:", error)


load_face_gallery()


def load_face_detector():

    global face_detector, haar_cascade

    if not _model_file_ok():

        try:
            print("[FACE] Downloading YuNet face model...")
            urllib.request.urlretrieve(FACE_MODEL_URL, FACE_MODEL_PATH)

        except Exception as error:
            print("[FACE] Download failed:", error)

        if not _model_file_ok() and os.path.exists(FACE_MODEL_PATH):
            os.remove(FACE_MODEL_PATH)      # junk / partial file

    if _model_file_ok():

        try:
            face_detector = cv2.FaceDetectorYN.create(
                FACE_MODEL_PATH, "", (320, 320), FACE_SCORE_THRESHOLD, 0.3, 5000
            )
            print("[FACE] Using YuNet detector")
            return

        except Exception as error:
            print("[FACE] Could not load YuNet:", error)

    haar_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    print("[FACE] Using the Haar cascade fallback (less accurate). "
          "Put the YuNet model next to server.py for better results.")


load_face_detector()
load_face_recognizer()


def recognize_face(frame, face):

    if face_recognizer is None or "raw" not in face:
        return None, None

    try:
        aligned = face_recognizer.alignCrop(
            frame, np.asarray(face["raw"], dtype=np.float32)
        )
        feature = face_recognizer.feature(aligned)
        embedding = feature.reshape(-1).astype(np.float32)
    except Exception:
        return None, None

    best_name = None
    best_score = -1.0

    with face_gallery_lock:
        for name, stored in face_gallery.items():
            score = float(face_recognizer.match(
                feature,
                np.asarray(stored, dtype=np.float32).reshape(1, -1),
                cv2.FaceRecognizerSF_FR_COSINE
            ))
            if score > best_score:
                best_name = name
                best_score = score

    if best_score < RECOGNITION_THRESHOLD:
        best_name = "Unknown"

    return best_name, embedding.tolist()


def detect_faces(frame):

    """Returns (faces, width, height) in the coordinates of the (possibly shrunk) frame."""

    height, width = frame.shape[:2]

    if width > DETECT_MAX_WIDTH:
        scale = DETECT_MAX_WIDTH / width
        frame = cv2.resize(frame, (DETECT_MAX_WIDTH, int(height * scale)))
        height, width = frame.shape[:2]

    faces = []

    if face_detector is not None:

        face_detector.setInputSize((width, height))

        _, found = face_detector.detect(frame)

        if found is not None:
            for f in found:
                x, y, w, h = [float(v) for v in f[:4]]
                faces.append({
                    "x1": x, "y1": y, "x2": x + w, "y2": y + h,
                    "raw": [float(value) for value in f[:15]]
                })

    else:

        gray = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY))

        found = haar_cascade.detectMultiScale(
            gray, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24)
        )

        for (x, y, w, h) in found:
            faces.append({"x1": float(x), "y1": float(y), "x2": float(x + w), "y2": float(y + h)})

    # Keep boxes inside the frame.
    for face in faces:
        face["x1"] = max(0.0, face["x1"])
        face["y1"] = max(0.0, face["y1"])
        face["x2"] = min(float(width), face["x2"])
        face["y2"] = min(float(height), face["y2"])

    return faces, width, height


def _center(box):
    return ((box["x1"] + box["x2"]) / 2, (box["y1"] + box["y2"]) / 2)


def select_target(faces, frame_width):

    global target_track_id, target_center, next_track_id

    enrolled_faces = [face for face in faces if face.get("identity") not in (None, "Unknown")]

    if not enrolled_faces:
        return None

    if target_track_id is not None:

        # Same person = the face nearest to where the locked face was last seen.
        def distance(face):
            cx, cy = _center(face)
            return ((cx - target_center[0]) ** 2 + (cy - target_center[1]) ** 2) ** 0.5

        best = min(enrolled_faces, key=distance)

        if distance(best) > TARGET_MAX_JUMP * frame_width:
            return None

    else:

        # No target yet: the only face, or the one closest to the frame center.
        best = min(enrolled_faces, key=lambda f: abs(_center(f)[0] - frame_width / 2))

        target_track_id = next_track_id
        next_track_id += 1

    target_center = _center(best)

    best = dict(best)
    best["id"] = target_track_id

    return best


def tracking_loop():

    global target_track_id, target_center, lost_since, turn_phase, turn_phase_start
    global latest_recognition

    last_frame_id_seen = -1
    last_control_time = 0.0
    control_interval = 1.0 / CONTROL_HZ

    infer_count = 0
    fps_timer = time.time()
    current_fps = 0.0
    last_infer_ms = 0.0

    while True:

        # Wake when a new frame arrives; take only the NEWEST one.
        frame_event.wait(timeout=0.2)
        frame_event.clear()

        with frame_lock:
            av_frame = latest_frame
            frame_id = latest_frame_id

        if av_frame is None or frame_id == last_frame_id_seen:
            continue

        last_frame_id_seen = frame_id

        frame = av_frame.to_ndarray(format="bgr24")

        infer_start = time.time()

        faces, frame_width, frame_height = detect_faces(frame)

        last_infer_ms = (time.time() - infer_start) * 1000.0

        infer_count += 1

        now = time.time()

        if now - fps_timer >= 1.0:
            current_fps = infer_count / (now - fps_timer)
            infer_count = 0
            fps_timer = now
            print("[FACE] %.1f fps, %.0f ms per frame" % (current_fps, last_infer_ms))

        # Recognize every detected face before choosing a tracking target. Unknown faces
        # stay available for display and enrollment, but can never drive the robot.
        for face in faces:
            face["identity"], face["embedding"] = recognize_face(frame, face)

        enrollment_face = min(
            faces,
            key=lambda face: abs(_center(face)[0] - frame_width / 2),
            default=None
        )
        with latest_recognition_lock:
            latest_recognition = {
                "frame_id": frame_id,
                "embedding": enrollment_face.get("embedding") if enrollment_face else None
            }

        target_box = select_target(faces, frame_width)
        recognition_name = None

        if target_box is not None:
            recognition_name = target_box.get("identity")
            with latest_recognition_lock:
                latest_recognition = {
                    "frame_id": frame_id,
                    "embedding": target_box.get("embedding")
                }

        # Every other visible face: drawn with a red box on the page (never followed).
        others_norm = [
            [
                round(f["x1"] / frame_width, 4),
                round(f["y1"] / frame_height, 4),
                round(f["x2"] / frame_width, 4),
                round(f["y2"] / frame_height, 4)
            ]
            for f in faces
            if target_box is None
            or (f["x1"], f["y1"]) != (target_box["x1"], target_box["y1"])
        ]

        box_norm = None
        offset = None
        steering = 0.0
        speed = 0.0
        state = "TARGET_LOST"

        if target_box is None:

            # Robot stops right away; the ID is kept for TARGET_LOST_TIMEOUT so a brief
            # occlusion does not make the robot switch to somebody else.
            if lost_since is None:
                lost_since = now

            if now - lost_since > TARGET_LOST_TIMEOUT:
                target_track_id = None
                target_center = None

            turn_phase = None

        else:

            lost_since = None

            center_x = (target_box["x1"] + target_box["x2"]) / 2
            person_height = target_box["y2"] - target_box["y1"]
            frame_center_x = frame_width / 2

            # -1 = left edge of the frame, 0 = center, +1 = right edge
            raw_offset = (center_x - frame_center_x) / frame_center_x

            if abs(raw_offset) < DEAD_ZONE:

                # Centered: no turning at all.
                turn_phase = None
                steering_out = 0.0

            else:

                # Turn pulse -> stop pause -> turn pulse ... until centered.
                if turn_phase is None:
                    turn_phase = "TURN"
                    turn_phase_start = now

                elif turn_phase == "TURN" and now - turn_phase_start >= TURN_PULSE_S:
                    turn_phase = "PAUSE"
                    turn_phase_start = now

                elif turn_phase == "PAUSE" and now - turn_phase_start >= TURN_PAUSE_S:
                    turn_phase = "TURN"
                    turn_phase_start = now

                if turn_phase == "TURN":
                    turn_dir = -raw_offset if FLIP_HORIZONTAL else raw_offset
                    steering_out = max(-MAX_STEERING, min(MAX_STEERING, KP * turn_dir))
                else:
                    steering_out = 0.0

            height_ratio = person_height / frame_height

            if height_ratio < TOO_FAR_HEIGHT_RATIO:
                state = "TOO_FAR"
                speed = APPROACH_SPEED

            elif height_ratio > TOO_CLOSE_HEIGHT_RATIO:
                state = "TOO_CLOSE"
                speed = TOO_CLOSE_SPEED

            else:
                state = "FOLLOW"
                speed = FOLLOW_SPEED

            offset = raw_offset
            steering = steering_out

            # Box is always in the raw (unflipped) image coordinates for the overlay.
            box_norm = [
                round(target_box["x1"] / frame_width, 4),
                round(target_box["y1"] / frame_height, 4),
                round(target_box["x2"] / frame_width, 4),
                round(target_box["y2"] / frame_height, 4)
            ]

        if now < control_hold_until:
            state = "ENROLL_HOLD"
            steering = 0.0
            speed = 0.0

        # Rate-limited control output (~CONTROL_HZ), never one message per queued frame.
        if now - last_control_time >= control_interval:

            last_control_time = now

            payload = {
                "type": "tracking",
                "target": target_box is not None,
                "tracking_id": target_box["id"] if target_box is not None else None,
                # Same direction convention as "steering", in case the ESP32 uses the offset.
                "offset": (
                    round(-offset if FLIP_HORIZONTAL else offset, 3)
                    if offset is not None else None
                ),
                "steering": round(steering, 3),
                "speed": round(speed, 3),
                "state": state
            }

            payload["identity"] = recognition_name

            send_tracking_to_esp32(payload)

            telemetry = dict(payload)
            telemetry["fps"] = round(current_fps, 1)
            telemetry["box"] = box_norm
            telemetry["others"] = others_norm
            telemetry["identity"] = recognition_name
            # The browser page wants the raw camera-frame offset (it does its own mirroring).
            telemetry["offset"] = round(offset, 3) if offset is not None else None

            broadcast_telemetry(telemetry)


# =====================================================
# START
# =====================================================

if __name__ == "__main__":

    print()
    print("=" * 60)
    print("PERSON FOLLOWING ROBOT")
    print("=" * 60)
    print("ESP32 WebSocket :", ESP32_WS_URL)
    print("Face detector   :", "YuNet" if face_detector is not None else "Haar cascade (fallback)")
    print("Local server    : http://127.0.0.1:%d" % PORT)
    print("Next step       : run  ngrok http %d  and open the https URL on the iPhone" % PORT)
    print("=" * 60)
    print()

    threading.Thread(target=connect_to_esp32, daemon=True).start()

    start_webrtc_loop()

    threading.Thread(target=tracking_loop, daemon=True).start()

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)