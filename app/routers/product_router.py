from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.database import get_db
from app.models import PriceHistory, TrackedProduct, User
from app.schemas import (
    PricePoint,
    ProductHistoryResponse,
    SearchCompareResponse,
    TrackedProductResponse,
    TrackProductRequest,
)
from app.services.scraper import compare_platforms, scrape_direct_product_price
from datetime import datetime

MAX_TRACKED_PRODUCTS = 10
router = APIRouter(prefix="/products", tags=["Products & Price Tracking"])


@router.get("/search", response_model=SearchCompareResponse)
async def search_and_compare(
    q: str = Query(..., min_length=2, max_length=100, description="Product search query"),
    current_user: User = Depends(get_current_user),
):
    q = " ".join(q.split())   
    if len(q) < 2:
        raise HTTPException(status_code=422, detail="Search query must have at least 2 characters")
    return await compare_platforms(q)


@router.post("/", response_model=TrackedProductResponse, status_code=status.HTTP_201_CREATED)
async def track_product(
    payload: TrackProductRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not payload.amazon_url and not payload.flipkart_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide at least one product URL (Amazon or Flipkart)",
        )
    tracked_count = await db.scalar(
    select(func.count())
    .select_from(TrackedProduct)
    .where(TrackedProduct.user_id == current_user.id)
)
    if tracked_count >= MAX_TRACKED_PRODUCTS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You can track at most {MAX_TRACKED_PRODUCTS} products. Remove one to add another.",
        )

    product = TrackedProduct(
        user_id=current_user.id,
        title=payload.title,
        image_url=payload.image_url,
        amazon_url=payload.amazon_url,
        flipkart_url=payload.flipkart_url,
    )
    db.add(product)
    await db.flush()

    if payload.amazon_url and payload.initial_amazon_price:
        db.add(
            PriceHistory(
                product_id=product.id,
                platform="amazon",
                price=payload.initial_amazon_price,
                in_stock=True,
            )
        )
    if payload.flipkart_url and payload.initial_flipkart_price:
        db.add(
            PriceHistory(
                product_id=product.id,
                platform="flipkart",
                price=payload.initial_flipkart_price,
                in_stock=True,
            )
        )

    await db.commit()
    await db.refresh(product)
    return product


@router.get("/", response_model=list[TrackedProductResponse])
async def list_tracked_products(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TrackedProduct)
        .where(TrackedProduct.user_id == current_user.id)
        .order_by(TrackedProduct.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{product_id}", response_model=ProductHistoryResponse)
async def get_product_history(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TrackedProduct)
        .options(selectinload(TrackedProduct.price_history))
        .where(
            TrackedProduct.id == product_id,
            TrackedProduct.user_id == current_user.id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    sorted_history = sorted(product.price_history, key=lambda h: h.checked_at)
    prices = [entry.price for entry in sorted_history]

    buckets: dict[datetime, PricePoint] = {}
    for entry in sorted_history:
        key = entry.checked_at.replace(second=0, microsecond=0)   # group by minute
        point = buckets.get(key)
        if point is None:
            point = buckets[key] = PricePoint(timestamp=key)
        if entry.platform == "amazon":
            point.amazon_price = entry.price
        elif entry.platform == "flipkart":
            point.flipkart_price = entry.price

    chart_data = list(buckets.values())

    return ProductHistoryResponse(
        product_id=product.id,
        title=product.title,
        lowest_price=min(prices) if prices else None,
        highest_price=max(prices) if prices else None,
        average_price=round(sum(prices) / len(prices), 2) if prices else None,
        chart_data=chart_data,
    )


@router.post("/{product_id}/refresh", response_model=ProductHistoryResponse)
async def refresh_product_now(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TrackedProduct)
        .options(selectinload(TrackedProduct.price_history))
        .where(
            TrackedProduct.id == product_id,
            TrackedProduct.user_id == current_user.id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    checks = []
    if product.amazon_url:
        checks.append(("amazon", product.amazon_url))
    if product.flipkart_url:
        checks.append(("flipkart", product.flipkart_url))

    for platform, url in checks:
        price, in_stock = await scrape_direct_product_price(url, platform)
        if price is not None:
            db.add(
                PriceHistory(
                    product_id=product.id,
                    platform=platform,
                    price=price,
                    in_stock=in_stock,
                )
            )

    await db.commit()
    await db.refresh(product, ["price_history"])
    return await get_product_history(product_id, db, current_user)



@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tracked_product(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(TrackedProduct).where(
            TrackedProduct.id == product_id,
            TrackedProduct.user_id == current_user.id,
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    await db.delete(product)
    await db.commit()