"""
Tests for push notification delivery (app/services/push.py,
app/services/scheduler.py, app/routers/push.py) and per-task notifications
(app/models/task_notification.py + the /tasks router integration).
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from pywebpush import WebPushException

from app.models.push_subscription import PushSubscription
from app.models.task import Task
from app.models.task_notification import TaskNotification
from app.services import push as push_service
from app.services import scheduler as scheduler_service


def _fake_response(status_code):
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def _sub(endpoint="https://push.example/abc"):
    return PushSubscription(endpoint=endpoint, p256dh="p256dh-key", auth="auth-key")


APPOINTMENT = {
    "type": "appointment",
    "title": "Doctor",
    "importance": 3,
    "scheduled_at": "2099-06-01T10:00:00",
    "estimated_duration": 60,
}


# ---------------------------------------------------------------------------
# send_push result mapping
# ---------------------------------------------------------------------------

class TestSendPush:
    def test_ok_on_success(self, monkeypatch):
        monkeypatch.setattr(push_service.settings, "vapid_private_key", "key")
        with patch.object(push_service, "webpush", return_value=None):
            result = push_service.send_push(_sub(), "Title", "Body")
        assert result == "ok"

    def test_gone_on_410(self, monkeypatch):
        monkeypatch.setattr(push_service.settings, "vapid_private_key", "key")
        with patch.object(
            push_service, "webpush",
            side_effect=WebPushException("gone", response=_fake_response(410)),
        ):
            result = push_service.send_push(_sub(), "Title", "Body")
        assert result == "gone"

    def test_gone_on_404(self, monkeypatch):
        monkeypatch.setattr(push_service.settings, "vapid_private_key", "key")
        with patch.object(
            push_service, "webpush",
            side_effect=WebPushException("missing", response=_fake_response(404)),
        ):
            result = push_service.send_push(_sub(), "Title", "Body")
        assert result == "gone"

    def test_failed_on_other_webpush_status(self, monkeypatch):
        monkeypatch.setattr(push_service.settings, "vapid_private_key", "key")
        with patch.object(
            push_service, "webpush",
            side_effect=WebPushException("bad request", response=_fake_response(400)),
        ):
            result = push_service.send_push(_sub(), "Title", "Body")
        assert result == "failed"

    def test_failed_on_generic_exception(self, monkeypatch):
        monkeypatch.setattr(push_service.settings, "vapid_private_key", "key")
        with patch.object(push_service, "webpush", side_effect=RuntimeError("network down")):
            result = push_service.send_push(_sub(), "Title", "Body")
        assert result == "failed"

    def test_failed_when_vapid_not_configured(self, monkeypatch):
        monkeypatch.setattr(push_service.settings, "vapid_private_key", "")
        result = push_service.send_push(_sub(), "Title", "Body")
        assert result == "failed"


# ---------------------------------------------------------------------------
# Scheduler: appointment reminders
# ---------------------------------------------------------------------------

def _make_appointment(db, **overrides):
    now = datetime.now()
    defaults = dict(
        type="appointment",
        title="Doctor",
        importance=3,
        status="pending",
        scheduled_at=now + timedelta(minutes=5),
        prep_duration=30,  # lead=30 => notify_at = now-25min => already due
    )
    defaults.update(overrides)
    task = Task(**defaults)
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


class TestSchedulerAppointmentNotifications:
    def test_attempts_all_subscriptions_no_short_circuit(self, db):
        db.add_all([_sub("https://a"), _sub("https://b")])
        task = _make_appointment(db)
        db.commit()

        calls = []

        def fake_send(sub, title, body):
            calls.append(sub.endpoint)
            return "ok" if sub.endpoint == "https://b" else "failed"

        with patch.object(scheduler_service, "send_push", side_effect=fake_send):
            scheduler_service._check_appointment_notifications(db)

        assert set(calls) == {"https://a", "https://b"}
        db.refresh(task)
        assert task.push_notified_at is not None

    def test_not_stamped_and_retried_when_all_fail(self, db):
        db.add(_sub("https://a"))
        task = _make_appointment(db)
        db.commit()

        with patch.object(scheduler_service, "send_push", return_value="failed"):
            scheduler_service._check_appointment_notifications(db)
        db.refresh(task)
        assert task.push_notified_at is None

        with patch.object(scheduler_service, "send_push", return_value="ok") as mock_send:
            scheduler_service._check_appointment_notifications(db)
        db.refresh(task)
        assert task.push_notified_at is not None
        assert mock_send.call_count == 1  # retried on the next tick

    def test_gone_subscription_is_pruned(self, db):
        db.add(_sub("https://gone"))
        _make_appointment(db)
        db.commit()

        with patch.object(scheduler_service, "send_push", return_value="gone"):
            scheduler_service._check_appointment_notifications(db)

        assert db.query(PushSubscription).filter_by(endpoint="https://gone").first() is None

    def test_no_subscriptions_is_a_noop(self, db):
        _make_appointment(db)
        db.commit()
        with patch.object(scheduler_service, "send_push") as mock_send:
            scheduler_service._check_appointment_notifications(db)
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# Per-task notifications: model + router
# ---------------------------------------------------------------------------

class TestPerTaskNotificationsCreate:
    def test_create_with_offsets_creates_deduped_rows(self, client, db):
        payload = {**APPOINTMENT, "notification_offsets": [0, 30, 30]}
        resp = client.post("/tasks/", json=payload)
        assert resp.status_code == 200, resp.text
        task_id = resp.json()["id"]

        rows = db.query(TaskNotification).filter_by(task_id=task_id).all()
        assert sorted(r.offset_minutes for r in rows) == [0, 30]
        assert resp.json()["notification_offsets"] == [0, 30]

    def test_no_offsets_creates_no_rows(self, client, db):
        resp = client.post("/tasks/", json=APPOINTMENT)
        task_id = resp.json()["id"]
        rows = db.query(TaskNotification).filter_by(task_id=task_id).all()
        assert rows == []

    def test_negative_offset_rejected(self, client, db):
        payload = {**APPOINTMENT, "notification_offsets": [-5]}
        resp = client.post("/tasks/", json=payload)
        assert resp.status_code == 422

    def test_past_time_guard_stamps_sent_at_immediately(self, client, db):
        past = datetime.now() - timedelta(minutes=10)
        payload = {
            "type": "appointment",
            "title": "Already due",
            "importance": 3,
            "scheduled_at": past.isoformat(),
            "notification_offsets": [0],
        }
        resp = client.post("/tasks/", json=payload)
        task_id = resp.json()["id"]
        row = db.query(TaskNotification).filter_by(task_id=task_id).first()
        assert row.sent_at is not None

    def test_future_offset_not_stamped(self, client, db):
        payload = {**APPOINTMENT, "notification_offsets": [0]}
        resp = client.post("/tasks/", json=payload)
        task_id = resp.json()["id"]
        row = db.query(TaskNotification).filter_by(task_id=task_id).first()
        assert row.sent_at is None


class TestPerTaskNotificationsUpdate:
    def test_update_replaces_rows(self, client, db):
        resp = client.post("/tasks/", json={**APPOINTMENT, "notification_offsets": [0]})
        task_id = resp.json()["id"]

        resp2 = client.put(f"/tasks/{task_id}", json={"notification_offsets": [15, 45]})
        assert resp2.status_code == 200

        rows = db.query(TaskNotification).filter_by(task_id=task_id).all()
        assert sorted(r.offset_minutes for r in rows) == [15, 45]

    def test_update_without_field_leaves_rows_unchanged(self, client, db):
        resp = client.post("/tasks/", json={**APPOINTMENT, "notification_offsets": [0, 30]})
        task_id = resp.json()["id"]

        resp2 = client.put(f"/tasks/{task_id}", json={"title": "Doctor visit"})
        assert resp2.status_code == 200

        rows = db.query(TaskNotification).filter_by(task_id=task_id).all()
        assert sorted(r.offset_minutes for r in rows) == [0, 30]

    def test_update_with_empty_list_removes_all(self, client, db):
        resp = client.post("/tasks/", json={**APPOINTMENT, "notification_offsets": [0, 30]})
        task_id = resp.json()["id"]

        resp2 = client.put(f"/tasks/{task_id}", json={"notification_offsets": []})
        assert resp2.status_code == 200

        rows = db.query(TaskNotification).filter_by(task_id=task_id).all()
        assert rows == []

    def test_update_reapplies_past_time_guard_against_new_scheduled_at(self, client, db):
        resp = client.post("/tasks/", json={**APPOINTMENT, "notification_offsets": [0]})
        task_id = resp.json()["id"]

        past = (datetime.now() - timedelta(minutes=5)).isoformat()
        resp2 = client.put(
            f"/tasks/{task_id}",
            json={"scheduled_at": past, "notification_offsets": [0]},
        )
        assert resp2.status_code == 200

        row = db.query(TaskNotification).filter_by(task_id=task_id).first()
        assert row.sent_at is not None


class TestPerTaskNotificationsDelete:
    def test_delete_task_cascades_notifications(self, client, db):
        resp = client.post("/tasks/", json={**APPOINTMENT, "notification_offsets": [0, 30]})
        task_id = resp.json()["id"]
        assert db.query(TaskNotification).filter_by(task_id=task_id).count() == 2

        resp2 = client.delete(f"/tasks/{task_id}")
        assert resp2.status_code == 200

        assert db.query(TaskNotification).filter_by(task_id=task_id).count() == 0


# ---------------------------------------------------------------------------
# Scheduler: per-task notifications
# ---------------------------------------------------------------------------

class TestSchedulerTaskNotifications:
    def test_fires_only_due_notifications(self, db):
        now = datetime.now()
        task = Task(
            type="appointment", title="Standup", importance=2, status="pending",
            scheduled_at=now + timedelta(minutes=5),
        )
        db.add(task)
        db.flush()
        not_due = TaskNotification(task_id=task.id, offset_minutes=0)  # fires at scheduled_at (+5m, not yet)
        due = TaskNotification(task_id=task.id, offset_minutes=30)  # scheduled_at - 30m = now-25m, due
        db.add_all([not_due, due])
        db.add(_sub("https://x"))
        db.commit()

        with patch.object(scheduler_service, "send_push", return_value="ok") as mock_send:
            scheduler_service._check_task_notifications(db)

        db.refresh(not_due)
        db.refresh(due)
        assert not_due.sent_at is None
        assert due.sent_at is not None
        assert mock_send.call_count == 1

    def test_each_notification_fires_exactly_once(self, db):
        now = datetime.now()
        task = Task(
            type="appointment", title="X", importance=2, status="pending",
            scheduled_at=now - timedelta(minutes=1),
        )
        db.add(task)
        db.flush()
        notif = TaskNotification(task_id=task.id, offset_minutes=0)
        db.add(notif)
        db.add(_sub("https://x"))
        db.commit()

        with patch.object(scheduler_service, "send_push", return_value="ok") as mock_send:
            scheduler_service._check_task_notifications(db)
            scheduler_service._check_task_notifications(db)

        assert mock_send.call_count == 1

    def test_not_stamped_when_delivery_fails(self, db):
        now = datetime.now()
        task = Task(
            type="appointment", title="X", importance=2, status="pending",
            scheduled_at=now - timedelta(minutes=1),
        )
        db.add(task)
        db.flush()
        notif = TaskNotification(task_id=task.id, offset_minutes=0)
        db.add(notif)
        db.add(_sub("https://x"))
        db.commit()

        with patch.object(scheduler_service, "send_push", return_value="failed"):
            scheduler_service._check_task_notifications(db)

        db.refresh(notif)
        assert notif.sent_at is None

    def test_gone_subscription_pruned_by_task_notification_check(self, db):
        now = datetime.now()
        task = Task(
            type="appointment", title="X", importance=2, status="pending",
            scheduled_at=now - timedelta(minutes=1),
        )
        db.add(task)
        db.flush()
        db.add(TaskNotification(task_id=task.id, offset_minutes=0))
        db.add(_sub("https://gone"))
        db.commit()

        with patch.object(scheduler_service, "send_push", return_value="gone"):
            scheduler_service._check_task_notifications(db)

        assert db.query(PushSubscription).filter_by(endpoint="https://gone").first() is None


# ---------------------------------------------------------------------------
# Router: /push/subscribe, /push/unsubscribe, /push/test
# ---------------------------------------------------------------------------

class TestPushRouter:
    def test_subscribe_creates_row(self, client, db):
        resp = client.post(
            "/push/subscribe",
            json={"endpoint": "https://x", "p256dh": "p", "auth": "a"},
        )
        assert resp.status_code == 204
        assert db.query(PushSubscription).filter_by(endpoint="https://x").count() == 1

    def test_subscribe_upserts_by_endpoint(self, client, db):
        client.post("/push/subscribe", json={"endpoint": "https://x", "p256dh": "p1", "auth": "a1"})
        client.post("/push/subscribe", json={"endpoint": "https://x", "p256dh": "p2", "auth": "a2"})

        rows = db.query(PushSubscription).filter_by(endpoint="https://x").all()
        assert len(rows) == 1
        assert rows[0].p256dh == "p2"
        assert rows[0].auth == "a2"

    def test_unsubscribe_deletes_row(self, client, db):
        client.post("/push/subscribe", json={"endpoint": "https://x", "p256dh": "p", "auth": "a"})
        resp = client.request(
            "DELETE", "/push/unsubscribe",
            json={"endpoint": "https://x", "p256dh": "p", "auth": "a"},
        )
        assert resp.status_code == 204
        assert db.query(PushSubscription).filter_by(endpoint="https://x").count() == 0

    def test_public_key_503_when_unconfigured(self, client, db, monkeypatch):
        from app.routers import push as push_router
        monkeypatch.setattr(push_router.settings, "vapid_public_key", "")
        resp = client.get("/push/public-key")
        assert resp.status_code == 503

    def test_test_endpoint_503_when_vapid_unset(self, client, db, monkeypatch):
        from app.routers import push as push_router
        monkeypatch.setattr(push_router.settings, "vapid_private_key", "")
        resp = client.post("/push/test")
        assert resp.status_code == 503

    def test_test_endpoint_404_when_no_subscriptions(self, client, db, monkeypatch):
        from app.routers import push as push_router
        monkeypatch.setattr(push_router.settings, "vapid_private_key", "key")
        resp = client.post("/push/test")
        assert resp.status_code == 404

    def test_test_endpoint_reports_results_and_prunes_gone(self, client, db, monkeypatch):
        from app.routers import push as push_router
        monkeypatch.setattr(push_router.settings, "vapid_private_key", "key")
        db.add_all([_sub("https://ok"), _sub("https://gone")])
        db.commit()

        def fake_send(sub, title, body):
            return "ok" if sub.endpoint == "https://ok" else "gone"

        with patch.object(push_router, "send_push", side_effect=fake_send):
            resp = client.post("/push/test")

        assert resp.status_code == 200
        results = resp.json()["results"]
        assert len(results) == 2
        assert {r["result"] for r in results} == {"ok", "gone"}
        assert db.query(PushSubscription).filter_by(endpoint="https://gone").count() == 0
        assert db.query(PushSubscription).filter_by(endpoint="https://ok").count() == 1
