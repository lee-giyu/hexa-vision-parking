"""Unified serial reader for the Hexa-Vision parking sensor bus.

A single process that OWNS /dev/ttyUSB0 exclusively and dispatches every line of
the Arduino Mega stream by prefix. This replaces the two separate readers
(serial_bridge.py + capture_trigger.py), which could not safely share one serial
port — the kernel delivers each incoming byte to only one reader, so two
concurrent readers each got a torn, partial stream. One reader, one owner.

    SPOT:<spot_id>:1  -> POST /sensors/ping {device_id_hw, status: 1}
    SPOT:<spot_id>:0  -> POST /sensors/ping {device_id_hw, status: 0}
                         AND POST /sensors/exit {lot_id, spot_id}  (departure)
    HB:<millis>       -> heartbeat, logged only
    ENTRY:1           -> capture one JPEG from the Pi AI Camera; save to ./captures/,
                         POST it to the vision PC (VISION_URL, set in .env),
                         then forward the recognized plate to POST /gate/entrance
    ENTRY:0           -> object left the entry lane: reset, ready for next vehicle

The Pi runs no inference itself: it detects -> captures -> saves -> pushes the
JPEG to the vision teammate's PC (reachable over its own Cloudflare tunnel,
configured via VISION_URL), which runs YOLO + OCR off-Pi and returns the recognized plate
in its JSON response. The Pi then forwards that plate to the local backend's
POST /gate/entrance so the DB + frontend update. The local ./captures/ copy is
kept as an on-Pi record for debugging.

Run inside the capture venv (.venv_capture, Python 3.13), which is built with
--system-site-packages so it can import the apt-installed python3-picamera2 /
python3-libcamera (those cannot be pip-installed) while still seeing requests /
pyserial / python-dotenv:
    source .venv_capture/bin/activate && python unified_serial_reader.py

Auto-reconnects on serial drop.
"""

import os
import sys
import time
import re
from datetime import datetime, timezone, timedelta

import requests
import serial
from dotenv import load_dotenv

# picamera2 is provided by the apt package python3-picamera2 and reaches this
# venv via --system-site-packages. Fail with a clear message if it is missing.
try:
    from picamera2 import Picamera2
except ImportError as exc:  # pragma: no cover - environment guard
    print(
        "[FATAL] Could not import picamera2. Run this in .venv_capture (created "
        "with --system-site-packages so it can see the apt python3-picamera2). "
        f"Original error: {exc}"
    )
    sys.exit(1)

load_dotenv()

# ==========================================
# Configuration
# ==========================================
PORT = "/dev/ttyUSB0"
BAUD = 115200
BASE_URL = "http://localhost:8000"

# API key loaded from .env (HEXAVISION_API_KEY), matching app/core/security.py.
API_KEY = os.getenv("HEXAVISION_API_KEY")
if not API_KEY:
    raise RuntimeError(
        "Missing required environment variable: HEXAVISION_API_KEY (set it in .env)."
    )
LOT_ID = 1

# Arduino spot_id (1..6) emitted by the sketch -> logical floor-plan number.
# The sketch assigns its six working sensors 1..6 sequentially, but the physical
# layout differs from the floor-plan numbering, so we remap here. Floor-plan
# spots 4 and 6 have no working sensor.
ARDUINO_TO_REAL_SPOT = {
    1: 3,  # sketch 1 (sensor S2) -> floor-plan 3
    2: 2,  # sketch 2 (sensor S3) -> floor-plan 2
    3: 1,  # sketch 3 (sensor S4) -> floor-plan 1
    4: 8,  # sketch 4 (sensor S5) -> floor-plan 8
    5: 7,  # sketch 5 (sensor S6) -> floor-plan 7
    6: 5   # sketch 6 (sensor S8) -> floor-plan 5
}

# Floor-plan number -> device_id_hw sent to the API. The backend parses the
# trailing two digits as spot_number, so they must match the floor-plan number
# (e.g. 5 -> "HC-SR04-05").
SPOT_TO_DEVICE = {
    1: "HC-SR04-01",
    2: "HC-SR04-02",
    3: "HC-SR04-03",
    5: "HC-SR04-05",
    7: "HC-SR04-07",
    8: "HC-SR04-08",
}

RECONNECT_BACKOFF_SEC = 2.0
HTTP_TIMEOUT = 5.0
_HEADERS = {"X-API-Key": API_KEY}

