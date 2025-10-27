from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, EmailStr
from enum import Enum

class UserRole(str, Enum):
    USER = "user"
    ADMIN = "admin"

class SubscriptionPlan(str, Enum):
    PREMIUM = "premium"

class SubscriptionStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

class PaymentProvider(str, Enum):
    STRIPE = "stripe"

# User models
class UserBase(BaseModel):
    email: EmailStr
    name: str
    role: UserRole = UserRole.USER

class UserCreate(UserBase):
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserUpdate(BaseModel):
    name: str

class UserPasswordUpdate(BaseModel):
    current_password: str
    new_password: str

class User(UserBase):
    id: str
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

class UserInDB(User):
    hashed_password: str

# Subscription models
class SubscriptionPlanInfo(BaseModel):
    name: SubscriptionPlan
    price: float
    features: List[str]
    duration_days: int

class SubscriptionCreate(BaseModel):
    plan: SubscriptionPlan
    payment_provider: PaymentProvider = PaymentProvider.STRIPE
    payment_token: str

class SubscriptionCreateWithUser(SubscriptionCreate):
    """Subscription creation with user registration data"""
    name: str
    email: str
    password: str

class SubscriptionCreateForUser(BaseModel):
    """Subscription creation for existing authenticated user"""
    plan: SubscriptionPlan
    payment_provider: PaymentProvider = PaymentProvider.STRIPE
    payment_token: str

class Subscription(BaseModel):
    id: str
    user_id: str
    plan: SubscriptionPlan
    status: SubscriptionStatus
    payment_provider: PaymentProvider
    stripe_subscription_id: Optional[str] = None
    starts_at: datetime
    ends_at: datetime
    created_at: datetime
    updated_at: datetime

# Token models
class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

# Payment models
class PaymentResponse(BaseModel):
    success: bool
    subscription_id: Optional[str] = None
    message: str
    payment_url: Optional[str] = None  # For payment redirects

# API Response models
class UserResponse(BaseModel):
    user: User
    subscription: Optional[Subscription] = None

class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    user: User
    subscription: Optional[Subscription] = None 

# Favorite models
class CreateFavoriteRequest(BaseModel):
    listing_id: str
    
class Favorite(BaseModel):
    user_id: str
    listing_id: str
    created_at: datetime

class DeleteFavorite(BaseModel):
    user_id: str
    listing_id: str

class GetFavorites(BaseModel):
    favorites: List[str]