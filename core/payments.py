from datetime import datetime, timedelta
from typing import Any, Optional
import inspect
import stripe
import traceback
from fastapi import HTTPException, status
from bson import ObjectId
from pymongo.errors import ServerSelectionTimeoutError
from pymongo.errors import PyMongoError

from .models import (   
    SubscriptionCreateWithUser, SubscriptionCreate, PaymentResponse, 
    Subscription, SubscriptionStatus, User, UserCreate
)
from core.config import settings
from .auth import get_password_hash, get_user_by_email
from dotenv import load_dotenv
import os
load_dotenv()
from .database import user_db

# Initialize Stripe
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
# Single subscription plan price
PLAN_PRICE = 20.00


async def process_stripe_subscription_with_user(
    subscription_data: SubscriptionCreateWithUser, 
    plan_price: float
) -> PaymentResponse:
    """Process Stripe subscription with new user creation"""
    try:
        # Create Stripe customer
        customer = stripe.Customer.create(
            email=subscription_data.email,
            name=subscription_data.name
        )
        
        # Attach payment method to customer
        payment_method = stripe.PaymentMethod.attach(
            subscription_data.payment_token,
            customer=customer.id
        )
        
        # Set as default payment method
        stripe.Customer.modify(
            customer.id,
            invoice_settings={"default_payment_method": payment_method.id}
        )
        
        # Get existing product by ID from config
        try:
            # Use product ID from config
            product_id = settings.STRIPE_PRODUCT_ID
            if not product_id:
                raise Exception("STRIPE_PRODUCT_ID not configured")
            
            # Verify the product exists in Stripe
            product = stripe.Product.retrieve(product_id)
            if not product:
                raise Exception(f"Product with ID {product_id} not found in Stripe")
                
        except Exception as e:
            raise Exception(f"Failed to get Stripe product: {str(e)}")

        # Create subscription with product ID
        subscription_params = {
            "customer": customer.id,
            "payment_behavior": "error_if_incomplete",
            "payment_settings": {
                "save_default_payment_method": "on_subscription"
            },
            "expand": ["latest_invoice.payment_intent"],
            "items": [{
                "price_data": {
                    "currency": "usd",
                    "product": product_id,
                    "unit_amount": int(plan_price * 100),
                    "recurring": {"interval": "month"}
                }
            }]
        }
        
        stripe_subscription = stripe.Subscription.create(**subscription_params)
        
        # Create user account
        hashed_password = get_password_hash(subscription_data.password)
        user_doc = {
            "email": subscription_data.email,
            "name": subscription_data.name,
            "hashed_password": hashed_password,
            "role": "user",
            "is_active": True,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        users_collection = user_db["users"]
        user_result = users_collection.insert_one(user_doc)
        user_id = str(user_result.inserted_id)
        
        # Create subscription record
        subscription_doc = {
            "user_id": user_id,
            "plan": "premium",
            "status": "active",
            "payment_provider": "stripe",
            "stripe_subscription_id": stripe_subscription.id,
            "starts_at": datetime.utcnow(),
            "ends_at": datetime.utcnow() + timedelta(days=30),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        subscriptions_collection = user_db["subscriptions"]
        subscription_result = subscriptions_collection.insert_one(subscription_doc)
        
        return PaymentResponse(
            success=True,
            subscription_id=str(subscription_result.inserted_id),
            message="Account created and subscription activated successfully!"
        )
        
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe error: {str(e)}"
        )


async def process_stripe_renewal(
    subscription_data: SubscriptionCreate, 
    user: User,
    plan_price: float
) -> PaymentResponse:
    """Process Stripe subscription renewal"""
    try:
        # Get or create Stripe customer
        customers = stripe.Customer.list(email=user.email, limit=1)
        
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.name
            )
        
        # Attach payment method to customer
        payment_method = stripe.PaymentMethod.attach(
            subscription_data.payment_token,
            customer=customer.id
        )
        
        # Set as default payment method
        stripe.Customer.modify(
            customer.id,
            invoice_settings={"default_payment_method": payment_method.id}
        )
        
        # Get existing product by ID from config
        try:
            # Use product ID from config
            product_id = settings.STRIPE_PRODUCT_ID
            if not product_id:
                raise Exception("STRIPE_PRODUCT_ID not configured")
            
            # Verify the product exists in Stripe
            product = stripe.Product.retrieve(product_id)
            if not product:
                raise Exception(f"Product with ID {product_id} not found in Stripe")
                
        except Exception as e:
            raise Exception(f"Failed to get Stripe product: {str(e)}")

        # Create new subscription with product ID
        subscription_params = {
            "customer": customer.id,
            "payment_behavior": "error_if_incomplete",
            "payment_settings": {
                "save_default_payment_method": "on_subscription"
            },
            "expand": ["latest_invoice.payment_intent"],
            "items": [{
                "price_data": {
                    "currency": "usd",
                    "product": product_id,
                    "unit_amount": int(plan_price * 100),
                    "recurring": {"interval": "month"}
                }
            }]
        }
        
        stripe_subscription = stripe.Subscription.create(**subscription_params)
        
        # Create new subscription record
        subscription_doc = {
            "user_id": user.id,
            "plan": "premium",
            "status": "active",
            "payment_provider": "stripe",
            "stripe_subscription_id": stripe_subscription.id,
            "starts_at": datetime.utcnow(),
            "ends_at": datetime.utcnow() + timedelta(days=30),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        subscriptions_collection = user_db["subscriptions"]
        subscription_result = subscriptions_collection.insert_one(subscription_doc)
        
        return PaymentResponse(
            success=True,
            subscription_id=str(subscription_result.inserted_id),
            message="Subscription renewed successfully!"
        )
        
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe error: {str(e)}"
        )