# Where captured entrance frames land (local on-Pi record for debugging).
CAPTURE_DIR = os.getenv("CAPTURE_DIR", "./captures")
# Remote vision PC endpoint. The vision teammate runs YOLO + OCR behind their own
# Cloudflare tunnel; we PUSH each entrance JPEG there because that PC sits on a
# mobile hotspot and cannot reach the Pi to pull. The real tunnel host is kept out
# of version control — set VISION_URL in .env (defaults to localhost for testing).
VISION_URL = os.getenv("VISION_URL", "http://localhost:5000/detect")
CAPTURE_POST_TIMEOUT = 10.0  # seconds; vision PC is on a mobile hotspot
# Ignore repeated ENTRY:1 triggers within this many seconds (debounce against a
# car idling in range / sensor flapping). Configurable via CAPTURE_COOLDOWN.
# Measured from the END of a burst (see capture_burst), not its start.
COOLDOWN_SEC = float(os.getenv("CAPTURE_COOLDOWN", "10"))
# On each ENTRY:1 trigger, capture a high-frequency burst for this many seconds
# rather than a single still: one shot often fires too early (hands / car roof)
# or is motion-blurred, so we give the vision PC a stream of sequential frames to
# find one clear, in-focus plate. Configurable via CAPTURE_BURST_SEC.
BURST_DURATION_SEC = float(os.getenv("CAPTURE_BURST_SEC", "10"))
# Camera ownership is split out when running in the hybrid deployment: this
# reader handles serial occupancy (SPOT:/exit/HB:) only, while pi_client.py owns
# the CSI camera as a separate service (hexavision-capture). Set
# READER_ENABLE_CAMERA=0 to run occupancy-only: the camera is never opened (so it
# does not fight pi_client for the sensor) and ENTRY: lines are logged but never
# captured. Default "1" keeps the original all-in-one behaviour.
ENABLE_CAMERA = os.getenv("READER_ENABLE_CAMERA", "1").strip().lower() not in (
    "0", "false", "no", "off"
)
# Korea Standard Time (UTC+9) — capture filenames/logs use local lot time.
KST = timezone(timedelta(hours=9))

SPOT_RE = re.compile(r"^SPOT:(\d+):([01])$")
# ENTRY:<0|1> with an optional trailing distance (ENTRY:1:7) for forward-compat.
# The current production sketch emits only "ENTRY:1" / "ENTRY:0" (no distance).
ENTRY_RE = re.compile(r"^ENTRY:([01])(?::(\d+))?$")


# ==========================================
# Occupancy bridge (SPOT: / HB:) — ported from serial_bridge.py
# ==========================================
def post_ping(spot_id: int, status: int) -> None:
    device = SPOT_TO_DEVICE.get(spot_id)
    if not device:
        print(f"[WARN] no device mapping for spot_id={spot_id}; skipping ping")
        return
    payload = {"device_id_hw": device, "status": status}
    try:
        r = requests.post(
            f"{BASE_URL}/sensors/ping", json=payload, headers=_HEADERS, timeout=HTTP_TIMEOUT
        )
        print(f"[PING] spot {spot_id} ({device}) -> {status}: {r.status_code} {r.text[:120]}")
    except requests.exceptions.RequestException as e:
        print(f"[ERR] ping failed for spot {spot_id}: {e}")


def post_exit(spot_id: int) -> None:
    payload = {"lot_id": LOT_ID, "spot_id": spot_id}
    try:
        r = requests.post(
            f"{BASE_URL}/sensors/exit", json=payload, headers=_HEADERS, timeout=HTTP_TIMEOUT
        )
        print(f"[EXIT] spot {spot_id}: {r.status_code} {r.text[:160]}")
    except requests.exceptions.RequestException as e:
        print(f"[ERR] exit failed for spot {spot_id}: {e}")


def handle_spot(line: str) -> None:
    m = SPOT_RE.match(line)
    if not m:
        print(f"[SKIP] {line!r}")
        return

    arduino_id, status = int(m.group(1)), int(m.group(2))

    # Ignore Arduino spot_ids not in the map (e.g. unmapped sensors); otherwise
    # translate to the floor-plan number.
    if arduino_id not in ARDUINO_TO_REAL_SPOT:
        print(f"[WARN] Unknown arduino_id={arduino_id}; ignoring.")
        return

    real_spot_id = ARDUINO_TO_REAL_SPOT[arduino_id]

    post_ping(real_spot_id, status)
    if status == 0:
        # Occupied -> vacant transition means the car left: close the session.
        post_exit(real_spot_id)


# ==========================================
# Entrance capture (ENTRY:) — ported from capture_trigger.py
# ==========================================
def init_camera() -> Picamera2:
    """Initialise and start the Pi AI Camera, matching pi_client.py's capture.

    Diagnosis (verified on-device): the daemon's full-resolution still looked
    "cropped/weird" vs pi_client.py, but it was NOT a field-of-view difference —
    both read the full sensor (ScalerCrop covered the entire 4056x3040 array). The
    real difference is resolution: the lens is slightly soft/out of focus, a
    full-res 4056x3040 still exposes that softness, while pi_client.py's 640x480
    preview downscales it away and looks clean. Focus is a manual lens ring here
    (LensPosition is unsupported), so until the lens is physically refocused we
    replicate pi_client.py's exact 640x480 preview configuration to produce the
    same usable frame. capture_file() works on a preview configuration too, so the
    burst capture path is unchanged.
    """
    picam2 = Picamera2()
    config = picam2.create_preview_configuration(main={"size": (640, 480)})
    picam2.configure(config)
    picam2.start()
    # Let auto-exposure settle before the first capture.
    time.sleep(2)
    print("[OK] Pi AI Camera started (preview 640x480, matching pi_client.py)")
    return picam2


