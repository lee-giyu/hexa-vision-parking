"""Sprint 1 seed: insert baseline master data into Aiven MySQL.

Run directly:
    python -m app.utils.seed
"""

from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.parking_lot import ParkingLot, PricingPolicy
from app.models.parking_spot import ParkingSpot
from app.models.user import User

_SPOT_TYPE_MAP = {
    1: "general",
    2: "general",
    3: "general",
    4: "general",
    5: "general",
    6: "compact",
    7: "disabled",
    8: "ev",
}


def seed() -> None:
    db = SessionLocal()
    try:
        # ------------------------------------------------------------------
        # 1. System operator (required by ParkingLots.operator_id NOT NULL)
        # ------------------------------------------------------------------
        operator = db.query(User).filter_by(email="system@hexavision.internal").first()
        if not operator:
            operator = User(
                email="system@hexavision.internal",
                phone_number="0000000000",
                password_hash="!disabled!",
                user_name="System Operator",
                role="operator",
            )
            db.add(operator)
            db.flush()
            print(f"[+] User          user_id={operator.user_id}  '{operator.user_name}'")
        else:
            print(f"[~] User          user_id={operator.user_id}  (already present)")

        # ------------------------------------------------------------------
        # 2. ParkingLot  lot_id=1
        # ------------------------------------------------------------------
        lot = db.query(ParkingLot).filter_by(lot_id=1).first()
        if not lot:
            lot = ParkingLot(
                lot_id=1,
                operator_id=operator.user_id,
                lot_name="Hexavision Surface Lot",
                address="Hexa Vision Campus, Block A",
                total_spots=8,
                description="An open-air, ground-level surface parking lot.",
                is_active=True,
            )
            db.add(lot)
            db.flush()
            print(f"[+] ParkingLot    lot_id={lot.lot_id}  '{lot.lot_name}'")
        else:
            print(f"[~] ParkingLot    lot_id={lot.lot_id}  (already present)")

        # ------------------------------------------------------------------
        # 3. PricingPolicy  3,000 KRW/hr, 30-min free window
        # ------------------------------------------------------------------
        policy = db.query(PricingPolicy).filter_by(lot_id=lot.lot_id).first()
        if not policy:
            policy = PricingPolicy(
                lot_id=lot.lot_id,
                base_rate_per_hour=3000,
                free_time_minutes=30,
                applied_from=datetime.now(timezone.utc),
            )
            db.add(policy)
            db.flush()
            print(
                f"[+] PricingPolicy  policy_id={policy.policy_id}  "
                f"3,000 KRW/hr  30 min free  applied_from={policy.applied_from:%Y-%m-%d %H:%M:%S UTC}"
            )
        else:
            print(f"[~] PricingPolicy  policy_id={policy.policy_id}  (already present)")

        # ------------------------------------------------------------------
        # 4. ParkingSpots  spot_number 1–8, floor=1 / "1F", section="A"
        # ------------------------------------------------------------------
        created = 0
        for n in range(1, 9):
            exists = (
                db.query(ParkingSpot)
                .filter_by(lot_id=lot.lot_id, floor_number=1, section="A", spot_number=n)
                .first()
            )
            if not exists:
                db.add(
                    ParkingSpot(
                        lot_id=lot.lot_id,
                        floor_number=1,
                        floor_label="1F",
                        section="A",
                        spot_number=n,
                        spot_type=_SPOT_TYPE_MAP[n],
                    )
                )
                created += 1

        db.flush()
        if created:
            print(f"[+] ParkingSpots  {created} spot(s) created  (1F / section A / numbers 1-8)")
            print(f"    types: 1-5=general  6=compact  7=disabled  8=ev")
        else:
            print(f"[~] ParkingSpots  all 8 spots already present")

        # ------------------------------------------------------------------
        db.commit()
        print("\n[OK] Seed complete — transaction committed to Aiven MySQL.")

    except Exception as exc:
        db.rollback()
        print(f"\n[FAIL] Seed aborted — transaction rolled back.\n       {type(exc).__name__}: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
