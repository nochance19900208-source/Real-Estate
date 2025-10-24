from fastapi import APIRouter, Depends, HTTPException
import stripe, os
from core.auth import get_current_active_user

router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

@router.get("/status")
async def get_subscription_status(current_user = Depends(get_current_active_user)):

    user = current_user

    customer_id = getattr(user, "stripe_customer_id", None)
    if not customer_id:
        return {"active": False, "reason": "no_customer"}

    try:
        subs = stripe.Subscription.list(
            customer=customer_id,
            status="all",
            limit=10,
            expand=["data.default_payment_method"]
        )

        if not subs.data:
            return {"active": False, "reason": "no_subscriptions", "customer_id": customer_id}

        latest = max(subs.data, key=lambda s: s["current_period_end"] or 0)

        status = latest["status"]
        cancel_at_period_end = bool(latest.get("cancel_at_period_end"))
        current_period_end = latest.get("current_period_end")

        is_active = (
            status in ALLOWED_ACTIVE_STATUSES
            or (TREAT_PAST_DUE_AS_ACTIVE and status in GRACE_STATUSES)
        )

        return {
            "active": bool(is_active),
            "status": status,
            "cancel_at_period_end": cancel_at_period_end,
            "current_period_end": current_period_end,
            "subscription_id": latest["id"],
            "customer_id": customer_id,
        }

    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))