async def reactivate_cancelled_subscription(subscription_doc: dict, user: User) -> PaymentResponse:
    """Reactivate a cancelled subscription that's still within the active period"""
    try:
        # Update payment method if provided
        stripe_subscription_id = subscription_doc.get("stripe_subscription_id")
        if stripe_subscription_id:
            # Remove cancel_at_period_end flag to reactivate the subscription
            stripe.Subscription.modify(
                stripe_subscription_id,
                cancel_at_period_end=False
            )
        
        # Update subscription status back to active
        subscriptions_collection = user_db["subscriptions"]
        subscriptions_collection.update_one(
            {"_id": subscription_doc["_id"]},
            {"$set": {
                "status": "active",
                "updated_at": datetime.utcnow()
            }}
        )
        
        return PaymentResponse(
            success=True,
            subscription_id=str(subscription_doc["_id"]),
            message="Subscription reactivated successfully! Your access will continue beyond the current period."
        )
        
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe error: {str(e)}"
        )

async def process_stripe_subscription_for_user(
    subscription_data: SubscriptionCreate, 
    user: User,
    plan_price: float
) -> PaymentResponse:
    """Process Stripe subscription for existing authenticated user"""
    try:
        # Get or create Stripe customer
        customers = stripe.Customer.list(email=user.email, limit=1)
        
        if customers.data:
            customer = customers.data[0]
        else:
            customer = stripe.Customer.create(
                email=user.email,
                name=user.name
            )
        
        # Attach payment method to customer
        payment_method = stripe.PaymentMethod.attach(
            subscription_data.payment_token,
            customer=customer.id
        )
        
        # Set as default payment method
        stripe.Customer.modify(
            customer.id,
            invoice_settings={"default_payment_method": payment_method.id}
        )
        
        # Get existing product by ID from config
        try:
            # Use product ID from config
            product_id = settings.STRIPE_PRODUCT_ID
            if not product_id:
                raise Exception("STRIPE_PRODUCT_ID not configured")
            
            # Verify the product exists in Stripe
            product = stripe.Product.retrieve(product_id)
            if not product:
                raise Exception(f"Product with ID {product_id} not found in Stripe")
                
        except Exception as e:
            raise Exception(f"Failed to get Stripe product: {str(e)}")

        # Create subscription with product ID
        subscription_params = {
            "customer": customer.id,
            "payment_behavior": "error_if_incomplete",
            "payment_settings": {
                "save_default_payment_method": "on_subscription"
            },
            "expand": ["latest_invoice.payment_intent"],
            "items": [{
                "price_data": {
                    "currency": "usd",
                    "product": product_id,
                    "unit_amount": int(plan_price * 100),
                    "recurring": {"interval": "month"}
                }
            }]
        }
        
        stripe_subscription = stripe.Subscription.create(**subscription_params)
        
        # Create subscription record
        subscription_doc = {
            "user_id": user.id,
            "plan": "premium",
            "status": "active",
            "payment_provider": "stripe",
            "stripe_subscription_id": stripe_subscription.id,
            "starts_at": datetime.utcnow(),
            "ends_at": datetime.utcnow() + timedelta(days=30),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        print(subscription_doc)
        subscriptions_collection = user_db["subscriptions"]
        subscription_result = subscriptions_collection.insert_one(subscription_doc)
        
        return PaymentResponse(
            success=True,
            subscription_id=str(subscription_result.inserted_id),
            message="Subscription activated successfully!"
        )
        
    except stripe.error.StripeError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe error: {str(e)}"
        )


# Webhook handlers

