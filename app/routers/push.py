from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.push_subscription import PushSubscription

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
