# Code Audit & QA Report — Hexa-Vision Parking

Audit date: **2026-06-01** (refreshed during the 2026-06 portfolio cleanup).

A self-conducted engineering audit of the parking backend. Every finding was
**verified against running systems**, not inferred from descriptions: the live
**Aiven MySQL DDL** (read over Tailscale), a `pytest` run, and a FastAPI route
dump. Each entry records *what's wrong*, *how it was confirmed*, and a
*recommended fix* with a severity rating.

Severity legend: **HIGH** = breaks a flow / tests, **MED** = latent or fragile,
**LOW** = cosmetic / hygiene. "Blocks demo?" reflects the live
serial→API→DB→frontend path.

---

## ✅ Resolved

### H2 — Serial port contention: two readers on `/dev/ttyUSB0`
- **Resolution:** `serial_bridge.py` and `capture_trigger.py` were merged into a
  single `ai_vision/unified_serial_reader.py` that owns `/dev/ttyUSB0` exclusively and
  dispatches by prefix (`SPOT:`/`HB:` → backend pings, `ENTRY:1` → camera
  capture). The two old scripts and their `hexavision-bridge` /
  `hexavision-capture` units were removed; one `hexavision-reader.service`
  replaces them.
- **Root cause (for the record):** a serial port cannot be reliably shared by two
  readers — the kernel delivers each byte to exactly one open file description,
  so two concurrent `readline()` loops each receive a torn, interleaved stream.
  Confirmed via `dmesg` (`ch341-uart converter now attached to ttyUSB0`): a single
  Arduino Mega on one CH340 bus, with both scripts hard-coding the same port.

### M2 — Duplicate Pydantic class definitions silently discarded validation
- **Resolution (2026-06 cleanup):** the bare re-definitions of
  `SensorPingRequest`/`SensorExitRequest` at the bottom of
  `app/schemas/parking.py` were removed, so the validated classes
  (`max_length=50`, `status` bounded `0|1`) are now the ones the app uses.
- **What had been wrong:** Python keeps the *last* definition, so the bare
  versions (`device_id_hw: str`, `status: int`, no constraints) shadowed the
  richer validated ones — the validation was dead code. This was also the enabler
  of part of **H1** (an out-of-range `device_id_hw` was no longer rejected).

### L6 — Stray module-level docstring mid-file
- **Resolution (2026-06 cleanup):** removed together with M2. The no-op
  triple-quoted string that sat between class definitions in
  `app/schemas/parking.py` is gone.

---

## HIGH

### H1 — `/sensors/ping` returns HTTP 500 on a non-numeric-suffixed `device_id_hw`
- **File:** `app/api/endpoints/parking.py` (`handle_sensor_ping`)
- **What's wrong:** `spot_num = int(req.device_id_hw.split("-")[-1])` assumes the
  hardware ID always ends in digits after a `-`. For IDs like `GPIO_1` (no dash),
  `split("-")[-1]` returns the whole string and `int("GPIO_1")` raises
  `ValueError`, which escapes the handler and becomes a 500.
- **How confirmed:** `pytest` failed 2 tests with
  `ValueError: invalid literal for int() with base 10: 'GPIO_1'`
  (`test_step3_sensor_ping_flips_device_to_occupied`,
  `test_complete_vehicle_lifecycle`) — both seed `device_id_hw` as `GPIO_n`.
- **Why the live demo still worked:** the production reader only ever sends
  `HC-SR04-0X`, whose suffix is numeric, so the live path never hit the crash.
- **Status:** **partially mitigated** — M2's fix restored length/value validation
  on `device_id_hw`, but the unsafe `int(...)` parse on a non-numeric suffix
  remains. Recommended fix: resolve the spot via the authoritative
  `SpotDevices.device_id_hw` row instead of string-parsing the ID, or guard the
  parse and return 400/404 on a non-numeric suffix.
- **Blocks demo?** No (live reader path unaffected). **Blocks tests?** Yes.

---

## MED

### M1 — Duplicate `POST /sensors/exit` route; the stub shadows nothing useful
- **File:** `app/api/endpoints/parking.py` (`register_sensor_exit` **and**
  `handle_sensor_exit`)
- **What's wrong:** Two handlers register the same `POST /sensors/exit`. Starlette
  matches the **first-registered** route, so `register_sensor_exit` (which closes
  the session and writes a paid `PaymentTransaction`) wins; `handle_sensor_exit`
  (a `print`-only stub that returns success without closing anything) is **dead,
  unreachable code**.
- **How confirmed:** FastAPI route dump shows two `POST /sensors/exit` entries, in
  that order.
- **Recommended fix:** delete the `handle_sensor_exit` stub so only the real
  charging handler remains.
- **Blocks demo?** No, but it is misleading and risks a future edit "fixing" the
  wrong one.

### M3 — Two competing occupancy sources of truth
- **Files:** `app/api/endpoints/parking.py` — `/spots` (reads
  `ParkingSpots.is_occupied`), `/gate/entrance` and `/dashboard/metrics` (read
  `ParkingSessions`), `/sensors/ping` (writes `ParkingSpots.is_occupied`).
- **What's wrong:** `/spots` reflects **sensor pings** (`is_occupied`), while
  `/dashboard/metrics` and entry/exit logic reflect **sessions**. Entrance/exit
  never touch `is_occupied`, and the ping never touches sessions, so the map and
  the metrics can disagree (e.g. a guest car with no sensor coverage, or a
  sensor-only occupancy with no session). The old `SpotDevices.current_state`
  column is now orphaned.
- **How confirmed:** Live DB rows + code paths; live `GET /spots` returns 200.
- **Recommended fix:** pick one source of truth — either drive `is_occupied` from
  the session lifecycle, or have `/spots` derive occupancy from active sessions;
  and retire/maintain `SpotDevices.current_state` consistently.
