from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, TIMESTAMP, UniqueConstraint, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class ParkingSpot(Base):
    __tablename__ = "ParkingSpots"
    __table_args__ = (
        UniqueConstraint("lot_id", "floor_number", "section", "spot_number", name="uq_spot_location"),
    )

    spot_id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(
        Integer,
        ForeignKey("ParkingLots.lot_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    floor_number = Column(Integer, nullable=False, server_default=text("1"))
    floor_label = Column(String(20), nullable=False, server_default=text("'1F'"))
    section = Column(String(10), nullable=True)
    spot_number = Column(Integer, nullable=False)
    spot_type = Column(String(20), nullable=False, server_default=text("'general'"))

    is_occupied = Column(Boolean, nullable=False, server_default=text("0"))

    lot = relationship("ParkingLot", back_populates="spots")
    devices = relationship("SpotDevice", back_populates="spot", cascade="all, delete-orphan")
    sessions = relationship("ParkingSession", back_populates="spot")
    reservations = relationship("Reservation", back_populates="spot", cascade="all, delete-orphan")


class SpotDevice(Base):
    __tablename__ = "SpotDevices"

    device_id = Column(Integer, primary_key=True, autoincrement=True)
    spot_id = Column(
        Integer,
        ForeignKey("ParkingSpots.spot_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    device_id_hw = Column(String(50), nullable=False, unique=True)
    device_type = Column(String(30), nullable=False)
    device_role = Column(String(20), nullable=False)
    current_state = Column(Boolean, nullable=False, server_default=text("0"))
    last_updated = Column(
        TIMESTAMP,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
    )

    spot = relationship("ParkingSpot", back_populates="devices")
