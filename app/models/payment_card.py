from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from datetime import datetime

from app.core.database import Base 

class PaymentCard(Base):
    __tablename__ = "payment_cards"

    card_id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("Users.user_id", ondelete="CASCADE"), nullable=False)
    bank_name = Column(String(50), nullable=False)
    card_number = Column(String(50), nullable=False)
    icon_class = Column(String(50))
    created_at = Column(DateTime, default=datetime.utcnow)