# Keys the vision server might use for the recognized plate string, in priority
# order. The /detect response contract is owned by the off-Pi vision PC and is not
# pinned in this repo, so plate extraction stays defensive (see extract_plate).
_PLATE_KEYS = ("plate_number", "plate", "license_plate", "lp", "text")
_PLATE_LIST_KEYS = ("results", "detections", "plates", "data")


def extract_plate(data: object) -> str | None:
    """Best-effort pull of a plate string from the vision server's JSON.

    Handles the common shapes without assuming one: a flat object keyed by
    plate_number/plate/license_plate/..., or a results/detections list of such
    objects (recursively, taking the first non-empty match), or a bare string.
    Returns the trimmed plate, or None if nothing plate-like is present.
    """
    if isinstance(data, dict):
        for key in _PLATE_KEYS:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
        for list_key in _PLATE_LIST_KEYS:
            items = data.get(list_key)
            if isinstance(items, list):
                for item in items:
                    plate = extract_plate(item)
                    if plate:
                        return plate
    elif isinstance(data, list):
        for item in data:
            plate = extract_plate(item)
            if plate:
                return plate
    elif isinstance(data, str) and data.strip():
        return data.strip()
    return None


def forward_plate_to_backend(plate_number: str) -> None:
    """Forward a recognized plate to the local backend so the DB + frontend update.

    POSTs to /gate/entrance (same router the reader already uses for /sensors/*),
    which assigns the first free spot and opens a ParkingSession. Fails soft so a
    backend hiccup never kills the read loop.
    """
    payload = {"lot_id": LOT_ID, "plate_number": plate_number}
    try:
        r = requests.post(
            f"{BASE_URL}/gate/entrance", json=payload, headers=_HEADERS, timeout=HTTP_TIMEOUT
        )
        print(f"[ENTRANCE] plate {plate_number!r} -> {r.status_code} {r.text[:160]}")
    except requests.exceptions.RequestException as e:
        print(f"[ERR] entrance forward failed for {plate_number!r}: {e}")


def _save_capture(picam2: Picamera2) -> str:
    """Capture one full-res JPEG to CAPTURE_DIR (local record) and return its path.

    Filenames carry microseconds so the many frames of a single burst (often
    several within the same wall-clock second) never collide / overwrite.
    """
    now = datetime.now(KST)
    filename = now.strftime("%Y%m%d_%H%M%S_%f") + ".jpg"
    path = os.path.join(CAPTURE_DIR, filename)
    picam2.capture_file(path)
    return path


def _push_to_vision(path: str) -> str | None:
    """Push one JPEG to the vision PC and return the recognized plate, or None.

    Fails soft and returns None on ANY trouble (network error, non-JSON body, or
    a JSON body with no plate). The caller is a tight burst loop, so a single bad
    frame must be logged and skipped without breaking the loop.
    """
    filename = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            r = requests.post(
                VISION_URL, files={"image": f}, timeout=CAPTURE_POST_TIMEOUT
            )
        print(f"[VISION] pushed {filename} -> {VISION_URL}: {r.status_code}")
    except requests.exceptions.RequestException as e:
        print(f"[ERR] vision push failed for {filename}: {e}")
        return None

    try:
        result = r.json()
    except ValueError:
        print(f"[WARN] vision response for {filename} was not JSON: {r.text[:160]!r}")
        return None

    return extract_plate(result)


def capture_burst(picam2: Picamera2, distance: str | None) -> None:
    """Capture a high-frequency burst for BURST_DURATION_SEC and stream every frame
    to the vision PC, giving it many shots at one clear, in-focus plate.

    A single still capture often fires too early (hands / the car roof) or is
    motion-blurred. Instead, from the moment of the trigger we capture as fast as
    the hardware allows for a fixed window, saving each frame on-Pi (local record)
    and POSTing it to the vision PC immediately. Every frame is wrapped so a
    single failed capture or upload is logged and skipped without breaking the
    burst. The first recognized plate is forwarded to the backend exactly once;
    capture continues for the full window regardless.
    """
    start = time.monotonic()
    dist_note = f", trigger_distance={distance}cm" if distance else ""
    print(f"[BURST] start {datetime.now(KST).isoformat()} "
          f"for {BURST_DURATION_SEC:.0f}s{dist_note}")

    frames = 0
    plates = 0
    plate_forwarded = False

    while time.monotonic() - start < BURST_DURATION_SEC:
        try:
            path = _save_capture(picam2)
        except Exception as e:  # a single bad capture must not break the burst
            print(f"[ERR] burst capture failed: {e}")
            continue
        frames += 1

        plate = _push_to_vision(path)
        if plate:
            plates += 1
            # Forward only the first plate so one burst opens at most one session.
            if not plate_forwarded:
                forward_plate_to_backend(plate)
                plate_forwarded = True

    elapsed = time.monotonic() - start
    print(f"[BURST] done in {elapsed:.1f}s: {frames} frames captured, "
          f"{plates} plate hit(s), plate_forwarded={plate_forwarded}")


