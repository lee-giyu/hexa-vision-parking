"""API route handlers for parking spot operations."""

import math
from datetime import datetime
 
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models import PaymentTransaction, ParkingLot, ParkingSession, ParkingSpot, PricingPolicy, SpotDevice, Vehicle, User, PaymentCard
from app.schemas.parking import (
    DashboardMetrics,
    PlateRecognitionRequest,
    SensorExitRequest,
    SensorPingRequest,
    SpotStatusResponse,
    TransactionResponse,
    CardCreateRequest,
    CardDeleteRequest,
    VehicleCreateRequest,
    VehicleDeleteRequest,
    SensorPingRequest,
    SensorExitRequest,
)

router = APIRouter()


def _close_session_and_charge(session: ParkingSession, db: Session) -> tuple[float, int]:
    """Close an active session: compute fee, write a paid transaction, persist.

    DEMO override: the fee is a flat 100 KRW per minute parked, rounded up so any
    non-zero duration bills at least 1 minute (100 KRW). This deliberately bypasses
    the lot's PricingPolicies (hourly rate + free-time) so the charged amount stays
    perfectly in sync with the frontend's real-time per-minute ticking UI. Marks the
    session closed and records a PaymentTransaction with payment_status='paid', then
    commits.

    Args:
        session: The active ParkingSession to close.
        db: Database session.

    Returns:
        tuple[float, int]: (elapsed_minutes, fee in KRW).

    Raises:
        HTTPException: 500 if the DB commit fails.
    """
    exit_time = datetime.utcnow()
    elapsed_minutes = (exit_time - session.entry_time).total_seconds() / 60

    # Flat demo rate: 100 KRW/min, rounded up. ceil guarantees a 1-2 min test
    # bills 100/200 KRW and never 0 (any duration > 0 counts as >= 1 minute).
    fee = math.ceil(elapsed_minutes) * 100

    session.exit_time = exit_time
    session.parking_fee = fee
    session.session_status = "closed"

    txn = PaymentTransaction(
        session_id=session.session_id,
        amount=fee,
        payment_status="paid",
        paid_at=exit_time,
    )
    db.add(txn)

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status_code=500, detail="Failed to record vehicle exit.")

    return elapsed_minutes, fee


@router.get("/", tags=["Health Check"])
def read_root():
    """Return a simple health-check response confirming server availability.

    Returns:
        dict: A message indicating operational status.
    """
    return {"message": "Hexa-Vision Parking API is running."}


@router.get("/spots", response_model=list[SpotStatusResponse], tags=["Parking"])
def get_parking_spots(db: Session = Depends(get_db)):
    """Return the real-time occupancy state of every parking spot (powers the
    frontend's live entrance/exit monitor).

    Occupancy is read directly from ParkingSpots.is_occupied, which is updated by
    sensor pings (POST /sensors/ping). This is an independent source of truth from
    the session-based occupancy used by /dashboard/metrics — see CODE_AUDIT.md (M3).

    For each spot, the in-progress ParkingSession (exit_time IS NULL) is joined in
    to expose the currently parked vehicle's plate_number and entry_time; both are
    None for spots with no active session. Active sessions are fetched once and
    mapped by spot_id, so there is no per-spot N+1 query.

    Returns:
        list[SpotStatusResponse]: For every spot, its display name, occupancy
            state, and (when occupied) the current vehicle's plate and entry time.
    """
    spots = db.query(ParkingSpot).order_by(ParkingSpot.spot_number).all()

    # Fetch all in-progress (not-yet-exited) sessions once and map spot_id -> session.
    # If a spot has multiple open sessions, the most recent entry wins.
    active_sessions = (
        db.query(ParkingSession)
        .filter(ParkingSession.exit_time == None)  # noqa: E711 (SQL IS NULL)
        .order_by(ParkingSession.entry_time.asc())
        .all()
    )
    session_by_spot = {s.spot_id: s for s in active_sessions}

    results = []
    for spot in spots:
        # Display name: 'A-1' when a section exists, otherwise just the number (e.g. '1').
        spot_name = f"{spot.section}-{spot.spot_number}" if spot.section else str(spot.spot_number)

        session = session_by_spot.get(spot.spot_id)
        results.append(
            SpotStatusResponse(
                spot_name=spot_name,
                is_occupied=bool(spot.is_occupied),
                plate_number=session.plate_number if session else None,
                entry_time=session.entry_time if session else None,
            )
        )

    return results

