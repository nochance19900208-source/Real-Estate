from datetime import datetime, timedelta
from typing import Optional
import jwt
import hashlib
import secrets
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from core.config import settings
from core.models import TokenData, User, UserInDB, UserRole
from core.database import user_db

# Import JWT exceptions with fallback for different PyJWT versions
try:
    from jwt.exceptions import DecodeError, ExpiredSignatureError
except ImportError:
    DecodeError = Exception
    ExpiredSignatureError = Exception

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="v1/auth/login")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash with robust bcrypt 72-byte limit handling"""
    try:
        # Check if it's our custom SHA-256 format
        if hashed_password.startswith("sha256:"):
            parts = hashed_password.split(":")
            if len(parts) == 3:
                salt = parts[1]
                stored_hash = parts[2]
                combined = plain_password + salt
                computed_hash = hashlib.sha256(combined.encode('utf-8')).hexdigest()
                return computed_hash == stored_hash
            return False
        
        # Handle bcrypt hashes
        password_bytes = len(plain_password.encode('utf-8'))
        
        if password_bytes > 72:
            # For long passwords, try SHA-256 + bcrypt first
            try:
                password_hash = hashlib.sha256(plain_password.encode('utf-8')).hexdigest()
                return pwd_context.verify(password_hash, hashed_password)
            except Exception:
                return False
        
        # For short passwords, use bcrypt normally
        try:
            return pwd_context.verify(plain_password, hashed_password)
        except Exception:
            return False
            
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    """Generate password hash with robust bcrypt 72-byte limit handling"""
    password_bytes = len(password.encode('utf-8'))
    
    # For passwords longer than 72 bytes, use a different approach
    if password_bytes > 72:
        # Use SHA-256 + bcrypt for long passwords
        try:
            password_hash = hashlib.sha256(password.encode('utf-8')).hexdigest()
            return pwd_context.hash(password_hash)
        except Exception:
            # If SHA-256 + bcrypt fails, use SHA-256 + salt as fallback
            salt = secrets.token_hex(16)
            combined = password + salt
            return f"sha256:{salt}:{hashlib.sha256(combined.encode('utf-8')).hexdigest()}"
    
    # For short passwords, use bcrypt normally
    try:
        return pwd_context.hash(password)
    except Exception as e:
        # If bcrypt fails for short passwords, use SHA-256 + salt
        salt = secrets.token_hex(16)
        combined = password + salt
        return f"sha256:{salt}:{hashlib.sha256(combined.encode('utf-8')).hexdigest()}"

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Create JWT access token"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def verify_token(token: str, credentials_exception):
    """Verify JWT token and extract user data"""
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except (DecodeError, ExpiredSignatureError, Exception) as e:
        # Catch any JWT-related exception
        raise credentials_exception
    return token_data

async def get_user_by_email(email: str) -> Optional[UserInDB]:
    """Get user from database by email"""
    users_collection = user_db["users"]
    user_doc = users_collection.find_one({"email": email})
    if user_doc:
        user_doc["id"] = str(user_doc["_id"])
        del user_doc["_id"]
        return UserInDB(**user_doc)
    return None

async def authenticate_user(email: str, password: str) -> Optional[UserInDB]:
    """Authenticate user with email and password"""
    user = await get_user_by_email(email)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user

async def get_current_user(token: str = Depends(oauth2_scheme)) -> User:
    """Get current authenticated user"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    token_data = verify_token(token, credentials_exception)
    user = await get_user_by_email(email=token_data.email)
    if user is None:
        raise credentials_exception
    
    # Convert UserInDB to User (remove password hash)
    return User(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        updated_at=user.updated_at
    )

async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """Get current active user"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

async def get_current_subscribed_user(current_user: User = Depends(get_current_active_user)) -> User:
    """Get current user with active subscription or admin user"""
    # Admin users can bypass subscription requirement
    if current_user.role == UserRole.ADMIN:
        return current_user
    
    # Check if user has valid subscription (active or cancelled but not yet expired)
    subscriptions_collection = user_db["subscriptions"]
    subscription_doc = subscriptions_collection.find_one({
        "user_id": current_user.id,
        "$or": [
            {"status": "active", "ends_at": {"$gt": datetime.utcnow()}},
            {"status": "cancelled", "ends_at": {"$gt": datetime.utcnow()}}  # Allow cancelled subs until period end
        ]
    })
    
    if not subscription_doc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required to access this resource"
        )
    
    return current_user
