import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlmodel import select

from database import SessionDep
from models import Payment, PaymentRead, User, WebhookEvent
from services.access_token import get_current_user
from services.payment_lifecycle import (
    apply_refund_event,
    apply_transaction_result,
    initialize_payment,
    verify_payment,
)
from services.paystack import paystack_service
from models import Order


router = APIRouter(prefix="/payment", tags=["Payments"])


def _get_owned_payment(
    session: SessionDep,
    *,
    payment_id: UUID,
    user_id: UUID,
) -> Payment:
    payment = session.exec(
        select(Payment)
        .join(Order, Order.id == Payment.order_id)
        .where(Payment.id == payment_id, Order.user_id == user_id)
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    return payment


@router.post("/order/{order_id}/initialize", response_model=PaymentRead)
def initialize_order_payment(
    order_id: UUID,
    session: SessionDep,
    current_user: User = Depends(get_current_user),
):
    order = session.exec(
        select(Order).where(
            Order.id == order_id,
            Order.user_id == current_user.id,
        )
    ).first()
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    payment = initialize_payment(session, order=order, user=current_user)
    session.commit()
    session.refresh(payment)
    return payment


@router.post("/{payment_id}/verify", response_model=PaymentRead)
def verify_order_payment(
    payment_id: UUID,
    session: SessionDep,
    current_user: User = Depends(get_current_user),
):
    payment = _get_owned_payment(
        session,
        payment_id=payment_id,
        user_id=current_user.id,
    )
    verify_payment(session, payment)
    session.commit()
    session.refresh(payment)
    return payment


@router.post("/reference/{reference}/verify", response_model=PaymentRead)
def verify_order_payment_by_reference(
    reference: str,
    session: SessionDep,
    current_user: User = Depends(get_current_user),
):
    payment = session.exec(
        select(Payment)
        .join(Order, Order.id == Payment.order_id)
        .where(
            Payment.reference == reference,
            Order.user_id == current_user.id,
        )
    ).first()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    verify_payment(session, payment)
    session.commit()
    session.refresh(payment)
    return payment


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def paystack_webhook(
    request: Request,
    session: SessionDep,
    x_paystack_signature: str | None = Header(default=None),
):
    raw_body = await request.body()
    if not paystack_service.verify_webhook_signature(
        raw_body,
        x_paystack_signature,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Paystack signature",
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook payload") from exc

    fingerprint = hashlib.sha256(raw_body).hexdigest()
    if session.exec(
        select(WebhookEvent).where(WebhookEvent.fingerprint == fingerprint)
    ).first():
        return {"status": "ok"}

    event_type = payload.get("event", "")
    data = payload.get("data") or {}
    if event_type in {"charge.success", "charge.failed"}:
        reference = data.get("reference")
        payment = session.exec(
            select(Payment).where(Payment.reference == reference)
        ).first()
        if not payment:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Payment is not ready for webhook processing",
            )
        apply_transaction_result(session, payment=payment, data=data)
    elif event_type in {
        "refund.pending",
        "refund.processing",
        "refund.needs-attention",
        "refund.failed",
        "refund.processed",
    }:
        processed = apply_refund_event(
            session,
            event_type=event_type,
            data=data,
        )
        if not processed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Refund is not ready for webhook processing",
            )

    session.add(
        WebhookEvent(
            fingerprint=fingerprint,
            event_type=event_type or "unknown",
        )
    )
    session.commit()
    return {"status": "ok"}
