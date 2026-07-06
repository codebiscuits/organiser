import json
import logging

from pywebpush import webpush, WebPushException

from app.config import settings

logger = logging.getLogger(__name__)


def send_push(subscription, title: str, body: str, url: str = "/") -> str:
    """
    Send a single web push notification.

    Returns one of:
    - "ok"     delivered successfully
    - "gone"   the push service reports the subscription no longer exists
               (404/410) — caller should delete the subscription row
    - "failed" any other error (network, VAPID misconfiguration, etc.) —
               caller should retry on the next tick
    """
    if not settings.vapid_private_key:
        logger.warning("VAPID keys not configured, skipping push notification")
        return "failed"
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
        return "ok"
    except WebPushException as e:
        status_code = e.response.status_code if e.response is not None else None
        if status_code in (404, 410):
            logger.info("Subscription gone for %s: %s", subscription.endpoint[:40], e)
            return "gone"
        logger.error("Push failed for %s: %s", subscription.endpoint[:40], e)
        return "failed"
    except Exception as e:
        logger.error("Push failed unexpectedly for %s: %s", subscription.endpoint[:40], e)
        return "failed"