@router.post("/gate/entrance", tags=["Gate"], dependencies=[Depends(require_api_key)])
def register_vehicle_entry(
    req: PlateRecognitionRequest, db: Session = Depends(get_db)
):
    """Register a vehicle entry event from the YOLO gate camera.

    Finds the first available spot in the requested lot (no active session), looks up
    the recognized plate against registered Vehicles to attach the owner, creates a
    new ParkingSession, and returns confirmation. Unregistered plates are accepted as
    guest sessions with user_id/vehicle_id left NULL.

    Args:
        req: Incoming payload containing the recognized plate_number and lot_id.
        db: Database session injected via FastAPI dependency.

    Returns:
        dict: Confirmation with the plate_number, assigned spot_id, and whether the
            plate matched a registered member.

    Raises:
        HTTPException: 404 if no vacant spots are available in the lot.
    """
    # Subquery: spot_ids that currently have an active session.
    occupied_spot_ids = (
        db.query(ParkingSession.spot_id)
        .filter(ParkingSession.session_status == "active")
        .subquery()
    )

    # First spot in the requested lot without an active session.
    available_spot = (
        db.query(ParkingSpot)
        .filter(
            ParkingSpot.lot_id == req.lot_id,
            ~ParkingSpot.spot_id.in_(occupied_spot_ids),
        )
        .order_by(ParkingSpot.spot_number)
        .first()
    )

    if not available_spot:
        raise HTTPException(
            status_code=404,
            detail="No vacant spots available in the lot.",
        )

    # Map the plate to a registered member, if one exists. Unregistered cars
    # park as guests: user_id/vehicle_id stay None so the nullable FKs hold.
    vehicle = (
        db.query(Vehicle)
        .filter(Vehicle.plate_number == req.plate_number)
        .first()
    )

    session = ParkingSession(
        spot_id=available_spot.spot_id,
        plate_number=req.plate_number,
        user_id=vehicle.user_id if vehicle else None,
        vehicle_id=vehicle.vehicle_id if vehicle else None,
        session_status="active",
    )
    db.add(session)
    db.commit()

    return {
        "status": "success",
        "message": "Vehicle entered.",
        "plate_number": req.plate_number,
        "assigned_spot_id": available_spot.spot_id,
        "registered_member": vehicle is not None,
    }


# ---------------------------------------------------------------------------
# Stub routes — auth wired in; business logic to be implemented next phase.
# ---------------------------------------------------------------------------

@router.post("/gate/exit", tags=["Gate"], dependencies=[Depends(require_api_key)])
def register_vehicle_exit(req: PlateRecognitionRequest, db: Session = Depends(get_db)):
    """Close an active parking session and record a payment transaction.

    Looks up the active session by plate_number, computes the fee using the
    lot's current PricingPolicy (falls back to 1,000 KRW per started 10 minutes
    if no policy exists), closes the session, and writes a PaymentTransaction.

    Args:
        req: Payload with plate_number (and lot_id, used for context).
        db: Database session injected via FastAPI dependency.

    Returns:
        dict: Confirmation with plate_number, session_id, elapsed_minutes,
            and parking_fee.

    Raises:
        HTTPException: 404 if no active session matches the plate.
        HTTPException: 500 if the DB commit fails.
    """
    session = (
        db.query(ParkingSession)
        .filter(
            ParkingSession.plate_number == req.plate_number,
            ParkingSession.session_status == "active",
        )
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"No active session found for plate '{req.plate_number}'.",
        )

    elapsed_minutes, fee = _close_session_and_charge(session, db)

    return {
        "status": "success",
        "message": "Vehicle exited.",
        "plate_number": req.plate_number,
        "session_id": session.session_id,
        "elapsed_minutes": round(elapsed_minutes, 1),
        "parking_fee": fee,
    }


