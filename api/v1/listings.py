import math
import datetime
import uuid
import bson
from fastapi import APIRouter, Query, Depends, HTTPException
from typing import Optional
from core.database import listings_db as db
from core.config import settings
from core.models import User
from core.auth import get_current_subscribed_user

router = APIRouter()

def get_all_listings_filtered(
    prefecture: Optional[str] = None,
    layout: Optional[str] = None,
    sale_price_min: Optional[int] = None,
    sale_price_max: Optional[int] = None,
    building_area_min: Optional[int] = None,
    building_area_max: Optional[int] = None,
    land_area_min: Optional[int] = None,
    land_area_max: Optional[int] = None,
    construction_year_min: Optional[int] = None,
    construction_year_max: Optional[int] = None,
    sort_by: Optional[str] = "createdAt",
    sort_order: Optional[str] = "desc",
    page: int = 1,
    limit: int = 20,
):
    query = {}

    if prefecture:
        query["Prefecture"] = prefecture
    if layout:
        query["Building - Layout"] = layout
    
    # Add price filtering to query - only include listings with numeric sale prices
    query["Sale Price"] = {"$exists": True, "$type": "number"}
    
    if sale_price_min is not None or sale_price_max is not None:
        if "Sale Price" not in query:
            query["Sale Price"] = {}
        if sale_price_min is not None:
            query["Sale Price"]["$gte"] = sale_price_min
        if sale_price_max is not None:
            query["Sale Price"]["$lte"] = sale_price_max

    # Map API parameter to database field name
    if sort_by == "sale_price":
        sort_field = "Sale Price"
    else:
        sort_field = "createdAt"
    sort_direction = 1 if sort_order == "asc" else -1

    # Use MongoDB aggregation with $unionWith to create a unified view across all collections
    # This allows proper database-level sorting and pagination
    
    # Get the first collection as the base
    collection_names = list(db.list_collection_names())
    if not collection_names:
        return {
            "results": [],
            "total_count": 0,
            "total_pages": 0,
            "current_page": page
        }
    
    base_collection = db[collection_names[0]]
    
    # Build the aggregation pipeline
    pipeline = [
        # Match documents based on query filters
        {"$match": query},
        
        # Project and add computed fields
        {"$addFields": {
            # Extract first number from "Building - Area" string
            "building_area_numeric": {
                "$let": {
                    "vars": {
                        "matches": {
                            "$regexFind": {
                                "input": {"$ifNull": ["$Building - Area", ""]},
                                "regex": r"([0-9]+(?:\.[0-9]+)?)"
                            }
                        }
                    },
                    "in": {
                        "$cond": {
                            "if": {"$ne": ["$$matches", None]},
                            "then": {"$toDouble": "$$matches.match"},
                            "else": None
                        }
                    }
                }
            },
            # Extract first number from "Land - Area" string
            "land_area_numeric": {
                "$let": {
                    "vars": {
                        "matches": {
                            "$regexFind": {
                                "input": {"$ifNull": ["$Land - Area", ""]},
                                "regex": r"([0-9]+(?:\.[0-9]+)?)"
                            }
                        }
                    },
                    "in": {
                        "$cond": {
                            "if": {"$ne": ["$$matches", None]},
                            "then": {"$toDouble": "$$matches.match"},
                            "else": None
                        }
                    }
                }
            },
            # Extract construction year from "Building - Construction Date" string
            # Look for 4-digit number first, then "X years" format
            "construction_year": {
                "$let": {
                    "vars": {
                        "dateStr": {"$ifNull": ["$Building - Construction Date", ""]},
                        "currentYear": {"$year": "$$NOW"}
                    },
                    "in": {
                        "$cond": {
                            "if": {"$eq": ["$$dateStr", ""]},
                            "then": None,
                            "else": {
                                "$let": {
                                    "vars": {
                                        # Look for any 4-digit number
                                        "fourDigitMatch": {
                                            "$regexFind": {
                                                "input": "$$dateStr",
                                                "regex": r"(\d{4})"
                                            }
                                        }
                                    },
                                    "in": {
                                        "$cond": {
                                            "if": {"$ne": ["$$fourDigitMatch", None]},
                                            "then": {"$toInt": "$$fourDigitMatch.match"},
                                            "else": {
                                                "$let": {
                                                    "vars": {
                                                        # Look for "X years" format
                                                        "yearsMatch": {
                                                            "$regexFind": {
                                                                "input": "$$dateStr",
                                                                "regex": r"(\d+)\s*years?"
                                                            }
                                                        }
                                                    },
                                                    "in": {
                                                        "$cond": {
                                                            "if": {"$ne": ["$$yearsMatch", None]},
                                                            "then": {
                                                                "$subtract": [
                                                                    "$$currentYear",
                                                                    {"$toInt": {"$arrayElemAt": ["$$yearsMatch.captures", 0]}}
                                                                ]
                                                            },
                                                            "else": None
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }},
        
        # Filter by building area if specified
        *([{
            "$match": {
                "building_area_numeric": {"$gte": building_area_min}
            }
        }] if building_area_min is not None else []),
        
        *([{
            "$match": {
                "building_area_numeric": {"$lte": building_area_max}
            }
        }] if building_area_max is not None else []),
        
                    # Filter by land area if specified
            *([{
                "$match": {
                    "land_area_numeric": {"$gte": land_area_min}
                }
            }] if land_area_min is not None else []),
            
            *([{
                "$match": {
                    "land_area_numeric": {"$lte": land_area_max}
                }
            }] if land_area_max is not None else []),
            
            # Filter by construction year if specified (exclude nulls/invalid values)
            *([{
                "$match": {
                    "construction_year": {
                        "$gte": construction_year_min,
                        "$ne": None,
                        "$type": "number"
                    }
                }
            }] if construction_year_min is not None else []),
            
            *([{
                "$match": {
                    "construction_year": {
                        "$lte": construction_year_max,
                        "$ne": None,
                        "$type": "number"
                    }
                }
            }] if construction_year_max is not None else []),

        
        # Project only the fields we need
        {"$project": {
            "_id": {"$toString": "$_id"},
            "Prefecture": 1,
            "Building - Layout": 1,
            "Sale Price": 1,
            "link": 1,
            "Building - Area": 1,
            "Land - Area": 1,
            "Building - Construction Date": 1,
            "Building - Structure": 1,
            "Property Type": 1,
            "Property Location": 1,
            "Transportation": 1,
            "createdAt": 1,
            "images": 1,
            "Contact Number": 1,
            "Reference URL": 1,
            "building_area_numeric": 1,  # Include for debugging if needed
            "land_area_numeric": 1,  # Include for debugging if needed
            "construction_year": 1  # Include for debugging if needed
        }}
    ]
    
    # Add $unionWith for all other collections
    for coll_name in collection_names[1:]:
        union_pipeline = [
            {"$match": query},
            
            # Project and add computed fields
            {"$addFields": {
                # Extract first number from "Building - Area" string
                "building_area_numeric": {
                    "$let": {
                        "vars": {
                            "matches": {
                                "$regexFind": {
                                    "input": {"$ifNull": ["$Building - Area", ""]},
                                    "regex": r"([0-9]+(?:\.[0-9]+)?)"
                                }
                            }
                        },
                        "in": {
                            "$cond": {
                                "if": {"$ne": ["$$matches", None]},
                                "then": {"$toDouble": "$$matches.match"},
                                "else": None
                            }
                        }
                    }
                },
                # Extract first number from "Land - Area" string
                "land_area_numeric": {
                    "$let": {
                        "vars": {
                            "matches": {
                                "$regexFind": {
                                    "input": {"$ifNull": ["$Land - Area", ""]},
                                    "regex": r"([0-9]+(?:\.[0-9]+)?)"
                                }
                            }
                        },
                        "in": {
                            "$cond": {
                                "if": {"$ne": ["$$matches", None]},
                                "then": {"$toDouble": "$$matches.match"},
                                "else": None
                            }
                        }
                    }
                },
                # Extract construction year from "Building - Construction Date" string
                # Look for 4-digit number first, then "X years" format
                "construction_year": {
                    "$let": {
                        "vars": {
                            "dateStr": {"$ifNull": ["$Building - Construction Date", ""]},
                            "currentYear": {"$year": "$$NOW"}
                        },
                        "in": {
                            "$cond": {
                                "if": {"$eq": ["$$dateStr", ""]},
                                "then": None,
                                "else": {
                                    "$let": {
                                        "vars": {
                                            # Look for any 4-digit number
                                            "fourDigitMatch": {
                                                "$regexFind": {
                                                    "input": "$$dateStr",
                                                    "regex": r"(\d{4})"
                                                }
                                            }
                                        },
                                        "in": {
                                            "$cond": {
                                                "if": {"$ne": ["$$fourDigitMatch", None]},
                                                "then": {"$toInt": "$$fourDigitMatch.match"},
                                                "else": {
                                                    "$let": {
                                                        "vars": {
                                                            # Look for "X years" format
                                                            "yearsMatch": {
                                                                "$regexFind": {
                                                                    "input": "$$dateStr",
                                                                    "regex": r"(\d+)\s*years?"
                                                                }
                                                            }
                                                        },
                                                        "in": {
                                                            "$cond": {
                                                                "if": {"$ne": ["$$yearsMatch", None]},
                                                                                                                                 "then": {
                                                                     "$subtract": [
                                                                         "$$currentYear",
                                                                         {"$toInt": {"$arrayElemAt": ["$$yearsMatch.captures", 0]}}
                                                                     ]
                                                                 },
                                                                "else": None
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }},
            
            # Filter by building area if specified
            *([{
                "$match": {
                    "building_area_numeric": {"$gte": building_area_min}
                }
            }] if building_area_min is not None else []),
            
            *([{
                "$match": {
                    "building_area_numeric": {"$lte": building_area_max}
                }
            }] if building_area_max is not None else []),
            
            # Filter by land area if specified
            *([{
                "$match": {
                    "land_area_numeric": {"$gte": land_area_min}
                }
            }] if land_area_min is not None else []),
            
            *([{
                "$match": {
                    "land_area_numeric": {"$lte": land_area_max}
                }
            }] if land_area_max is not None else []),
            
            # Filter by construction year if specified (exclude nulls/invalid values)
            *([{
                "$match": {
                    "construction_year": {
                        "$gte": construction_year_min,
                        "$ne": None,
                        "$type": "number"
                    }
                }
            }] if construction_year_min is not None else []),
            
            *([{
                "$match": {
                    "construction_year": {
                        "$lte": construction_year_max,
                        "$ne": None,
                        "$type": "number"
                    }
                }
            }] if construction_year_max is not None else []),
            
            {"$project": {
                "_id": {"$toString": "$_id"},
                "Prefecture": 1,
                "Building - Layout": 1,
                "Sale Price": 1,
                "link": 1,
                "Building - Area": 1,
                "Land - Area": 1,
                "Building - Construction Date": 1,
                "Building - Structure": 1,
                "Property Type": 1,
                "Property Location": 1,
                "Transportation": 1,
                "createdAt": 1,
                "images": 1,
                "Contact Number": 1,
                "Reference URL": 1,
                "building_area_numeric": 1,  # Include for debugging if needed
                "land_area_numeric": 1,  # Include for debugging if needed
                "construction_year": 1  # Include for debugging if needed
            }}
        ]
        
        pipeline.append({
            "$unionWith": {
                "coll": coll_name,
                "pipeline": union_pipeline
            }
        })
    
    # Add sorting
    pipeline.append({"$sort": {sort_field: sort_direction}})
    
    # Get total count first (without pagination)
    count_pipeline = pipeline.copy()
    count_pipeline.append({"$count": "total"})
    
    count_result = list(base_collection.aggregate(count_pipeline))
    total_count = count_result[0]["total"] if count_result else 0
    
    # Add pagination
    pipeline.extend([
        {"$skip": (page - 1) * limit},
        {"$limit": limit}
    ])
    
    # Execute the aggregation
    all_results = list(base_collection.aggregate(pipeline))
    
    total_pages = math.ceil(total_count / limit)

    return {
        "results": all_results,
        "total_count": total_count,
        "total_pages": total_pages,
        "current_page": page
    }


@router.get("/listings")
def get_listings(
    current_user: User = Depends(get_current_subscribed_user),  # Require authenticated user with subscription
    prefecture: Optional[str] = Query(None),
    layout: Optional[str] = Query(None),
    sale_price_min: Optional[int] = Query(None),
    sale_price_max: Optional[int] = Query(None),
    building_area_min: Optional[int] = Query(None),
    building_area_max: Optional[int] = Query(None),
    land_area_min: Optional[int] = Query(None),
    land_area_max: Optional[int] = Query(None),
    construction_year_min: Optional[int] = Query(None),
    construction_year_max: Optional[int] = Query(None),
    sort_by: Optional[str] = Query("createdAt", regex="^(createdAt|sale_price)$"),
    sort_order: Optional[str] = Query("desc", regex="^(asc|desc)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, le=100),
):
    """Get listings - requires active subscription"""
    results = get_all_listings_filtered(
        prefecture=prefecture,
        layout=layout,
        sale_price_min=sale_price_min,
        sale_price_max=sale_price_max,
        building_area_min=building_area_min,
        building_area_max=building_area_max,
        land_area_min=land_area_min,
        land_area_max=land_area_max,
        construction_year_min=construction_year_min,
        construction_year_max=construction_year_max,
        sort_by=sort_by,
        sort_order=sort_order,
        page=page,
        limit=limit
    )
    return results


@router.get("/listings/{listing_id}")
def get_listing_by_id(
    listing_id: str,
    current_user: User = Depends(get_current_subscribed_user),  # Require authenticated user with subscription
):
    """Get a specific listing by ID"""
    try:
        # Convert string ID to UUID
        uuid_value = uuid.UUID(listing_id)
        
        # Search for the listing across all collections
        collection_names = list(db.list_collection_names())
        for collection_name in collection_names:
            listing = db[collection_name].find_one({"_id": bson.Binary.from_uuid(uuid_value)})
            if listing:
                # Convert _id to string for JSON serialization
                listing["_id"] = str(listing["_id"])
                return listing
        
        raise HTTPException(status_code=404, detail="Listing not found")
        
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid listing ID format")
