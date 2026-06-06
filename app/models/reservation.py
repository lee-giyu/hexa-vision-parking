from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class Reservation(Base):
    __tablename__ = "Reservations"

    reservation_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("Users.user_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    spot_id = Column(
        Integer,
        ForeignKey("ParkingSpots.spot_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    vehicle_id = Column(
        Integer,
        ForeignKey("Vehicles.vehicle_id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    reserved_from = Column(TIMESTAMP, nullable=False)
    reserved_until = Column(TIMESTAMP, nullable=False)
    status = Column(String(20), nullable=False, server_default=text("'pending'"))
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    user = relationship("User", back_populates="reservations")
    spot = relationship("ParkingSpot", back_populates="reservations")
    vehicle = relationship("Vehicle", back_populates="reservations")
