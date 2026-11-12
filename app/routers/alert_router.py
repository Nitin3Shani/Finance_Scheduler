from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_user
from app.database import get_db
from app.models import PriceAlert, TrackedProduct, User
from app.schemas import AlertCreate, AlertResponse

router = APIRouter(prefix="/alerts", tags=["Price Alerts"])


@router.post("/", response_model=AlertResponse, status_code= status.HTTP_201_CREATED)
async def upsert_price_alert(
    payload: AlertCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    prod_res = await db.execute(
        select(TrackedProduct).where(
            TrackedProduct.id == payload.product_id,
            TrackedProduct.user_id == current_user.id,
        )
    )
    product = prod_res.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Product not found")

    alert_res = await db.execute(
        select(PriceAlert).where(PriceAlert.product_id == payload.product_id)
    )
    alert = alert_res.scalar_one_or_none()

    if alert:
        alert.target_price = payload.target_price
        alert.is_active = True
    else:
        alert = PriceAlert(
            product_id=payload.product_id,
            target_price=payload.target_price,
            is_active=True,
        )
        db.add(alert)

    await db.commit()
    await db.refresh(alert)
    return alert


@router.get("/{product_id}", response_model=AlertResponse)
async def get_price_alert(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PriceAlert)
        .join(TrackedProduct)
        .where(
            PriceAlert.product_id == product_id,
            TrackedProduct.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No alert set for this product")
    return alert


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_price_alert(
    product_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(PriceAlert)
        .join(TrackedProduct)
        .where(
            PriceAlert.product_id == product_id,
            TrackedProduct.user_id == current_user.id,
        )
    )
    alert = result.scalar_one_or_none()
    if not alert:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

    await db.delete(alert)
    await db.commit()