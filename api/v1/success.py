from fastapi import FastAPI, HTTPException, Depends
import os, stripe, json
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from core.config import settings
import stripe
from typing import Optional
from dotenv import load_dotenv
load_dotenv()
app = FastAPI()


STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
stripe_price_id = os.getenv("STRIPE_PRODUCT_ID")
# Configure CORS to allow requests from your frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Update with your frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set your Stripe API key

# Models for request validation
class SubscriptionRequest(BaseModel):
    paymentMethodId: str
    amount: float
    customerId: Optional[str] = None
    priceId: Optional[str] = None

@app.post("/create-subscription")
async def create_subscription(request: SubscriptionRequest):
    try:
        # If no priceId is provided, use a default price or create one based on the amount
        price_id = request.priceId
        if not price_id:
            # You might want to look up an existing price based on the amount
            # or create a new price if needed
            price_id = stripe_price_id  # Replace with your price ID logic
        
        # If a customerId is provided, use it; otherwise create a new customer
        if request.customerId:
            customer = stripe.Customer.retrieve(request.customerId)
            # Attach the payment method to the customer
            stripe.PaymentMethod.attach(
                request.paymentMethodId,
                customer=customer.id
            )
        else:
            # Create a new customer with the payment method
            customer = stripe.Customer.create(
                payment_method=request.paymentMethodId,
                email="amazon@example.com",  # In a real app, get this from authenticated user
                invoice_settings={
                    'default_payment_method': request.paymentMethodId,
                },
            )
        
        # Set the customer's default payment method
        stripe.Customer.modify(
            customer.id,
            invoice_settings={
                'default_payment_method': request.paymentMethodId,
            }
        )
        
        # Create the subscription
        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[
                {"price": stripe_price_id},
            ],
            payment_behavior="default_incomplete",
            expand=["latest_invoice.payment_intent"],
        )
        
        latest_invoice = subscription.latest_invoice
        payment_intent = latest_invoice.payment_intent
        
        # Return different responses based on the payment intent status
        if payment_intent.status == "requires_action":
            return {
                "requiresAction": True,
                "clientSecret": payment_intent.client_secret,
                "subscriptionId": subscription.id
            }
        elif payment_intent.status == "succeeded":
            return {
                "success": True,
                "subscriptionId": subscription.id
            }
        else:
            return {
                "error": "Payment failed",
                "paymentIntentStatus": payment_intent.status
            }
            
    except stripe.error.StripeError as e:
        # Handle Stripe-specific errors
        error_message = e.user_message if hasattr(e, 'user_message') else str(e)
        raise HTTPException(status_code=400, detail=error_message)
    
    except Exception as e:
        # Handle other errors
        raise HTTPException(status_code=500, detail=str(e))

# Optional: Add an endpoint to fetch subscription details
@app.get("/api/subscription/{subscription_id}")
async def get_subscription(subscription_id: str):
    try:
        subscription = stripe.Subscription.retrieve(
            subscription_id,
            expand=["latest_invoice.payment_intent", "customer"]
        )
        return {
            "status": subscription.status,
            "currentPeriodEnd": subscription.current_period_end,
            "customerId": subscription.customer.id if subscription.customer else None
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

# Optional: Add an endpoint to cancel subscription
@app.delete("/api/subscription/{subscription_id}")
async def cancel_subscription(subscription_id: str):
    try:
        cancelled_subscription = stripe.Subscription.delete(subscription_id)
        return {
            "success": True,
            "status": cancelled_subscription.status
        }
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)