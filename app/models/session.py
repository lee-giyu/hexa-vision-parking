from sqlalchemy import Column, ForeignKey, Integer, String, TIMESTAMP, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class ParkingSession(Base):
    __tablename__ = "ParkingSessions"

    session_id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(
        Integer,
        ForeignKey("Users.user_id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    vehicle_id = Column(
        Integer,
        ForeignKey("Vehicles.vehicle_id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
    )
    spot_id = Column(
        Integer,
        ForeignKey("ParkingSpots.spot_id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    plate_number = Column(String(20), nullable=True)
    entry_time = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    exit_time = Column(TIMESTAMP, nullable=True)
    parking_fee = Column(Integer, nullable=True)
    session_status = Column(String(10), nullable=False, server_default=text("'active'"))

    user = relationship("User", back_populates="sessions")
    vehicle = relationship("Vehicle", back_populates="sessions")
    spot = relationship("ParkingSpot", back_populates="sessions")
    transactions = relationship("PaymentTransaction", back_populates="session", cascade="all, delete-orphan")


class PaymentTransaction(Base):
    __tablename__ = "PaymentTransactions"

    transaction_id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(
        Integer,
        ForeignKey("ParkingSessions.session_id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    amount = Column(Integer, nullable=False)
    payment_method = Column(String(30), nullable=True)
    payment_status = Column(String(20), nullable=False, server_default=text("'pending'"))
    paid_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    session = relationship("ParkingSession", back_populates="transactions")
