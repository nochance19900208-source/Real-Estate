from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from core.config import settings
import stripe
from typing import Optional
import os, stripe, json
from dotenv import load_dotenv
load_dotenv()



STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRODUCT_ID")

app = FastAPI()

# Configure CORS - make sure to include your frontend origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)



class SubscriptionRequest(BaseModel):
    paymentMethodId: str
    amount: float
    
# This is the endpoint your frontend is likely calling
@app.post("/create-checkout-session")  # Note: Changed from /api/create-subscription
async def create_checkout_session(request: SubscriptionRequest):
    try:
        # Find or create a price based on the amount
        # In a real app, you might look up an existing price or create one
        amount_in_cents = int(request.amount * 100)
        
        # For testing, we'll create a price on the fly
        # In production, you should create prices in the dashboard and reference them
        price = stripe.Price.create(
            unit_amount=amount_in_cents,
            currency="usd",
            recurring={"interval": "month"},
            product_data={
                "name": f"Monthly Subscription ${request.amount}"
            },
        )
        
        # Create a customer
        customer = stripe.Customer.create(
            payment_method=request.paymentMethodId,
            invoice_settings={
                'default_payment_method': request.paymentMethodId,
            },
        )
        
        # Create the subscription
        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[
                {"price": STRIPE_PRICE_ID},
            ],
            payment_behavior="default_incomplete",
            payment_settings={"save_default_payment_method": "on_subscription"},
            expand=["latest_invoice.payment_intent"],
        )
        
        latest_invoice = subscription.latest_invoice
        payment_intent = latest_invoice.payment_intent
        
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
                "error": f"Payment failed with status: {payment_intent.status}"
            }
            
    except stripe.error.StripeError as e:
        print(f"Stripe error: {str(e)}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# You may also want a health check endpoint
@app.get("/")
def read_root():
    return {"status": "API is running"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)