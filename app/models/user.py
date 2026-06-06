from sqlalchemy import Column, Integer, String, TIMESTAMP, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class User(Base):
    __tablename__ = "Users"

    user_id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), nullable=False, unique=True)
    phone_number = Column(String(20), nullable=False, unique=True)
    password_hash = Column(String(255), nullable=False)
    user_name = Column(String(50), nullable=False)
    role = Column(String(20), nullable=False, server_default=text("'user'"))
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))

    lots = relationship("ParkingLot", back_populates="operator")
    vehicles = relationship("Vehicle", back_populates="user", cascade="all, delete-orphan")
    sessions = relationship("ParkingSession", back_populates="user")
    reservations = relationship("Reservation", back_populates="user", cascade="all, delete-orphan")
