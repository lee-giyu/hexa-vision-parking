"""Pydantic request/response schemas for parking endpoints."""

import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class SpotStatusResponse(BaseModel):
    """Combined view of a parking spot and its live session state for the Frontend.

    Powers the real-time entrance/exit monitor: occupancy comes from the
    sensor-backed is_occupied flag, while plate_number/entry_time are pulled
    from the spot's active ParkingSession (exit_time IS NULL) so the UI can show which
    plate is in which spot and since when. Both session fields are None for a vacant
    spot (or an occupied spot with no matching open session).

    Attributes:
        spot_name: Human-readable spot label constructed as '{section}-{spot_number}'.
        is_occupied: Current occupancy state derived from the sensor's current_state.
        plate_number: Plate of the car currently parked here, from the active session.
        entry_time: When that car entered, from the active session.
    """

    spot_name: str
    is_occupied: bool
    plate_number: str | None = None
    entry_time: datetime | None = None


class SensorPingRequest(BaseModel):
    """Request schema for hardware sensor state updates.

    Attributes:
        device_id_hw: Unique hardware identifier of the sensor (e.g., 'GPIO_4').
            Max 50 characters, matching the DDL VARCHAR(50) constraint.
        status: Occupancy flag — strictly 0 (vacant) or 1 (occupied).
    """

    device_id_hw: str = Field(
        ...,
        min_length=1,
        max_length=50,
        examples=["GPIO_4"],
    )
    status: int = Field(
        ...,
        ge=0,
        le=1,
        description="0 = vacant, 1 = occupied.",
    )

class SensorExitRequest(BaseModel):
    """Request schema for a sensor-driven vehicle exit (spot vacated).

    Attributes:
        lot_id: The ID of the parking lot the sensor belongs to.
        spot_id: The ID of the spot whose sensor flipped occupied -> vacant.
    """

    lot_id: int = Field(..., description="ID of the parking lot", examples=[1])
    spot_id: int = Field(..., description="ID of the vacated spot", examples=[5])


class CardCreateRequest(BaseModel):
    bank: str
    num: str
    icon: str
    
class CardDeleteRequest(BaseModel):
    card_numbers: list[str]        

class PlateRecognitionRequest(BaseModel):
    """Request schema for YOLO license plate recognition at the entrance gate.

    Attributes:
        lot_id: The ID of the parking lot where the camera is installed.
        plate_number: License plate string recognized by the YOLO camera.
            Max 20 characters, matching the DDL VARCHAR(20) constraint.
    """

    lot_id: int = Field(
        ...,
        description="ID of the parking lot",
        examples=[1],
    )
    plate_number: str = Field(
        ...,
        min_length=1,
        max_length=20,
        examples=["12가 3456"],
    )

    @field_validator("plate_number")
    @classmethod
    def sanitize_plate(cls, v: str) -> str:
        """Strip whitespaces and validate Korean license plate format.

        Pattern matches: 2~3 digits + one Korean char + 4 digits.

        Args:
            v: Raw plate_number input.

        Returns:
            str: Validated and whitespace-stripped plate string.

        Raises:
            ValueError: If the plate format is invalid.
        """
        # Remove all whitespace
        v = re.sub(r"\s+", "", v)
        
        # Validate Korean license plate pattern
        if not re.match(r"^\d{2,3}[가-힣]\d{4}$", v):
            raise ValueError(
                f"Invalid Korean license plate format: '{v}'. "
                "Expected format: 2-3 digits + one Korean character + 4 digits."
            )
        return v


class TransactionResponse(BaseModel):
    """Ledger row returned for a single completed payment.

    Attributes:
        transaction_id: PK of the PaymentTransactions row.
        session_id: FK linking back to the associated ParkingSession.
        plate_number: License plate on the session (may be None for legacy rows).
        amount: Fee charged in KRW.
        payment_status: Payment lifecycle state (e.g. 'completed').
        paid_at: Timestamp the payment was recorded.
        entry_time: When the vehicle entered the lot.
        exit_time: When the vehicle exited (None if session still open).
        lot_name: Name of the parking lot, resolved via
            ParkingSession -> ParkingSpot -> ParkingLot.
    """

    transaction_id: int
    session_id: int
    plate_number: str | None
    amount: int
    payment_status: str
    paid_at: datetime | None
    entry_time: datetime
    exit_time: datetime | None
    lot_name: str

    model_config = {"from_attributes": True}


class DashboardMetrics(BaseModel):
    """Real-time lot-wide occupancy and revenue snapshot.

    Attributes:
        total_spots: Total number of parking spots across all lots.
        occupied_spots: Spots with an active session right now.
        available_spots: Spots currently free (total − occupied).
        total_revenue: Sum of all completed PaymentTransaction amounts in KRW.
    """

    total_spots: int
    occupied_spots: int
    available_spots: int
    total_revenue: int

class VehicleCreateRequest(BaseModel):
    num: str
    desc: str = "Registered vehicle"

class VehicleDeleteRequest(BaseModel):
    plate_numbers: list[str]