@router.post("/sensors/exit", tags=["Hardware"], dependencies=[Depends(require_api_key)])
def register_sensor_exit(req: SensorExitRequest, db: Session = Depends(get_db)):
    """Close the active session for a spot when its sensor reports it vacated.

    Finds the active ParkingSession by spot_id (newest by entry_time), then reuses
    the shared fee/transaction logic to close it and record a paid transaction.

    Args:
        req: Payload with lot_id and the vacated spot_id.
        db: Database session injected via FastAPI dependency.

    Returns:
        dict: Confirmation with spot_id, session_id, elapsed_minutes, parking_fee.

    Raises:
        HTTPException: 404 if no active session exists for the spot.
    """
    session = (
        db.query(ParkingSession)
        .filter(
            ParkingSession.spot_id == req.spot_id,
            ParkingSession.session_status == "active",
        )
        .order_by(ParkingSession.entry_time.desc())
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=404,
            detail=f"No active session found for spot_id {req.spot_id}.",
        )

    elapsed_minutes, fee = _close_session_and_charge(session, db)

    return {
        "status": "success",
        "message": "Vehicle exited (sensor).",
        "spot_id": req.spot_id,
        "session_id": session.session_id,
        "elapsed_minutes": round(elapsed_minutes, 1),
        "parking_fee": fee,
    }


@router.post("/lpr/trigger", tags=["LPR"], dependencies=[Depends(require_api_key)])
def trigger_lpr(lot_id: int, db: Session = Depends(get_db)):
    """Check lot availability and signal the YOLOv5 pipeline to capture a plate.

    Queries for any spot in the requested lot that does not have an active
    session.  Returns 400 immediately when the lot is full so the edge client
    can skip the (expensive) YOLO inference step.

    Args:
        lot_id: ID of the parking lot the gate camera belongs to (query param).
        db: Database session injected via FastAPI dependency.

    Returns:
        dict: Status 'ready' and a human-readable message when a spot is free.

    Raises:
        HTTPException: 400 if every spot in the lot has an active session.
    """
    occupied_spot_ids = (
        db.query(ParkingSession.spot_id)
        .filter(ParkingSession.session_status == "active")
        .subquery()
    )
    available = (
        db.query(ParkingSpot)
        .filter(
            ParkingSpot.lot_id == lot_id,
            ~ParkingSpot.spot_id.in_(occupied_spot_ids),
        )
        .first()
    )
    if not available:
        raise HTTPException(status_code=400, detail="Parking lot is full.")
    return {"status": "ready", "message": "Spot available. YOLOv5 pipeline may capture."}


@router.get("/transactions/{plate}", response_model=list[TransactionResponse], tags=["Transactions"], dependencies=[Depends(require_api_key)])
def get_transactions(plate: str, db: Session = Depends(get_db)):
    """Return all payment transactions associated with a given license plate.

    Joins PaymentTransactions -> ParkingSessions on session_id, then chains
    ParkingSessions -> ParkingSpots -> ParkingLots to resolve the owning lot's
    name. Filtered by plate_number and ordered newest-first by paid_at.

    Args:
        plate: URL path segment containing the license plate string.
        db: Database session injected via FastAPI dependency.

    Returns:
        list[TransactionResponse]: Chronologically descending ledger rows,
            each carrying the resolved lot_name.

    Raises:
        HTTPException: 404 if no transactions exist for the given plate.
    """
    rows = (
        db.query(PaymentTransaction, ParkingSession, ParkingLot.lot_name)
        .join(ParkingSession, PaymentTransaction.session_id == ParkingSession.session_id)
        .join(ParkingSpot, ParkingSession.spot_id == ParkingSpot.spot_id)
        .join(ParkingLot, ParkingSpot.lot_id == ParkingLot.lot_id)
        .filter(ParkingSession.plate_number == plate)
        .order_by(PaymentTransaction.paid_at.desc())
        .all()
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No transactions found for plate '{plate}'.",
        )
    return [
        TransactionResponse(
            transaction_id=txn.transaction_id,
            session_id=txn.session_id,
            plate_number=session.plate_number,
            amount=txn.amount,
            payment_status=txn.payment_status,
            paid_at=txn.paid_at,
            entry_time=session.entry_time,
            exit_time=session.exit_time,
            lot_name=lot_name,
        )
        for txn, session, lot_name in rows
    ]