async def handle_subscription_payment_succeeded(invoice):
    """Handle successful subscription payment"""
    subscription_id = invoice.get('subscription')

    # If subscription id is not present on the invoice (some invoice events
    # may omit it), try a few fallbacks to find the subscription:
    # 1. Look through invoice['lines'] for a subscription reference
    # 2. Use invoice['customer'] to list active subscriptions
    # 3. Retrieve the invoice from Stripe to inspect its fields
    if not subscription_id:
        # Try lines data
        lines = invoice.get('lines') or {}
        try:
            # lines may be a dict with 'data' list
            for item in (lines.get('data') or []):
                # line items may include a price->recurring or plan->id, or a subscription field
                if 'subscription' in item:
                    subscription_id = item.get('subscription')
                    break
                # sometimes price.product/plan may be present but not subscription id
        except Exception:
            pass

    if not subscription_id:
        # Try to use the customer to find an active subscription
        customer_id = invoice.get('customer')
        if customer_id:
            try:
                subs = stripe.Subscription.list(customer=customer_id, limit=3)
                # pick the first active or latest subscription
                if subs and getattr(subs, 'data', None):
                    for s in subs.data:
                        status = getattr(s, 'status', None) or s.get('status')
                        if status == 'active':
                            subscription_id = getattr(s, 'id', None) or s.get('id')
                            break
                    # fallback to first subscription id
                    if not subscription_id and subs.data:
                        subscription_id = getattr(subs.data[0], 'id', None) or subs.data[0].get('id')
            except Exception:
                subscription_id = None

    if not subscription_id:
        # Last resort: try retrieving the invoice from Stripe (in case the
        # webhook payload was partial) and inspect its subscription field.
        invoice_id = invoice.get('id')
        if invoice_id:
            try:
                stripe_invoice = stripe.Invoice.retrieve(invoice_id)
                subscription_id = getattr(stripe_invoice, 'subscription', None) or stripe_invoice.get('subscription')
            except Exception:
                subscription_id = None

    if not subscription_id:
        # Can't determine subscription id — we cannot proceed meaningfully
        # (but do not raise); simply return so webhook handling continues.
        return
    
    # Update subscription status in database
    subscriptions_collection = user_db["subscriptions"]
    subscription_doc = subscriptions_collection.find_one({
        "stripe_subscription_id": subscription_id
    })
    
    if subscription_doc:
        # Extend subscription period
        new_end_date = datetime.utcnow() + timedelta(days=30)
        subscriptions_collection.update_one(
            {"_id": subscription_doc["_id"]},
            {"$set": {
                "status": "active",
                "ends_at": new_end_date,
                "updated_at": datetime.utcnow()
            }}
        )
    else:
        # No existing subscription record found — create one.
        # Try to map the Stripe subscription -> customer -> our user
        user_email = None
        try:
            # Attempt to fetch the subscription from Stripe to get the customer id
            stripe_sub = stripe.Subscription.retrieve(subscription_id)
            stripe_customer_id = getattr(stripe_sub, 'customer', None) or stripe_sub.get('customer')
        except Exception:
            stripe_customer_id = None

        if stripe_customer_id:
            # Try to find a user with this Stripe customer id stored
            users_collection = user_db['users']
            user_doc = users_collection.find_one({
                'stripe_customer_id': stripe_customer_id
            })
            if user_doc:
                user_email = user_doc.get('email', str(user_doc.get('_id')))  # Use email if available, fallback to _id
            
            if not user_email and stripe_customer_id:
                # If we still don't have user_email, try getting email from Stripe customer
                try:
                    stripe_customer = stripe.Customer.retrieve(stripe_customer_id)
                    user_email = stripe_customer.get('email')
                except Exception:
                    pass
                    
        print(f"User ID (email): {user_email}")
        # Build a subscription document similar to when created during signup/renewal
        subscription_doc_to_insert = {
            'user_email': user_email,
            'plan': 'premium',
            'status': 'active',
            'payment_provider': 'stripe',
            'stripe_subscription_id': subscription_id,
            'starts_at': datetime.utcnow(),
            'ends_at': datetime.utcnow() + timedelta(days=30),
            'created_at': datetime.utcnow(),
            'updated_at': datetime.utcnow()
        }

        subscriptions_collection.insert_one(subscription_doc_to_insert)


async def handle_subscription_payment_failed(invoice):
    """Handle failed subscription payment"""
    subscription_id = invoice.get('subscription')
    if not subscription_id:
        return
    
    # Update subscription status in database
    subscriptions_collection = user_db["subscriptions"]
    subscription_doc = subscriptions_collection.find_one({
        "stripe_subscription_id": subscription_id
    })
    
    if subscription_doc:
        subscriptions_collection.update_one(
            {"_id": subscription_doc["_id"]},
            {"$set": {
                "status": "inactive",
                "ends_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }}
        )


async def handle_subscription_cancelled(subscription):
    """Handle subscription cancellation"""
    subscription_id = subscription.get('id')
    if not subscription_id:
        return
    
    period_end = datetime.fromtimestamp(subscription['canceled_at'])
    
    # Update subscription status in database
    subscriptions_collection = user_db["subscriptions"]
    subscriptions_collection.update_one(
        {"stripe_subscription_id": subscription_id},
        {"$set": {
            "status": "cancelled",
            "ends_at": period_end,
            "updated_at": datetime.utcnow()
        }}
    )


async def handle_subscription_updated(subscription):
    """Handle subscription updates"""
    subscription_id = subscription.get('id')
    if not subscription_id:
        return
    
    # Update subscription details in database
    subscriptions_collection = user_db["subscriptions"]
    
    # Convert Stripe timestamp to datetime
    period_end = datetime.fromtimestamp(subscription.get('cancel_at'))
    if period_end:
        update_data = {
            "status": "cancelled",
            "ends_at": period_end,
            "updated_at": datetime.utcnow()
        }
    
    
    # Update status based on Stripe status
    if period_end:
        subscriptions_collection.update_one(
            {"stripe_subscription_id": subscription_id},
            {"$set": update_data}
        )