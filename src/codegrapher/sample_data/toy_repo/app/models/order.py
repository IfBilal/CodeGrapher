from sqlalchemy import Column, ForeignKey, Integer, String

from app.db import Base


class Order(Base):
    __tablename__ = "order"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"))
    total_cents = Column(Integer)
    status = Column(String)
