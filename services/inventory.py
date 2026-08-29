from collections import defaultdict
from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlmodel import Session, select

from models import Order, OrderItem, Product, ProductSubVariant, ProductVariant


def validate_inventory_selection(
    session: Session,
    product: Product,
    product_variant_id: UUID | None,
    product_sub_variant_id: UUID | None,
    quantity: int,
) -> None:
    if product_variant_id is not None:
        variant = session.exec(
            select(ProductVariant).where(
                ProductVariant.id == product_variant_id,
                ProductVariant.product_id == product.id,
            )
        ).first()
        if not variant:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The selected variant does not belong to this product",
            )

        has_sub_variants = session.exec(
            select(ProductSubVariant.id).where(
                ProductSubVariant.product_variant_id == variant.id
            )
        ).first()

        if has_sub_variants:
            if product_sub_variant_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"A size must be selected for variant {variant.name}",
                )
            sub_variant = session.exec(
                select(ProductSubVariant).where(
                    ProductSubVariant.id == product_sub_variant_id,
                    ProductSubVariant.product_variant_id == variant.id,
                )
            ).first()
            if not sub_variant:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="The selected size does not belong to this variant",
                )
            available_quantity = sub_variant.quantity
            inventory_name = f"{variant.name} ({sub_variant.size})"
        else:
            if product_sub_variant_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"The selected variant {variant.name} does not support sizes",
                )
            available_quantity = variant.quantity
            inventory_name = variant.name
    else:
        if product_sub_variant_id is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A product variant must be selected before choosing a size",
            )
        has_variants = session.exec(
            select(ProductVariant.id).where(ProductVariant.product_id == product.id)
        ).first()
        if has_variants:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A product variant must be selected",
            )
        available_quantity = product.quantity
        inventory_name = product.name

    if quantity > available_quantity:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only {available_quantity} units of {inventory_name} are available",
        )


def _load_inventory(
    session: Session,
    order: Order,
) -> tuple[
    list[OrderItem],
    dict[UUID, Product],
    dict[UUID, ProductVariant],
    dict[UUID, ProductSubVariant],
]:
    items = list(
        session.exec(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.id)
            .with_for_update()
        ).all()
    )
    product_ids = sorted({item.product_id for item in items}, key=str)
    products = list(
        session.exec(
            select(Product)
            .where(Product.id.in_(product_ids))
            .order_by(Product.id)
            .with_for_update()
        ).all()
    )
    variants = list(
        session.exec(
            select(ProductVariant)
            .where(ProductVariant.product_id.in_(product_ids))
            .order_by(ProductVariant.id)
            .with_for_update()
        ).all()
    )
    variant_ids = [variant.id for variant in variants]
    sub_variants = list(
        session.exec(
            select(ProductSubVariant)
            .where(ProductSubVariant.product_variant_id.in_(variant_ids))
            .order_by(ProductSubVariant.id)
            .with_for_update()
        ).all()
    ) if variant_ids else []
    return (
        items,
        {product.id: product for product in products},
        {variant.id: variant for variant in variants},
        {sub_variant.id: sub_variant for sub_variant in sub_variants},
    )

def _sync_variant_quantity_from_sub_variants(
    variant: ProductVariant,
    sub_variants: dict[UUID, ProductSubVariant],
) -> None:
    variant_sub_variants = [
        sub_variant
        for sub_variant in sub_variants.values()
        if sub_variant.product_variant_id == variant.id
    ]
    if variant_sub_variants:
        variant.quantity = sum(sub_variant.quantity for sub_variant in variant_sub_variants)

