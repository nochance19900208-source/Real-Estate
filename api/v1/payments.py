import os, stripe, json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from core.models import (
    SubscriptionCreateWithUser, SubscriptionCreate, PaymentResponse, 
    User
)
from motor.motor_asyncio import AsyncIOMotorClient
from core.auth import get_current_active_user, get_user_by_email
from core.config import settings
from core.database import user_db
from core.payments import (
    PLAN_PRICE,
    process_stripe_subscription_with_user,
    process_stripe_renewal,
    reactivate_cancelled_subscription,
    process_stripe_subscription_for_user,
    handle_subscription_payment_succeeded,
    handle_subscription_payment_failed,
    handle_subscription_cancelled,
    handle_subscription_updated
)
from dotenv import load_dotenv

client = AsyncIOMotorClient("mongodb://192.142.53.129:27017")
db = client["users"]                 # database
subscriptions = db["subscriptions"]


load_dotenv()

subs_router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])

#router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])
router = APIRouter()

# Initialize Stripe

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:3000")

class CreateCheckoutSessionBody(BaseModel):
    price_id: str
    customer_email: str | None = None

@router.get("/hello")
def hello():
    return {"message": "Hello World"}


STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
STRIPE_PRODUCT_ID = os.getenv("STRIPE_PRODUCT_ID")

@router.post("/webhook")
async def stripe_webhook(request: Request):
    """Handle Stripe webhook events"""
    payload = await request.body()
    sig_header = request.headers.get('stripe-signature')

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
        
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    # Handle the event
    if event['type'] == 'payment_intent.created': 
        print("**************succeeded***************")
        await handle_subscription_payment_succeeded(event['data']['object'])
    elif event['type'] == 'invoice.payment_failed':
        print("**************failed***************")
        await handle_subscription_payment_failed(event['data']['object'])
    elif event['type'] == 'customer.subscription.deleted':
        print("**************deleted***************")
        await handle_subscription_cancelled(event['data']['object'])
    elif event['type'] == 'customer.subscription.updated':
        print("**************updated***************")
        await handle_subscription_updated(event['data']['object'])
    
    return {"status": "success"}


@router.post("/confirm")
async def confirm_checkout(session_id: str, current_user=Depends(get_current_active_user)):
    session = stripe.checkout.Session.retrieve(session_id, expand=["subscription", "customer"])
    # persist to your user:
    await user_db.update_one(
        {"_id": current_user.id},
        {"$set": {
            "stripe_customer_id": session.customer,
            "subscription_status": session.subscription.status,  # 'active'
            "stripe_subscription_id": session.subscription.id
        }}
    )
    return {"ok": True}

@router.post("/create-checkout-session")
def create_checkout_session(body: CreateCheckoutSessionBody):
    try:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price": body.price_id, "quantity": 1}],
            success_url=f"{FRONTEND_URL}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{FRONTEND_URL}/cancel",
            customer_email=body.customer_email,
            allow_promotion_codes=True
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/checkout-session")
async def get_checkout_session(session_id: str):
    try:
        session = stripe.checkout.Session.retrieve(session_id, expand=["subscription", "customer"])
        return {
            "id": session.id,
            "status": session.status,                 # 'complete' when done
            "payment_status": session.payment_status, # 'paid' for successful first invoice
            "subscription_status": session.subscription.status if session.subscription else None,
            "customer_email": session.customer_details.email if session.customer_details else None,
            "subscription_id": session.subscription.id if session.subscription else None,
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

class CreatePaymentIntentBody(BaseModel):
    amount: int
    currency: str = "usd"

@router.post("/create-payment-intent")
def create_payment_intent(body: CreatePaymentIntentBody):
    try:
        intent = stripe.PaymentIntent.create(
            amount=body.amount,
            currency=body.currency,
            automatic_payment_methods={"enabled": True},
        )
        return {"clientSecret": intent.client_secret}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

        
@router.get("/config")
async def get_payment_config():
    return {
        "stripe": {
            "publishable_key": STRIPE_PUBLISHABLE_KEY
        }
    }

@router.post("/create-subscription", response_model=PaymentResponse)
async def create_subscription(subscription_data: SubscriptionCreateWithUser):
    """Create a new subscription along with user account"""
    
    # Check if user already exists
    existing_user = await get_user_by_email(subscription_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    try:
        if subscription_data.payment_provider.value == "stripe":
            return await process_stripe_subscription_with_user(subscription_data, PLAN_PRICE)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only Stripe payment provider is supported"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payment processing failed: {str(e)}"
        )


@router.post("/renew-subscription", response_model=PaymentResponse)
async def renew_subscription(
    subscription_data: SubscriptionCreate,
    current_user: User = Depends(get_current_active_user)
):
    """Renew an expired or cancelled subscription"""
    
    # Check if user has an existing subscription
    subscriptions_collection = user_db["subscriptions"]
    existing_subscription = subscriptions_collection.find_one({
        "user_id": current_user.id
    }, sort=[("created_at", -1)])  # Get most recent subscription
    
    # If user has a cancelled subscription that's still within the active period,
    # just reactivate it instead of creating a new one
    if existing_subscription:
        ends_at = existing_subscription.get("ends_at")
        if isinstance(ends_at, str):
            ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
        
        # Check if subscription is still within active period
        if ends_at > datetime.utcnow():
            if existing_subscription.get("status") == "cancelled":
                # Reactivate the cancelled subscription (no payment required)
                try:
                    return await reactivate_cancelled_subscription(existing_subscription, current_user)
                except Exception as e:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to reactivate subscription: {str(e)}"
                    )
            elif existing_subscription.get("status") == "active":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="You already have an active subscription"
                )
    
    # If no existing subscription or it's truly expired, create a new one
    try:
        if subscription_data.payment_provider.value == "stripe":
            return await process_stripe_renewal(subscription_data, current_user, PLAN_PRICE)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only Stripe payment provider is supported"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payment processing failed: {str(e)}"
        )


