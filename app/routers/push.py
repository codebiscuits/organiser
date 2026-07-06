from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.push_subscription import PushSubscription
from app.services.push import send_push

router = APIRouter()


class SubscribeRequest(BaseModel):
    endpoint: str
    p256dh: str
    auth: str


@router.get("/public-key")
def get_public_key():
    if not settings.vapid_public_key:
        raise HTTPException(status_code=503, detail="Push notifications not configured")
    return {"publicKey": settings.vapid_public_key}


@router.post("/subscribe", status_code=204)
def subscribe(body: SubscribeRequest, db: Session = Depends(get_db)):
    existing = db.query(PushSubscription).filter_by(endpoint=body.endpoint).first()
    if existing:
        existing.p256dh = body.p256dh
        existing.auth = body.auth
    else:
        db.add(PushSubscription(endpoint=body.endpoint, p256dh=body.p256dh, auth=body.auth))
    db.commit()


@router.delete("/unsubscribe", status_code=204)
def unsubscribe(body: SubscribeRequest, db: Session = Depends(get_db)):
    db.query(PushSubscription).filter_by(endpoint=body.endpoint).delete()
    db.commit()


@router.post("/test")
def test_push(db: Session = Depends(get_db)):
    """
    Send a test push notification to every stored subscription.

    Primary debugging tool for confirming delivery works on a given device
    (e.g. Android over the tailscale-serve HTTPS URL). Prunes any
    subscription the push service reports as gone (404/410).
    """
    if not settings.vapid_private_key:
        raise HTTPException(status_code=503, detail="Push notifications not configured")

    subscriptions = db.query(PushSubscription).all()
    if not subscriptions:
        raise HTTPException(status_code=404, detail="No push subscriptions registered")

    results = []
    for sub in subscriptions:
        result = send_push(sub, "Life Organiser", "Test notification")
        results.append({"endpoint": sub.endpoint[:40] + "...", "result": result})
        if result == "gone":
            db.delete(sub)
    db.commit()

    return {"results": results}
