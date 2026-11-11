import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import TrackedProduct, PriceHistory
from app.services.scraper import scrape_direct_product_price
from app.services.notifier import send_price_alert_email

logger = logging.getLogger(__name__)

JOB_ID = "price_check_job"


SYNC_DB_URL = settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")

scheduler = AsyncIOScheduler(
    jobstores={
        
        "default": SQLAlchemyJobStore(url=SYNC_DB_URL, tablename="apscheduler_jobs"),
    },
    job_defaults={
        "coalesce": True,                # if runs were missed, run once, not several times
        "max_instances": 1,              # never run two checks at once
        "misfire_grace_time": 3 * 3600,  # still run a check that is up to 3 hours late
    },
)


async def _check_product(product_id: int) -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(TrackedProduct)
            .where(TrackedProduct.id == product_id)
            .options(
                selectinload(TrackedProduct.owner),
                selectinload(TrackedProduct.alert),
            )
        )
        product = result.scalar_one_or_none()
        if product is None:
            return

        checks: list[tuple[str, str]] = []
        if product.amazon_url:
            checks.append(("amazon", product.amazon_url))
        if product.flipkart_url:
            checks.append(("flipkart", product.flipkart_url))

        for platform, url in checks:
            price, in_stock = await scrape_direct_product_price(url, platform)
            await asyncio.sleep(2)  # brief pause to reduce rate-limiting risk

            if price is None:
                continue  # failed or unavailable: record nothing

            db.add(
                PriceHistory(
                    product_id=product.id,
                    platform=platform,
                    price=price,
                    in_stock=in_stock,
                )
            )

            alert = product.alert
            if alert and alert.is_active and price <= alert.target_price:
                sent = await send_price_alert_email(
                    recipient_email=product.owner.email,
                    product_title=product.title,
                    platform=platform,
                    current_price=price,
                    target_price=alert.target_price,
                    product_url=url,
                )
                if sent:  # only pause the alert if the email really went out
                    alert.is_active = False
                    alert.last_triggered_at = datetime.now(timezone.utc)

        await db.commit()


async def check_all_tracked_products() -> None:
    """Periodic job: scrape tracked URLs, log PriceHistory, fire active alerts.

    Keep this function at module level and don't rename or move it: the saved job
    in the database refers to it by its import path.
    """
    async with AsyncSessionLocal() as db:
        product_ids = (await db.execute(select(TrackedProduct.id))).scalars().all()

    for product_id in product_ids:
        try:
            await _check_product(product_id)
        except Exception:
            logger.exception("price check failed for product_id=%s", product_id)


def start_scheduler() -> None:
    if scheduler.running:
        return

    scheduler.start()  # loads any saved jobs from the database

    # Add the job only if it isn't already saved, so a restart keeps the old timer.
    # (replace_existing=True would overwrite it and reset the 6-hour wait.)
    if scheduler.get_job(JOB_ID) is None:
        scheduler.add_job(
            check_all_tracked_products,
            trigger="interval",
            minutes=5,  
            id=JOB_ID
    )

def stop_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)