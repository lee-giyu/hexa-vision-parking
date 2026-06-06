"""Integration tests: full virtual vehicle lifecycle end-to-end."""

import os

# Must set env vars before any app module is imported (modules validate at import time)
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASSWORD", "test")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "3306")
os.environ.setdefault("DB_NAME", "testdb")
_MOCK_API_KEY = "test-mock-key-hexavision"
os.environ.setdefault("HEXAVISION_API_KEY", _MOCK_API_KEY)

from datetime import datetime, timezone, UTC

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# Patch app.core.database to use SQLite before app.main is imported.
# create_engine is lazy so the MySQL URL from env vars never actually connects;
# we immediately replace both engine and SessionLocal in the module namespace so
# the get_db closure picks up the SQLite session at call time.
import app.core.database as _db_mod

_TEST_ENGINE = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TEST_SESSION = sessionmaker(autocommit=False, autoflush=False, bind=_TEST_ENGINE)

_db_mod.engine = _TEST_ENGINE
_db_mod.SessionLocal = _TEST_SESSION

from app.core.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    ParkingLot,
    ParkingSession,
    ParkingSpot,
    PaymentTransaction,
    PricingPolicy,
    SpotDevice,
    User,
)

# Patch MySQL-only server_default before creating SQLite schema.
# DefaultClause supports proper boolean evaluation in SQLAlchemy's ORM mapper;
# raw text() raises TypeError on `not col.server_default` in _insert_cols_as_none.
from sqlalchemy.schema import DefaultClause as _DefaultClause  # noqa: E402
SpotDevice.__table__.c.last_updated.server_default = _DefaultClause("CURRENT_TIMESTAMP")

Base.metadata.create_all(bind=_TEST_ENGINE)


def _override_get_db():
    db = _TEST_SESSION()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db

client = TestClient(app)
_HEADERS = {"X-API-Key": _MOCK_API_KEY}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_db():
    """Wipe all rows before each test for full isolation."""
    db = _TEST_SESSION()
    try:
        # FK-safe deletion order (SQLite doesn't enforce FKs by default, but correct order helps portability)
        db.query(PaymentTransaction).delete()
        db.query(ParkingSession).delete()
        db.query(SpotDevice).delete()
        db.query(ParkingSpot).delete()
        db.query(PricingPolicy).delete()
        db.query(ParkingLot).delete()
        db.query(User).delete()
        db.commit()
    finally:
        db.close()
    yield


@pytest.fixture
def seeded_lot(clean_db):
    """Insert operator, lot, 3 spots with sensor devices, and a 3,000 KRW/hr policy."""
    db = _TEST_SESSION()
    try:
        operator = User(
            email="system@hexavision.internal",
            phone_number="0000000000",
            password_hash="!disabled!",
            user_name="System Operator",
            role="operator",
        )
        db.add(operator)
        db.flush()

        lot = ParkingLot(
            operator_id=operator.user_id,
            lot_name="Test Lot",
            address="Test Address",
            total_spots=3,
            is_active=True,
        )
        db.add(lot)
        db.flush()

        policy = PricingPolicy(
            lot_id=lot.lot_id,
            base_rate_per_hour=3000,
            free_time_minutes=0,
            applied_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        )
        db.add(policy)
        db.flush()

        spots, devices = [], []
        for n in range(1, 4):
            spot = ParkingSpot(
                lot_id=lot.lot_id,
                floor_number=1,
                floor_label="1F",
                section="A",
                spot_number=n,
                spot_type="general",
            )
            db.add(spot)
            db.flush()

            device = SpotDevice(
                spot_id=spot.spot_id,
                device_id_hw=f"GPIO_{n}",
                device_type="ultrasonic",
                device_role="sensor",
                current_state=False,
                # Supply explicitly so SQLAlchemy doesn't RETURNING the MySQL-only
                # CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP expression from SQLite
                last_updated=datetime.now(UTC).replace(tzinfo=None),
            )
            db.add(device)
            db.flush()
            spots.append(spot)
            devices.append(device)

        db.commit()
        yield {"lot": lot, "spots": spots, "devices": devices, "policy": policy}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Step-by-step tests
# ---------------------------------------------------------------------------

