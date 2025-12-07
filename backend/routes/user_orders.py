# backend/routes/user_orders.py
"""
User-specific order management with authentication.
This replaces the in-memory order queue with database-backed orders per user.
"""
from datetime import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from backend.database import get_db
from backend.models.database import User, Order, OrderStatus
from backend.utils.auth import get_current_user

router = APIRouter()

# --- Request/Response Models ---

class OrderCreate(BaseModel):
    symbol: str = Field(..., min_length=1)
    side: str = Field(..., pattern="^(BUY|SELL)$")
    order_type: str = Field(..., pattern="^(market|limit)$")
    quantity: float = Field(..., gt=0)
    leverage: int = Field(1, ge=1, le=100)
    limit_price: Optional[float] = Field(None, gt=0)
    margin: Optional[float] = None

class OrderUpdate(BaseModel):
    symbol: Optional[str] = None
    side: Optional[str] = Field(None, pattern="^(BUY|SELL)$")
    order_type: Optional[str] = Field(None, pattern="^(market|limit)$")
    quantity: Optional[float] = Field(None, gt=0)
    leverage: Optional[int] = Field(None, ge=1, le=100)
    limit_price: Optional[float] = Field(None, gt=0)
    margin: Optional[float] = None
    status: Optional[str] = None

class OrderResponse(BaseModel):
    id: int
    symbol: str
    side: str
    order_type: str
    quantity: float
    leverage: int
    limit_price: Optional[float]
    margin: Optional[float]
    status: str
    created_at: datetime
    executed_at: Optional[datetime]
    
    class Config:
        from_attributes = True

class BulkOrderCreate(BaseModel):
    orders: List[OrderCreate] = Field(..., min_items=1, max_items=50)

class ExecuteOrdersRequest(BaseModel):
    order_ids: List[int] = Field(..., min_items=1)

# --- Order Management Routes ---

@router.post("/", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(
    order_data: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create a new order in the queue.
    
    The order will be stored in the database and can be executed later.
    """
    # Validate leverage against user's max leverage setting
    if order_data.leverage > current_user.max_leverage:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Leverage {order_data.leverage}x exceeds your maximum allowed leverage of {current_user.max_leverage}x"
        )
    
    # Create order in database
    db_order = Order(
        user_id=current_user.id,
        symbol=order_data.symbol.upper(),
        side=order_data.side,
        order_type=order_data.order_type,
        quantity=order_data.quantity,
        leverage=order_data.leverage,
        limit_price=order_data.limit_price,
        margin=order_data.margin,
        status=OrderStatus.QUEUED
    )
    
    db.add(db_order)
    db.commit()
    db.refresh(db_order)
    
    return db_order

@router.post("/bulk", response_model=List[OrderResponse], status_code=status.HTTP_201_CREATED)
async def create_bulk_orders(
    bulk_data: BulkOrderCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Create multiple orders at once.
    
    Maximum 50 orders per request.
    """
    created_orders = []
    
    for order_data in bulk_data.orders:
        # Validate leverage
        if order_data.leverage > current_user.max_leverage:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Leverage {order_data.leverage}x exceeds maximum {current_user.max_leverage}x"
            )
        
        db_order = Order(
            user_id=current_user.id,
            symbol=order_data.symbol.upper(),
            side=order_data.side,
            order_type=order_data.order_type,
            quantity=order_data.quantity,
            leverage=order_data.leverage,
            limit_price=order_data.limit_price,
            margin=order_data.margin,
            status=OrderStatus.QUEUED
        )
        
        db.add(db_order)
        created_orders.append(db_order)
    
    db.commit()
    
    # Refresh all orders to get IDs
    for order in created_orders:
        db.refresh(order)
    
    return created_orders

@router.get("/", response_model=List[OrderResponse])
async def get_my_orders(
    status_filter: Optional[str] = None,
    limit: int = 100,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get all orders for the current user.
    
    - **status_filter**: Optional filter by order status (queued, executed, failed, cancelled)
    - **limit**: Maximum number of orders to return (default 100)
    """
    query = db.query(Order).filter(Order.user_id == current_user.id)
    
    if status_filter:
        try:
            status_enum = OrderStatus(status_filter.lower())
            query = query.filter(Order.status == status_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status: {status_filter}"
            )
    
    orders = query.order_by(Order.created_at.desc()).limit(limit).all()
    return orders

@router.get("/queued", response_model=List[OrderResponse])
async def get_queued_orders(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get only queued orders (ready to execute).
    """
    orders = db.query(Order).filter(
        Order.user_id == current_user.id,
        Order.status == OrderStatus.QUEUED
    ).order_by(Order.created_at).all()
    
    return orders

@router.get("/{order_id}", response_model=OrderResponse)
async def get_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get a specific order by ID.
    """
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    return order

@router.put("/{order_id}", response_model=OrderResponse)
async def update_order(
    order_id: int,
    order_update: OrderUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Update a queued order.
    
    Only orders with status 'queued' can be updated.
    """
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    if order.status != OrderStatus.QUEUED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only update queued orders"
        )
    
    # Update fields
    if order_update.symbol is not None:
        order.symbol = order_update.symbol.upper()
    if order_update.side is not None:
        order.side = order_update.side
    if order_update.order_type is not None:
        order.order_type = order_update.order_type
    if order_update.quantity is not None:
        order.quantity = order_update.quantity
    if order_update.leverage is not None:
        if order_update.leverage > current_user.max_leverage:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Leverage exceeds maximum {current_user.max_leverage}x"
            )
        order.leverage = order_update.leverage
    if order_update.limit_price is not None:
        order.limit_price = order_update.limit_price
    if order_update.margin is not None:
        order.margin = order_update.margin
    
    order.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(order)
    
    return order

@router.delete("/{order_id}")
async def delete_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Delete a queued order.
    
    Only queued orders can be deleted.
    """
    order = db.query(Order).filter(
        Order.id == order_id,
        Order.user_id == current_user.id
    ).first()
    
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found"
        )
    
    if order.status != OrderStatus.QUEUED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only delete queued orders"
        )
    
    db.delete(order)
    db.commit()
    
    return {"message": "Order deleted successfully", "order_id": order_id}


@router.delete("/")
async def clear_queue(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Clear all queued orders for the current user.
    """
    deleted_count = db.query(Order).filter(
        Order.user_id == current_user.id,
        Order.status == OrderStatus.QUEUED
    ).delete()
    
    db.commit()
    
    return {
        "message": f"Cleared {deleted_count} queued orders",
        "deleted_count": deleted_count
    }

@router.get("/history/executed", response_model=List[OrderResponse])
async def get_executed_orders(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Get executed order history.
    """
    orders = db.query(Order).filter(
        Order.user_id == current_user.id,
        Order.status == OrderStatus.EXECUTED
    ).order_by(Order.executed_at.desc()).limit(limit).all()
    
    return orders