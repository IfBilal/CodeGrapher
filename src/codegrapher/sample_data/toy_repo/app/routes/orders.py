from fastapi import APIRouter

from app.db import db_session
from app.models.order import Order
from app.services.billing import charge_card

router = APIRouter()


@router.post("/orders")
def create_order(payload):
    order = Order(user_id=payload.user_id, total_cents=payload.total_cents, status="pending")
    charge_card(payload.total_cents)
    db_session.add(order)
    db_session.commit()
    return order


@router.delete("/orders/{id}")
def cancel_order(id):
    order = db_session.query(Order).get(id)
    db_session.delete(order)
    db_session.commit()
    return {"status": "cancelled"}