def commit_inventory_for_order(session: Session, order: Order) -> None:
    if order.inventory_committed_at is not None:
        return

    items, products, variants, sub_variants = _load_inventory(session, order)
    variants_by_product: dict[UUID, list[ProductVariant]] = defaultdict(list)
    sub_variants_by_variant: dict[UUID, list[ProductSubVariant]] = defaultdict(list)
    for variant in variants.values():
        variants_by_product[variant.product_id].append(variant)
    for sub_variant in sub_variants.values():
        sub_variants_by_variant[sub_variant.product_variant_id].append(sub_variant)

    product_demand: dict[UUID, int] = defaultdict(int)
    variant_demand: dict[UUID, int] = defaultdict(int)
    sub_variant_demand: dict[UUID, int] = defaultdict(int)

    for item in items:
        product = products.get(item.product_id)
        if not product:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Product {item.product_id} is no longer available",
            )

        product_variants = variants_by_product[item.product_id]
        if product_variants:
            if item.product_variant_id is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"A variant is required for product {product.name}",
                )
            variant = variants.get(item.product_variant_id)
            if not variant or variant.product_id != item.product_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"The selected variant for {product.name} is invalid",
                )

            variant_sub_variants = sub_variants_by_variant.get(variant.id, [])
            if variant_sub_variants:
                if item.product_sub_variant_id is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"A size is required for variant {variant.name}",
                    )
                sub_variant = sub_variants.get(item.product_sub_variant_id)
                if (
                    not sub_variant
                    or sub_variant.product_variant_id != variant.id
                ):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"The selected size for {variant.name} is invalid",
                    )
                sub_variant_demand[sub_variant.id] += item.quantity
            else:
                if item.product_sub_variant_id is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"The selected variant for {product.name} does not support sizes",
                    )
                variant_demand[variant.id] += item.quantity
        else:
            if item.product_variant_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"The selected variant for {product.name} is unavailable",
                )
            if item.product_sub_variant_id is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"The selected size for {product.name} is unavailable",
                )
            product_demand[product.id] += item.quantity

    for sub_variant_id, quantity in sub_variant_demand.items():
        sub_variant = sub_variants[sub_variant_id]
        if sub_variant.quantity < quantity:
            variant = variants[sub_variant.product_variant_id]
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Only {sub_variant.quantity} units of "
                    f"{variant.name} ({sub_variant.size}) are available"
                ),
            )

    for variant_id, quantity in variant_demand.items():
        variant = variants[variant_id]
        if variant.quantity < quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Only {variant.quantity} units of {variant.name} are available",
            )

    for product_id, quantity in product_demand.items():
        product = products[product_id]
        if product.quantity < quantity:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Only {product.quantity} units of {product.name} are available",
            )

    for sub_variant_id, quantity in sub_variant_demand.items():
        sub_variants[sub_variant_id].quantity -= quantity
    for variant_id, quantity in variant_demand.items():
        variants[variant_id].quantity -= quantity
    for product_id, quantity in product_demand.items():
        products[product_id].quantity -= quantity

    for variant in variants.values():
        _sync_variant_quantity_from_sub_variants(variant, sub_variants)
    for product_id, product_variants in variants_by_product.items():
        products[product_id].quantity = sum(
            variant.quantity for variant in product_variants
        )

    order.inventory_committed_at = datetime.now(timezone.utc)
    session.add(order)


def restore_inventory_for_order(session: Session, order: Order) -> None:
    if (
        order.inventory_committed_at is None
        or order.inventory_restored_at is not None
    ):
        return

    items, products, variants, sub_variants = _load_inventory(session, order)
    variant_product_ids: set[UUID] = set()

    for item in items:
        product = products.get(item.product_id)
        if not product:
            continue
        if item.product_variant_id is None:
            product.quantity += item.quantity
            continue

        variant = variants.get(item.product_variant_id)
        if not variant or variant.product_id != item.product_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ordered variant is unavailable for inventory restoration",
            )
        
        if item.product_sub_variant_id is not None:
            sub_variant = sub_variants.get(item.product_sub_variant_id)
            if (
                not sub_variant
                or sub_variant.product_variant_id != variant.id
            ):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Ordered size is unavailable for inventory restoration",
                )
            sub_variant.quantity += item.quantity
        else:
            variant.quantity += item.quantity

        _sync_variant_quantity_from_sub_variants(variant, sub_variants)
        variant_product_ids.add(item.product_id)

    for product_id in variant_product_ids:
        products[product_id].quantity = sum(
            variant.quantity
            for variant in variants.values()
            if variant.product_id == product_id
        )

    order.inventory_restored_at = datetime.now(timezone.utc)
    session.add(order)
