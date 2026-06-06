from sqlalchemy import Column, ForeignKey, Integer, String, Text, Time, Boolean, TIMESTAMP, text
from sqlalchemy.orm import relationship

from app.core.database import Base


class ParkingLot(Base):
    __tablename__ = "ParkingLots"

    lot_id = Column(Integer, primary_key=True, autoincrement=True)
    operator_id = Column(
        Integer,
        ForeignKey("Users.user_id", ondelete="RESTRICT", onupdate="CASCADE"),
        nullable=False,
    )
    lot_name = Column(String(100), nullable=False)
    address = Column(String(255), nullable=False)
    total_spots = Column(Integer, nullable=False, server_default=text("0"))
    operating_start = Column(Time, nullable=True)
    operating_end = Column(Time, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("1"))
    created_at = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    image_url = Column(String(500), nullable=True)
    description = Column(Text, nullable=True)

    operator = relationship("User", back_populates="lots")
    spots = relationship("ParkingSpot", back_populates="lot", cascade="all, delete-orphan")
    pricing_policies = relationship("PricingPolicy", back_populates="lot", cascade="all, delete-orphan")


class PricingPolicy(Base):
    __tablename__ = "PricingPolicies"

    policy_id = Column(Integer, primary_key=True, autoincrement=True)
    lot_id = Column(
        Integer,
        ForeignKey("ParkingLots.lot_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
    )
    base_rate_per_hour = Column(Integer, nullable=False)
    free_time_minutes = Column(Integer, nullable=False, server_default=text("0"))
    applied_from = Column(TIMESTAMP, nullable=False, server_default=text("CURRENT_TIMESTAMP"))
    applied_until = Column(TIMESTAMP, nullable=True)

    lot = relationship("ParkingLot", back_populates="pricing_policies")