- **Blocks demo?** No, but map vs dashboard may visibly disagree.

### M4 — `spot_id` vs `spot_number` conflation in the sensor flow
- **Files:** `app/api/endpoints/parking.py` — `/sensors/ping` resolves by
  `spot_number`, `/sensors/exit` resolves by `spot_id` PK; the reader posts the
  same floor-plan number to both.
- **What's wrong:** The reader sends a **floor-plan logical number** (1,2,3,5,7,8).
  The ping endpoint treats it as `spot_number`; the exit endpoint treats it as the
  `spot_id` primary key. These only coincide because the live DB happens to have
  `spot_id == spot_number`. If rows are ever re-seeded so PKs diverge,
  `/sensors/exit` would close the wrong session or none.
- **How confirmed:** Live `SELECT spot_id, spot_number` shows 1:1; code paths
  differ.
- **Recommended fix:** make both endpoints resolve spots the same way (preferably
  `lot_id` + `spot_number`, or both by PK), and document which identifier the
  reader emits.
- **Blocks demo?** No (holds by coincidence on the current DB).

### M5 — Backend `requirements.txt` is polluted with vision / CUDA dependencies
- **File:** `requirements.txt`
- **What's wrong:** The backend lockfile pins `torch`, `torchvision`,
  `ultralytics`, `opencv-python`, `triton`, and a full `nvidia-*` / `cuda-*`
  stack. The backend `.venv` is meant to be FastAPI/SQLAlchemy-only and vision
  runs off-Pi. These heavy CUDA wheels do not belong in (and will not install
  cleanly on) the Pi backend venv.
- **How confirmed:** File contents vs the "two uv venvs must never be mixed"
  constraint.
- **Recommended fix:** regenerate `requirements.txt` from a clean backend venv
  (fastapi, uvicorn, sqlalchemy, pymysql, pydantic, python-dotenv, pyserial,
  requests).
- **Blocks demo?** No (existing `.venv` already works), but breaks reproducible
  installs.

### M6 — `frontend/` is referenced in docs but lives in a separate repo
- **Files (docs):** `README.md`
- **What's wrong:** Both documents describe `frontend/index.html` (PWA,
  `API_CONFIG`, service worker), but there is no `frontend/` directory here — the
  frontend is deployed separately (GitHub Pages).
- **How confirmed:** `git ls-files` shows nothing under `frontend/`.
- **Recommended fix:** either commit the frontend into this repo or keep it in its
  own GitHub Pages repo and make the docs say so (the docs now state the latter).
- **Blocks demo?** No.

---

## LOW

### L1 — Duplicate imports
- **File:** `app/api/endpoints/parking.py` imports `SensorExitRequest` and
  `SensorPingRequest` twice. Harmless but should be de-duplicated.

### L2 — Deprecated `datetime.utcnow()`
- **Files:** `app/api/endpoints/parking.py`; `app/models/payment_card.py`
  (`default=datetime.utcnow`).
- Emits `DeprecationWarning` on Python 3.12+; prefer timezone-aware
  `datetime.now(UTC)`.

### L3 — `ai_vision/edge_trigger_client.py` posts without an API key
- **File:** `ai_vision/edge_trigger_client.py` posts to `/gate/entrance` with no `X-API-Key`
  header. `/gate/entrance` now requires the key, so this mock harness would
  receive 403. Reference/mock only; not on the live path.

### L4 — CORS `allow_credentials=True` with possible wildcard origin
- **File:** `app/main.py`. If `ALLOWED_ORIGINS=*` (allowed outside production),
  browsers reject credentialed requests against a wildcard origin. Only relevant
  in dev wildcard mode.

### L5 — Schema/style inconsistency in `PaymentCard`
- **File:** `app/models/payment_card.py`. Table is lowercase `payment_cards`
  (others are CamelCase, e.g. `ParkingSpots`) and it uses `DateTime`/
  `datetime.utcnow` rather than the `TIMESTAMP`/`server_default` pattern used
  elsewhere.

### L7 — Seed/DB spot-count drift
- **File:** `app/utils/seed.py` creates 8 spots (section "A", 1–8); the live DB
  has 10 spots (1–10) and 10 `HC-SR04-0N` sensor rows. Re-running the seed will
  not reconcile the extra rows.

### L8 — Stale documentation literal
- **File:** `app/schemas/parking.py` — `TransactionResponse` docstring cites
  `payment_status` "e.g. 'completed'". The live CHECK constraint allows only
  `{pending, paid, failed, refunded}`; the code correctly writes `paid`. Comment
  only.

---

## Explicitly checked — no bug found

- **Enum literals vs live DDL CHECK constraints:** all compliant. Verified
  domains: `payment_status ∈ {pending,paid,failed,refunded}`,
  `session_status ∈ {active,closed}`, `spot_type ∈ {general,disabled,ev,compact}`,
  `device_role ∈ {sensor,actuator}`,
  `device_type ∈ {ultrasonic,magnetic,camera,led_indicator,other}`. The code
  writes `payment_status="paid"` and `/dashboard/metrics` filters revenue on
  `"paid"`.
- **Hardware/vision isolation:** no active backend module imports vision or
  GPIO/serial libraries from `ai_vision/` — the separation constraint holds.
- **Sensor occupancy threshold:** confirmed `OCCUPIED_THRESHOLD_CM = 10` in the
  Arduino sketch.
- **Dead sensors S1/S7:** correctly excluded from the sketch `SENSORS[]` table
  (only S2,S3,S4,S5,S6,S8 are wired/read).
- **Vehicle/card add-delete endpoints:** functional and consistent (lookup by
  email, bulk delete with `synchronize_session=False`).
