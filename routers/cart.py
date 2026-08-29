from fastapi import APIRouter, status, Depends, Query, HTTPException
from models import (
    Cart,
    CartRead,
    CartItemCreate,
    CartItemRead,
    CartItem,
    CartItemUpdate,
    Product,
    ProductVariant,
    User,
)
from database import SessionDep
from services.access_token import get_current_user
from services.inventory import validate_inventory_selection
from uuid import UUID
from sqlmodel import select
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

router = APIRouter(prefix="/cart", tags=["Cart"])

def get_or_create_cart(session: SessionDep, current_user: User) -> Cart:
    cart = session.exec(
        select(Cart)
        .where(Cart.user_id == current_user.id)
        .options(
            selectinload(Cart.items).selectinload(CartItem.product),
            selectinload(Cart.items).selectinload(CartItem.product_variant),
            selectinload(Cart.items).selectinload(CartItem.product_sub_variant),
            selectinload(Cart.user),
        )
    ).first()

    if cart:
        return cart

    cart = Cart(user_id=current_user.id, total_price=0, total_products=0)
    session.add(cart)
    session.flush()
    return cart

def refresh_cart_totals(session: SessionDep, cart_id: UUID) -> Cart:
    total_products, total_price = session.exec(
        select(
            func.coalesce(func.sum(CartItem.quantity), 0),
            func.coalesce(func.sum(CartItem.total_price), 0),
        ).where(CartItem.cart_id == cart_id)
    ).one()

    cart = session.exec(
        select(Cart)
        .where(Cart.id == cart_id)
    ).one()
    cart.total_products = total_products
    cart.total_price = total_price
    session.add(cart)
    return cart

def _add_or_merge_cart_item(session: SessionDep, current_user: User, item: CartItemCreate, product: Product) -> UUID:
    cart = get_or_create_cart(session, current_user)
    existing_item = session.exec(
        select(CartItem)
        .where(
            CartItem.cart_id == cart.id,
            CartItem.product_id == item.product_id,
            CartItem.product_variant_id == item.product_variant_id,
            CartItem.product_sub_variant_id == item.product_sub_variant_id,
        )
    ).first()

    if existing_item:
        # Idempotent add: re-sending the same product sets the requested
        # quantity instead of incrementing, so duplicate/double-fired POSTs
        # can't make the quantity drift. Use PUT /cart/items/{id} to change it.
        existing_item.quantity = item.quantity
        existing_item.total_price = existing_item.quantity * product.price
        target_item = existing_item
    else:
        target_item = CartItem(
            cart_id=cart.id,
            product_id=item.product_id,
            product_variant_id=item.product_variant_id,
            product_sub_variant_id=item.product_sub_variant_id,
            quantity=item.quantity,
            total_price=item.quantity * product.price,
        )

    session.add(target_item)
    session.flush()
    refresh_cart_totals(session, cart.id)
    return target_item.id

@router.post("/items", response_model=CartItemRead, status_code=status.HTTP_201_CREATED)
def create_cart_item(item: CartItemCreate, session: SessionDep, current_user: User = Depends(get_current_user)):
    product = session.exec(select(Product).where(Product.id == item.product_id)).first()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")
    validate_inventory_selection(
        session,
        product,
        item.product_variant_id,
        item.product_sub_variant_id,
        item.quantity,
    )

    try:
        item_id = _add_or_merge_cart_item(session, current_user, item, product)
        session.commit()
    except IntegrityError:
        # A concurrent request already created a cart item for this product.
        # Roll back and merge into the now-existing row instead of inserting a duplicate.
        session.rollback()
        item_id = _add_or_merge_cart_item(session, current_user, item, product)
        session.commit()

    return session.exec(
        select(CartItem)
        .where(CartItem.id == item_id)
        .options(
            selectinload(CartItem.product),
            selectinload(CartItem.product_variant),
            selectinload(CartItem.product_sub_variant),
        )
    ).one()

@router.get("", response_model=CartRead)
def get_cart(
    session: SessionDep,
    current_user: User = Depends(get_current_user),
    page: int = Query(1, description="Page number", ge=1),
    page_size: int = Query(10, description="Number of items per page", ge=1, le=100),
):
    cart = session.exec(
        select(Cart)
        .where(Cart.user_id == current_user.id)
        .options(
            selectinload(Cart.items).selectinload(CartItem.product),
            selectinload(Cart.items).selectinload(CartItem.product_variant),
            selectinload(Cart.items).selectinload(CartItem.product_sub_variant),
            selectinload(Cart.user),
        )
    ).first()

    if not cart:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart not found")

    return cart

@router.put("/items/{item_id}", response_model=CartItemRead, status_code=status.HTTP_202_ACCEPTED)
def update_cart_item(item_id: UUID, item_update: CartItemUpdate, session: SessionDep, current_user: User = Depends(get_current_user)):
    updated_item = session.exec(
        select(CartItem)
        .join(Cart, CartItem.cart_id == Cart.id)
        .where(CartItem.id == item_id, Cart.user_id == current_user.id)
        .options(
            selectinload(CartItem.product),
            selectinload(CartItem.product_variant),
            selectinload(CartItem.product_sub_variant),
        )
    ).first()
    if not updated_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart Item not found")
    requested_quantity = (
        item_update.quantity
        if item_update.quantity is not None
        else updated_item.quantity
    )
    validate_inventory_selection(
        session,
        updated_item.product,
        updated_item.product_variant_id,
        updated_item.product_sub_variant_id,
        requested_quantity,
    )
    updated_item.sqlmodel_update(item_update.model_dump(exclude_unset=True))
    updated_item.total_price = updated_item.quantity * updated_item.product.price
    session.add(updated_item)
    session.flush()
    refresh_cart_totals(session, updated_item.cart_id)
    session.commit()
    session.refresh(updated_item)
    return updated_item

@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_cart_item(item_id: UUID, session: SessionDep, current_user: User = Depends(get_current_user)):
    deleted_item = session.exec(
        select(CartItem)
        .join(Cart, CartItem.cart_id == Cart.id)
        .where(CartItem.id == item_id, Cart.user_id == current_user.id)
    ).first()
    if not deleted_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cart Item not found")
    cart_id = deleted_item.cart_id
    session.delete(deleted_item)
    session.flush()
    refresh_cart_totals(session, cart_id)
    session.commit()
    return None