class UnifiedReader:
    """Owns the camera + entrance state; dispatches each serial line by prefix."""

    def __init__(self, picam2: "Picamera2 | None") -> None:
        # picam2 is None in occupancy-only mode (READER_ENABLE_CAMERA=0); ENTRY:
        # lines are then logged but never trigger a capture.
        self.picam2 = picam2
        self._last_capture_ts = 0.0   # monotonic time of last capture (cooldown)
        self._object_present = False  # mirrors the sensor's debounced ENTRY state

    def handle_line(self, line: str) -> None:
        line = line.strip()
        if not line:
            return

        if line.startswith("HB:"):
            print(f"[HB] {line}")
            return

        if line.startswith("ENTRY:"):
            self._handle_entry(line)
            return

        # Everything else is occupancy (SPOT:) or noise; the bridge logic logs
        # unrecognised lines via [SKIP].
        handle_spot(line)

    def _handle_entry(self, line: str) -> None:
        m = ENTRY_RE.match(line)
        if not m:
            print(f"[SKIP] {line!r}")
            return

        entry_state, distance = int(m.group(1)), m.group(2)

        if entry_state == 0:
            # Object left the lane: reset, ready for the next vehicle.
            if self._object_present:
                print("[ENTRY:0] lane clear — ready for next trigger")
            self._object_present = False
            return

        # entry_state == 1: object detected.
        self._object_present = True

        # Occupancy-only mode: the camera belongs to pi_client (hexavision-capture),
        # so just log the trigger and never capture here.
        if self.picam2 is None:
            print("[ENTRY:1] object detected (camera handled by pi_client; no capture here)")
            return

        now_mono = time.monotonic()
        if now_mono - self._last_capture_ts < COOLDOWN_SEC:
            remaining = COOLDOWN_SEC - (now_mono - self._last_capture_ts)
            print(f"[SKIP] ENTRY:1 within cooldown ({remaining:.1f}s left) — not capturing")
            return

        try:
            capture_burst(self.picam2, distance)
        except Exception as e:  # capture must never kill the read loop
            print(f"[ERR] burst failed: {e}")
        finally:
            # Start the cooldown from the END of the burst, so back-to-back
            # triggers can't kick off overlapping bursts.
            self._last_capture_ts = time.monotonic()


def run(reader: UnifiedReader) -> None:
    print(f"Unified serial reader starting: {PORT} @ {BAUD} -> "
          f"API {BASE_URL} + captures {CAPTURE_DIR} -> vision {VISION_URL} "
          f"(cooldown {COOLDOWN_SEC:.0f}s)")
    os.makedirs(CAPTURE_DIR, exist_ok=True)

    while True:
        # exclusive=True: this process is the sole owner of the serial bus. A
        # second opener fails fast instead of corrupting the byte stream.
        try:
            ser = serial.Serial(PORT, BAUD, timeout=1, exclusive=True)
        except serial.SerialException as e:
            print(f"[ERR] open {PORT}: {e}; retrying in {RECONNECT_BACKOFF_SEC}s")
            time.sleep(RECONNECT_BACKOFF_SEC)
            continue

        print("[OK] serial open (exclusive)")
        try:
            while True:
                raw = ser.readline()
                if not raw:
                    continue
                reader.handle_line(raw.decode("utf-8", errors="replace"))
        except serial.SerialException as e:
            print(f"[ERR] serial dropped: {e}; reconnecting")
            try:
                ser.close()
            except Exception:
                pass
            time.sleep(RECONNECT_BACKOFF_SEC)


def main() -> None:
    if ENABLE_CAMERA:
        picam2 = init_camera()
    else:
        picam2 = None
        print("[OK] Occupancy-only mode (READER_ENABLE_CAMERA=0): camera not opened; "
              "ENTRY: triggers are logged only. Camera is owned by pi_client "
              "(hexavision-capture).")
    reader = UnifiedReader(picam2)
    try:
        run(reader)
    except KeyboardInterrupt:
        print("\n[System] Unified serial reader stopped.")
    finally:
        try:
            if picam2 is not None:
                picam2.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