class TestParkingLifecycle:

    def test_step1_lpr_trigger_returns_ready(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        resp = client.post(f"/lpr/trigger?lot_id={lot_id}", headers=_HEADERS)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ready"

    def test_step2_gate_entrance_assigns_active_session(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        resp = client.post(
            "/gate/entrance",
            json={"plate_number": "123가4567", "lot_id": lot_id},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["plate_number"] == "123가4567"
        assigned_spot_id = body["assigned_spot_id"]
        assert isinstance(assigned_spot_id, int)

        db = _TEST_SESSION()
        try:
            session = (
                db.query(ParkingSession)
                .filter_by(plate_number="123가4567", session_status="active")
                .first()
            )
            assert session is not None
            assert session.spot_id == assigned_spot_id
        finally:
            db.close()

    def test_step3_sensor_ping_flips_device_to_occupied(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        entrance = client.post(
            "/gate/entrance",
            json={"plate_number": "123가4567", "lot_id": lot_id},
            headers=_HEADERS,
        )
        assert entrance.status_code == 200
        assigned_spot_id = entrance.json()["assigned_spot_id"]

        db = _TEST_SESSION()
        try:
            hw_id = (
                db.query(SpotDevice)
                .filter_by(spot_id=assigned_spot_id, device_role="sensor")
                .first()
                .device_id_hw
            )
        finally:
            db.close()

        ping = client.post(
            "/sensors/ping",
            json={"device_id_hw": hw_id, "status": 1},
            headers=_HEADERS,
        )
        assert ping.status_code == 200
        assert ping.json()["current_state"] is True

        db = _TEST_SESSION()
        try:
            assert db.query(SpotDevice).filter_by(device_id_hw=hw_id).first().current_state is True
        finally:
            db.close()

    def test_step4_dashboard_metrics_occupancy_increments(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id

        before = client.get("/dashboard/metrics", headers=_HEADERS).json()
        assert before["occupied_spots"] == 0

        client.post(
            "/gate/entrance",
            json={"plate_number": "123가4567", "lot_id": lot_id},
            headers=_HEADERS,
        )

        after = client.get("/dashboard/metrics", headers=_HEADERS).json()
        assert after["occupied_spots"] == before["occupied_spots"] + 1
        assert after["available_spots"] == before["available_spots"] - 1

    def test_step5_gate_exit_fee_and_payment_transaction(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        plate = "123가4567"

        client.post(
            "/gate/entrance",
            json={"plate_number": plate, "lot_id": lot_id},
            headers=_HEADERS,
        )

        resp = client.post(
            "/gate/exit",
            json={"plate_number": plate, "lot_id": lot_id},
            headers=_HEADERS,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "success"
        assert body["plate_number"] == plate
        assert isinstance(body["elapsed_minutes"], float)
        assert isinstance(body["parking_fee"], int)
        assert body["parking_fee"] >= 0

        session_id = body["session_id"]

        db = _TEST_SESSION()
        try:
            session = db.query(ParkingSession).filter_by(session_id=session_id).first()
            assert session.session_status == "closed"
            assert session.exit_time is not None

            txn = db.query(PaymentTransaction).filter_by(session_id=session_id).first()
            assert txn is not None
            assert txn.payment_status == "paid"
            assert txn.amount == body["parking_fee"]
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Full end-to-end test
# ---------------------------------------------------------------------------

class TestFullLifecycleE2E:

    def test_complete_vehicle_lifecycle(self, seeded_lot):
        """LPR trigger -> entrance -> sensor ping -> metrics -> exit + transaction ledger."""
        lot_id = seeded_lot["lot"].lot_id
        plate = "999나1234"

        # Step 1: LPR trigger confirms space available
        r1 = client.post(f"/lpr/trigger?lot_id={lot_id}", headers=_HEADERS)
        assert r1.status_code == 200, r1.text

        # Step 2: Vehicle enters; spot is assigned
        r2 = client.post(
            "/gate/entrance",
            json={"plate_number": plate, "lot_id": lot_id},
            headers=_HEADERS,
        )
        assert r2.status_code == 200, r2.text
        assigned_spot_id = r2.json()["assigned_spot_id"]

        # Step 3: Hardware sensor fires for that spot
        db = _TEST_SESSION()
        try:
            hw = (
                db.query(SpotDevice)
                .filter_by(spot_id=assigned_spot_id, device_role="sensor")
                .first()
                .device_id_hw
            )
        finally:
            db.close()
        r3 = client.post("/sensors/ping", json={"device_id_hw": hw, "status": 1}, headers=_HEADERS)
        assert r3.status_code == 200, r3.text

        # Step 4: Dashboard reports at least one active occupancy
        r4 = client.get("/dashboard/metrics", headers=_HEADERS)
        assert r4.status_code == 200, r4.text
        assert r4.json()["occupied_spots"] >= 1

        # Step 5: Vehicle exits — fee computed, session closed, transaction in ledger
        r5 = client.post(
            "/gate/exit",
            json={"plate_number": plate, "lot_id": lot_id},
            headers=_HEADERS,
        )
        assert r5.status_code == 200, r5.text
        session_id = r5.json()["session_id"]

        db = _TEST_SESSION()
        try:
            assert db.query(PaymentTransaction).filter_by(session_id=session_id).count() == 1
            closed = db.query(ParkingSession).filter_by(session_id=session_id).first()
            assert closed.session_status == "closed"
        finally:
            db.close()


# ---------------------------------------------------------------------------
# Security layer
# ---------------------------------------------------------------------------

class TestSecurityLayer:

    def test_protected_endpoints_reject_missing_api_key(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        cases = [
            ("post", f"/lpr/trigger?lot_id={lot_id}", None),
            ("post", "/gate/entrance", {"plate_number": "123가4567", "lot_id": lot_id}),
            ("post", "/sensors/ping", {"device_id_hw": "GPIO_1", "status": 0}),
            ("post", "/gate/exit", {"plate_number": "123가4567", "lot_id": lot_id}),
            ("get", "/dashboard/metrics", None),
        ]
        for method, path, payload in cases:
            fn = getattr(client, method)
            kwargs = {} if payload is None else {"json": payload}
            resp = fn(path, **kwargs)
            assert resp.status_code == 403, (
                f"Expected 403 on {method.upper()} {path}, got {resp.status_code}"
            )

    def test_protected_endpoints_reject_wrong_api_key(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        bad_headers = {"X-API-Key": "wrong-key"}
        resp = client.post(f"/lpr/trigger?lot_id={lot_id}", headers=bad_headers)
        assert resp.status_code == 403

    def test_valid_api_key_grants_access(self, seeded_lot):
        lot_id = seeded_lot["lot"].lot_id
        resp = client.post(f"/lpr/trigger?lot_id={lot_id}", headers=_HEADERS)
        assert resp.status_code == 200
