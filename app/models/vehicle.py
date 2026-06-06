from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP
from sqlalchemy.orm import relationship

from app.core.database import Base


class Vehicle(Base):
    __tablename__ = "Vehicles"

    vehicle_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("Users.user_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    plate_number = Column(String(20), nullable=False, unique=True)
    pass_type = Column(String(30), nullable=True)
    pass_expiry = Column(TIMESTAMP, nullable=True)

    user = relationship("User", back_populates="vehicles")
    sessions = relationship("ParkingSession", back_populates="vehicle")
    reservations = relationship("Reservation", back_populates="vehicle")
