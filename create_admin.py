#!/usr/bin/env python3
"""
Script to create an admin user that can bypass subscription requirements.
Run this script to create an admin user in the database.
"""

import asyncio
import sys
import os
from datetime import datetime
from bson import ObjectId

# Add the backend directory to the Python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.database import user_db
from core.auth import get_password_hash
from core.models import UserRole

async def create_admin_user(email: str, name: str, password: str):
    """Create an admin user in the database"""
    
    # Check if user already exists
    users_collection = user_db["users"]
    existing_user = users_collection.find_one({"email": email})
    
    if existing_user:
        print(f"User with email {email} already exists!")
        return False
    
    # Create admin user
    hashed_password = get_password_hash(password)
    admin_doc = {
        "email": email,
        "name": name,
        "role": UserRole.ADMIN,
        "hashed_password": hashed_password,
        "is_active": True,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow()
    }
    
    try:
        result = users_collection.insert_one(admin_doc)
        print(f"✅ Admin user created successfully!")
        print(f"   Email: {email}")
        print(f"   Name: {name}")
        print(f"   Role: {UserRole.ADMIN}")
        print(f"   User ID: {result.inserted_id}")
        print(f"\nYou can now login with this admin account and access all features without a subscription.")
        return True
    except Exception as e:
        print(f"❌ Error creating admin user: {e}")
        return False

async def main():
    """Main function to create admin user"""
    print("🔧 Admin User Creation Script")
    print("=" * 40)
    
    # Get admin user details
    email = input("Enter admin email: ").strip()
    name = input("Enter admin name: ").strip()
    password = input("Enter admin password: ").strip()
    
    if not email or not name or not password:
        print("❌ All fields are required!")
        return
    
    # Create the admin user
    success = await create_admin_user(email, name, password)
    
    if success:
        print(f"\n🎉 Admin user '{name}' created successfully!")
        print("You can now login to the application and access all features without requiring a subscription.")

if __name__ == "__main__":
    asyncio.run(main()) 