@router.post("/create-subscription-for-user", response_model=PaymentResponse)
async def create_subscription_for_user(
    subscription_data: SubscriptionCreate,
    current_user: User = Depends(get_current_active_user)
):
    """Create a new subscription for an existing authenticated user"""
    
    # Check if user already has an active subscription
    subscriptions_collection = user_db["subscriptions"]
    existing_subscription = subscriptions_collection.find_one({
        "user_id": current_user.id,
        "status": "active"
    })
    
    if existing_subscription:
        # Check if it's actually expired
        ends_at = existing_subscription.get("ends_at")
        if isinstance(ends_at, str):
            ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
        
        if ends_at > datetime.utcnow():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="You already have an active subscription"
            )
    
    try:
        if subscription_data.payment_provider.value == "stripe":
            return await process_stripe_subscription_for_user(subscription_data, current_user, PLAN_PRICE)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only Stripe payment provider is supported"
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Payment processing failed: {str(e)}"
        )


@router.get("/subscription")
async def get_user_subscription(current_user: User = Depends(get_current_active_user)):
    """Get current user's subscription information"""
    subscriptions_collection = user_db["subscriptions"]
    subscription_doc = subscriptions_collection.find_one({
        "user_id": current_user.id
    }, sort=[("created_at", -1)])  # Get most recent subscription
    
    if subscription_doc:
        subscription_doc["id"] = str(subscription_doc["_id"])
        del subscription_doc["_id"]
        return {"subscription": subscription_doc}
    
    return {"subscription": None}


@router.post("/cancel-subscription")
async def cancel_subscription(current_user: User = Depends(get_current_active_user)):
    """Cancel user's subscription"""
    subscriptions_collection = user_db["subscriptions"]
    subscription_doc = subscriptions_collection.find_one({
        "user_id": current_user.id,
        "status": "active"
    })
    
    if not subscription_doc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active subscription found"
        )
    
    try:
        message = "Subscription cancelled successfully"
        
        # Cancel with Stripe at period end (user keeps access until billing cycle ends)
        if subscription_doc.get("stripe_subscription_id"):
            try:
                # First, get the current subscription status from Stripe
                stripe_subscription = stripe.Subscription.retrieve(subscription_doc["stripe_subscription_id"])
                
                if stripe_subscription.status == "active":
                    # Only modify if subscription is still active
                    stripe.Subscription.modify(
                        subscription_doc["stripe_subscription_id"],
                        cancel_at_period_end=True
                    )
                    message = "Subscription will be cancelled at the end of your current billing period. You'll retain access until then."
                elif stripe_subscription.status in ["canceled", "cancelled"]:
                    # Already cancelled in Stripe
                    message = "Subscription is already cancelled"
                else:
                    # Handle other statuses (incomplete, past_due, etc.)
                    message = f"Subscription status is {stripe_subscription.status}. Marked as cancelled in our system."
                    
            except stripe.error.StripeError as stripe_error:
                # If Stripe call fails, still update our database
                print(f"Stripe error during cancellation: {stripe_error}")
                message = "Subscription cancelled in our system. Please contact support if you continue to be charged."
        
        # Update subscription status to indicate it's cancelled
        subscriptions_collection.update_one(
            {"_id": subscription_doc["_id"]},
            {"$set": {
                "status": "cancelled",
                "updated_at": datetime.utcnow()
            }}
        )
        
        return {"message": message}
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel subscription: {str(e)}"
        ) 


@router.post("/reactivate-subscription", response_model=PaymentResponse)
async def reactivate_subscription(current_user: User = Depends(get_current_active_user)):
    """Reactivate a cancelled subscription that's still within the active period"""
    
    # Check if user has a cancelled subscription that can be reactivated
    subscriptions_collection = user_db["subscriptions"]
    existing_subscription = subscriptions_collection.find_one({
        "user_id": current_user.id,
        "status": "cancelled"
    }, sort=[("created_at", -1)])  # Get most recent cancelled subscription
    
    if not existing_subscription:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No cancelled subscription found to reactivate"
        )
    
    # Check if subscription is still within active period
    ends_at = existing_subscription.get("ends_at")
    if isinstance(ends_at, str):
        ends_at = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
    
    if ends_at <= datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Subscription has expired and cannot be reactivated. Please create a new subscription."
        )
    
    try:
        return await reactivate_cancelled_subscription(existing_subscription, current_user)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to reactivate subscription: {str(e)}"
        )

    

@router.get("/plan")
async def get_subscription_plan():
    """Get subscription plan information"""
    return {
        "plan": {
            "name": "premium",
            "price": PLAN_PRICE,
            "features": [
                "Access to all property listings",
                "Detailed property information",
                "Contact information for properties",
                "Advanced search and filtering",
                "Monthly updates"
            ],
            "duration_days": 30
        }
    } 