@router.get("/dashboard/metrics", response_model=DashboardMetrics, tags=["Dashboard"], dependencies=[Depends(require_api_key)])
def get_dashboard_metrics(db: Session = Depends(get_db)):
    """Return a real-time snapshot of lot-wide occupancy and revenue.

    Three focused queries:
      1. COUNT(*) on ParkingSpots for the total capacity.
      2. COUNT(*) on active ParkingSessions for current occupancy.
      3. SUM(amount) on completed PaymentTransactions for total revenue.

    Args:
        db: Database session injected via FastAPI dependency.

    Returns:
        DashboardMetrics: total_spots, occupied_spots, available_spots,
            and total_revenue (KRW).
    """
    total_spots = db.query(ParkingSpot).count()
    occupied_spots = (
        db.query(ParkingSession)
        .filter(ParkingSession.session_status == "active")
        .count()
    )
    total_revenue = (
        db.query(func.sum(PaymentTransaction.amount))
        .filter(PaymentTransaction.payment_status == "paid")
        .scalar()
        or 0
    )
    return DashboardMetrics(
        total_spots=total_spots,
        occupied_spots=occupied_spots,
        available_spots=total_spots - occupied_spots,
        total_revenue=total_revenue,
    )

@router.get("/users/{email}/state", tags=["Users"], dependencies=[Depends(require_api_key)])
def get_user_dynamic_state(email: str, db: Session = Depends(get_db)):
    """Resolve a user by email and return their registered vehicles plus any
    active parking session (location/time).

    Backs the frontend's hybrid-login integration.
    """
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # 1. Load the user's vehicles.
    vehicles = db.query(Vehicle).filter(Vehicle.user_id == user.user_id).all()

    cards = db.query(PaymentCard).filter(PaymentCard.user_id == user.user_id).all()
    # 2. Find the currently active parking session, if any.
    plate_numbers = [v.plate_number for v in vehicles]
    active_session = None
    
    if plate_numbers:
        active_session = db.query(ParkingSession).filter(
            ParkingSession.plate_number.in_(plate_numbers),
            ParkingSession.session_status == "active"
        ).first()

    parking_name = "No active parking session"
    entry_time_iso = None

    if active_session:
        entry_time_iso = active_session.entry_time.isoformat()
        spot = db.query(ParkingSpot).filter(ParkingSpot.spot_id == active_session.spot_id).first()
        if spot:
            lot = db.query(ParkingLot).filter(ParkingLot.lot_id == spot.lot_id).first()
            if lot:
                parking_name = lot.lot_name

    return {
        "parking": parking_name,
        "entry_time": entry_time_iso,
        "vehicles": [
            {
                "num": v.plate_number,
                "desc": "Registered vehicle",
                "seasonParking": [] 
            } for v in vehicles
        ],
        "cards": [
            {
                "bank": c.bank_name,
                "num": c.card_number,
                "icon": c.icon_class
            } for c in cards
        ]
    }

@router.post("/users/{email}/cards", tags=["Users"], dependencies=[Depends(require_api_key)])
def add_payment_card(email: str, req: CardCreateRequest, db: Session = Depends(get_db)):
    """Register a new payment card for the user."""
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    new_card = PaymentCard(
        user_id=user.user_id,
        bank_name=req.bank,
        card_number=req.num,
        icon_class=req.icon
    )
    db.add(new_card)
    db.commit()
    
    return {"status": "success", "message": "Card registered."}

