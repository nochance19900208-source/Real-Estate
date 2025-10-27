import uuid
import datetime
import bson
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from core.database import listings_db as db, user_db
from core.config import settings
from core.models import User, Favorite, DeleteFavorite, GetFavorites, CreateFavoriteRequest
from core.auth import get_current_subscribed_user

router = APIRouter()

@router.post("/favorites", response_model=Favorite)
def create_favorite(
    request: CreateFavoriteRequest,
    current_user: User = Depends(get_current_subscribed_user),  # Require authenticated user with subscription
):
    """Create a favorite"""    
    # Clean the string - remove any whitespace or quotes
    listing_id = request.listing_id.strip().strip('"').strip("'")
    
    uuid_value = uuid.UUID(listing_id)

    # Make sure the listing actually exists
    exists = False
    collection_names = list(db.list_collection_names())
    for collection_name in collection_names:
        exists = db[collection_name].find_one({"_id": bson.Binary.from_uuid(uuid_value)})
        if exists:
            break

    if not exists:
        raise HTTPException(status_code=404, detail="Listing not found")
    
    # Make sure the user is not already favoriting this listing
    favorite = user_db["favorites"].find_one({"user_id": current_user.id, "listing_id": listing_id})
    if favorite:
        raise HTTPException(status_code=400, detail="Already favoriting this listing")

    # Create the favorite
    insert_data = {"user_id": current_user.id, "listing_id": listing_id, "created_at": datetime.datetime.now()}
    user_db["favorites"].insert_one(insert_data)
    return insert_data


@router.delete("/favorites/{listing_id}", response_model=DeleteFavorite)
def delete_favorite(
    listing_id: str,
    current_user: User = Depends(get_current_subscribed_user),
):
    """Delete a favorite"""
    user_db["favorites"].delete_one({"user_id": current_user.id, "listing_id": listing_id})
    return {"user_id": current_user.id, "listing_id": listing_id}


@router.get("/favorites", response_model=GetFavorites)
def get_favorites(
    current_user: User = Depends(get_current_subscribed_user),
):
    """Get all favorites"""
    favorites = user_db["favorites"].find({"user_id": current_user.id})
    favorites_list = [x["listing_id"] for x in favorites]
    return {"favorites": favorites_list}