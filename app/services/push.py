import json
import logging

from pywebpush import webpush, WebPushException

from app.config import settings

logger = logging.getLogger(__name__)


def send_push(subscription, title: str, body: str, url: str = "/") -> bool:
    if not settings.vapid_private_key:
        logger.warning("VAPID keys not configured, skipping push notification")
        return False
    try:
        webpush(
            subscription_info={
                "endpoint": subscription.endpoint,
                "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
            },
            data=json.dumps({"title": title, "body": body, "url": url}),
            vapid_private_key=settings.vapid_private_key,
            vapid_claims={"sub": settings.vapid_subject},
            ttl=300,
        )
        return True
    except WebPushException as e:
        logger.error("Push failed for %s: %s", subscription.endpoint[:40], e)
        return False
