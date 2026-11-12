import logging
from email.message import EmailMessage

import aiosmtplib

from app.config import settings

logger = logging.getLogger(__name__)


async def send_price_alert_email(
    recipient_email: str,
    product_title: str,
    platform: str,
    current_price: float,
    target_price: float,
    product_url: str,
) -> bool:
    
    if not settings.SMTP_USER or not settings.SMTP_PASSWORD:
        logger.warning("alert skipped, SMTP not configured: %s", product_title)
        return False

    clean_title = " ".join(product_title.split())  
    
    msg = EmailMessage()
    msg["From"] = settings.SMTP_USER
    msg["To"] = recipient_email
    msg["Subject"] = f"Price drop alert: {clean_title[:50]} is now ₹{current_price:,.2f}"
    msg.set_content(
        f"Good news!\n\n"
        f"Your tracked product '{clean_title}' has dropped to your target price "
        f"on {platform.capitalize()}.\n\n"
        f"• Current price: ₹{current_price:,.2f}\n"
        f"• Your target price: ₹{target_price:,.2f}\n"
        f"• Buy link: {product_url}\n\n"
        f"This alert has been paused automatically. Re-enable it or set a new "
        f"target price anytime in your dashboard."
    )

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            start_tls=True,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            timeout=15,
        )
    except Exception:
        logger.exception("failed to send price alert to %s", recipient_email)
        return False

    return True