@router.delete("/users/{email}/cards", tags=["Users"], dependencies=[Depends(require_api_key)])
def delete_payment_cards(email: str, req: CardDeleteRequest, db: Session = Depends(get_db)):
    """Delete the selected payment cards from the database."""
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Bulk-delete every card whose number is in the request payload (req.card_numbers).
    db.query(PaymentCard).filter(
        PaymentCard.user_id == user.user_id,
        PaymentCard.card_number.in_(req.card_numbers)
    ).delete(synchronize_session=False)

    db.commit()

    return {"status": "success", "message": f"{len(req.card_numbers)} card(s) deleted."}

@router.post("/users/{email}/vehicles", tags=["Users"], dependencies=[Depends(require_api_key)])
def add_vehicle(email: str, req: VehicleCreateRequest, db: Session = Depends(get_db)):
    """Register a new vehicle for the user."""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    # Normalize the plate by stripping surrounding/internal spaces before saving.
    clean_plate = req.num.replace(" ", "").strip()

    new_vehicle = Vehicle(
        user_id=user.user_id,
        plate_number=clean_plate
    )
    db.add(new_vehicle)
    db.commit()

    return {"status": "success", "message": "Vehicle registered."}

@router.delete("/users/{email}/vehicles", tags=["Users"], dependencies=[Depends(require_api_key)])
def delete_vehicles(email: str, req: VehicleDeleteRequest, db: Session = Depends(get_db)):
    """Delete the selected vehicles from the database."""
    user = db.query(User).filter(User.email == email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    db.query(Vehicle).filter(
        Vehicle.user_id == user.user_id,
        Vehicle.plate_number.in_(req.plate_numbers)
    ).delete(synchronize_session=False)

    db.commit()

    return {"status": "success", "message": f"{len(req.plate_numbers)} vehicle(s) deleted."}

@router.post("/sensors/ping", tags=["Sensors"], dependencies=[Depends(require_api_key)])
def handle_sensor_ping(req: SensorPingRequest, db: Session = Depends(get_db)):
    """Update a spot's is_occupied flag from an ultrasonic sensor reading (0/1).

    The spot is resolved by treating the trailing digits of the hardware ID as the
    spot_number (e.g. "HC-SR04-01" -> spot_number 1). IDs without a numeric suffix
    can raise during parsing — see CODE_AUDIT.md (H1).
    """
    # Extract the trailing number of the hardware ID as the spot_number
    # (e.g. "HC-SR04-01" -> 1).
    spot_num = int(req.device_id_hw.split("-")[-1])
    is_occ = True if req.status == 1 else False

    spot = db.query(ParkingSpot).filter(
        ParkingSpot.lot_id == 1,
        ParkingSpot.spot_number == spot_num
    ).first()

    if spot and spot.is_occupied != is_occ:
        spot.is_occupied = is_occ
        db.commit()
        print(f"[sensor ping] spot {spot_num} -> {'occupied' if is_occ else 'vacant'}")

    return {"status": "success", "device": req.device_id_hw, "occupied": is_occ}

@router.post("/sensors/exit", tags=["Sensors"], dependencies=[Depends(require_api_key)])
def handle_sensor_exit(req: SensorExitRequest, db: Session = Depends(get_db)):
    """[Inactive / unreachable] Exit-handling stub.

    The same 'POST /sensors/exit' path is registered earlier on register_sensor_exit,
    so under Starlette routing this handler never receives a request. Session closing
    and fee settlement happen in register_sensor_exit. See CODE_AUDIT.md (M1) for the
    duplicate-route cleanup.
    """
    print(f"[sensor exit] vehicle left spot {req.spot_id}")
    return {"status": "success", "message": f"Spot {req.spot_id} exit processed"}