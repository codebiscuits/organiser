"""
Integration tests for API routes — exercises the full HTTP → DB → response cycle.

Uses a fresh in-memory SQLite DB per test (via conftest.py fixtures).
Tests verify DB state after mutations rather than HTML content, since most
endpoints return HTMX fragments.
"""
import pytest
from datetime import date, datetime, timedelta, time

from app.models.task import Task, CompletedTask
from app.models.recurrence import Projection, Recurrence
from app.models.workout import Exercise, ExerciseMuscle, MuscleGroup, PerformedSet
from app.models.preset import TaskPreset
from app.services.scheduling import build_daily_schedule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FUTURE_DATE = date(2099, 1, 15)

PRESET_PAYLOAD = {
    "name": "Weekly Standup",
    "type": "recurring",
    "title": "Team Standup",
    "estimated_duration": 15,
    "importance": 2,
    "urgency": 2,
    "allow_afternoon": False,
    "interval_type": "weekly",
    "interval_multiple": 1,
    "day_of_week": "1,3,5",
}

ERRAND_PAYLOAD = {
    "type": "errand",
    "title": "Buy milk",
    "importance": 2,
    "urgency": 2,
    "estimated_duration": 15,
    "allow_afternoon": False,
}

APPOINTMENT_PAYLOAD = {
    "type": "appointment",
    "title": "Doctor",
    "importance": 3,
    "scheduled_at": "2099-06-01T10:00:00",
    "estimated_duration": 60,
}

DEADLINE_PAYLOAD = {
    "type": "deadline",
    "title": "Tax return",
    "importance": 3,
    "deadline_at": "2099-04-15T23:59:00",
    "estimated_duration": 120,
}

RECURRING_PAYLOAD = {
    "type": "recurring",
    "title": "Morning walk",
    "importance": 2,
    "urgency": 2,
    "estimated_duration": 30,
    "recurrence": {
        "interval_type": "daily",
        "interval_multiple": 1,
        "start_date": "2026-01-01T00:00:00",
    },
}

WORKOUT_PAYLOAD = {
    "type": "workout",
    "title": "Workout",
    "importance": 2,
    "urgency": 2,
    "estimated_duration": 45,
    "recurrence": {
        "interval_type": "daily",
        "interval_multiple": 1,
        "start_date": "2026-01-01T00:00:00",
    },
}


def create_task(client, payload: dict) -> dict:
    resp = client.post("/tasks/", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def add_todays_projection(db, task_id: str) -> Projection:
    """
    Ensure a Projection for today exists for this task, returning it.

    Idempotent (get-or-create): since the finding-9 fix, create_task itself
    already anchors a recurring/variable_recurring/workout task's first
    projection window at max(today, start_date), so a task created from a
    past-dated payload (e.g. RECURRING_PAYLOAD's start_date of 2026-01-01)
    may already have today's projection by the time a test calls this — a
    plain unconditional insert would violate the (task_id, due_date) unique
    constraint.
    """
    existing = db.query(Projection).filter(
        Projection.task_id == task_id, Projection.due_date == date.today()
    ).first()
    if existing:
        return existing
    p = Projection(task_id=task_id, due_date=date.today())
    db.add(p)
    db.commit()
    return p


# ---------------------------------------------------------------------------
# Task creation
# ---------------------------------------------------------------------------

class TestCreateTask:
    def test_create_errand(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        assert data["type"] == "errand"
        assert data["title"] == "Buy milk"
        assert data["status"] == "pending"
        assert data["importance"] == 2
        assert data["urgency"] == 2

    def test_create_appointment(self, client, db):
        data = create_task(client, APPOINTMENT_PAYLOAD)
        assert data["type"] == "appointment"
        assert data["title"] == "Doctor"
        assert data["scheduled_at"] == "2099-06-01T10:00:00"

    def test_create_deadline(self, client, db):
        data = create_task(client, DEADLINE_PAYLOAD)
        assert data["type"] == "deadline"
        assert data["deadline_at"] == "2099-04-15T23:59:00"

    def test_create_recurring_generates_projections(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        projections = db.query(Projection).filter(Projection.task_id == task_id).all()
        assert len(projections) > 0
        # 90 days generated from start_date; start is in the past so all within range
        assert len(projections) >= 1

    def test_create_recurring_creates_recurrence_rule(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
        assert recurrence is not None
        assert recurrence.interval_type == "daily"
        assert recurrence.interval_multiple == 1

    def test_create_appointment_with_prep_duration_generates_prep_task(self, client, db):
        payload = {
            **APPOINTMENT_PAYLOAD,
            "prep_duration": 30,
        }
        data = create_task(client, payload)
        task_id = data["id"]

        all_tasks = db.query(Task).all()
        assert len(all_tasks) == 2  # main + prep

        prep = db.query(Task).filter(Task.title.like("Getting ready for%")).first()
        assert prep is not None
        assert prep.estimated_duration == 30
        # Prep task scheduled 30 min before the appointment
        assert prep.scheduled_at == datetime(2099, 6, 1, 9, 30)

    def test_create_task_missing_required_field_returns_422(self, client, db):
        resp = client.post("/tasks/", json={"type": "errand"})  # missing title, importance
        assert resp.status_code == 422

    def test_get_task_by_id(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.get(f"/tasks/{task_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id

    def test_get_nonexistent_task_returns_404(self, client, db):
        resp = client.get("/tasks/does-not-exist")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Review finding 9 — a recurrence start_date far in the past used to
# generate a projection window entirely before today, leaving the task with
# zero live projections. Fix: anchor the window at max(today, start_date).
# ---------------------------------------------------------------------------

class TestPastStartDateProjections:
    PAST_START = {
        "interval_type": "daily",
        "interval_multiple": 1,
        # Well outside the old fixed 90-day window measured from start_date.
        "start_date": (date.today() - timedelta(days=200)).isoformat() + "T00:00:00",
    }

    def test_create_task_with_past_start_date_has_live_projection(self, client, db):
        data = create_task(client, {
            "type": "recurring",
            "title": "Old daily habit",
            "importance": 2,
            "urgency": 2,
            "estimated_duration": 10,
            "recurrence": self.PAST_START,
        })
        task_id = data["id"]

        live = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date >= date.today(),
        ).count()
        assert live > 0, "a recurrence with a start_date >90 days in the past must still have a live projection"

    def test_type_change_to_recurring_with_past_start_date_has_live_projection(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(
            f"/tasks/{task_id}",
            json={
                "type": "recurring",
                "title": "Old daily habit",
                "importance": 2,
                "urgency": 2,
                "recurrence": self.PAST_START,
            },
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        live = db.query(Projection).filter(
            Projection.task_id == new_id,
            Projection.due_date >= date.today(),
        ).count()
        assert live > 0, "type-change into recurring with a past start_date must still have a live projection"

    def test_update_adds_recurrence_with_past_start_date_has_live_projection(self, client, db):
        # A plain errand doesn't carry recurrence — post it as an errand,
        # then PUT the same type with a recurrence attached, exercising
        # update_task's "task had no recurrence row before" branch.
        data = create_task(client, {**ERRAND_PAYLOAD, "type": "recurring", "recurrence": {
            "interval_type": "daily", "interval_multiple": 1,
        }})
        task_id = data["id"]
        # Wipe the recurrence row this created so update_task takes the
        # "newly recurring" branch again below.
        db.query(Recurrence).filter(Recurrence.task_id == task_id).delete()
        db.query(Projection).filter(Projection.task_id == task_id).delete()
        db.commit()

        resp = client.put(
            f"/tasks/{task_id}",
            json={"recurrence": self.PAST_START},
        )
        assert resp.status_code == 200, resp.text

        live = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date >= date.today(),
        ).count()
        assert live > 0, "adding a recurrence with a past start_date must still produce a live projection"


# ---------------------------------------------------------------------------
# Review finding 8 — duplicate-title check used SQLite's ASCII-only LOWER(),
# missing matches on titles with uppercase accented letters.
# ---------------------------------------------------------------------------

class TestCheckTitleDuplicate:
    def test_exact_match_flagged_duplicate(self, client, db):
        create_task(client, {**ERRAND_PAYLOAD, "title": "Buy milk"})
        resp = client.get("/tasks/check-title", params={"title": "Buy milk"})
        assert resp.status_code == 200
        assert resp.json()["duplicate"] is True

    def test_no_match_not_duplicate(self, client, db):
        resp = client.get("/tasks/check-title", params={"title": "Nothing like this exists"})
        assert resp.status_code == 200
        assert resp.json()["duplicate"] is False

    def test_matches_non_ascii_uppercase_case_insensitively(self, client, db):
        """
        SQLite's built-in LOWER() only folds ASCII case, so a title with an
        uppercase accented letter would never match its lowercase form
        under the old `func.lower(Task.title) == normalized` SQL filter.
        Python's str.lower() is Unicode-aware and catches it.
        """
        create_task(client, {**ERRAND_PAYLOAD, "title": "CAFÉ run"})
        resp = client.get("/tasks/check-title", params={"title": "café run"})
        assert resp.status_code == 200
        assert resp.json()["duplicate"] is True

    def test_exclude_id_ignores_self_match(self, client, db):
        data = create_task(client, {**ERRAND_PAYLOAD, "title": "Buy milk"})
        resp = client.get("/tasks/check-title", params={"title": "Buy milk", "exclude_id": data["id"]})
        assert resp.status_code == 200
        assert resp.json()["duplicate"] is False


# ---------------------------------------------------------------------------
# Task completion
# ---------------------------------------------------------------------------

class TestCompleteTask:
    def test_complete_errand_deletes_task(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.post(f"/tasks/{task_id}/complete")
        assert resp.status_code == 200
        import json as _json
        trigger = _json.loads(resp.headers.get("hx-trigger", "{}"))
        assert "showUndo" in trigger

        assert db.query(Task).filter(Task.id == task_id).first() is None

    def test_complete_errand_records_in_completed_tasks(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        client.post(f"/tasks/{task_id}/complete")

        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None
        assert completed.task_title == "Buy milk"
        assert completed.task_type == "errand"

    def test_complete_appointment_deletes_task(self, client, db):
        data = create_task(client, APPOINTMENT_PAYLOAD)
        task_id = data["id"]

        client.post(f"/tasks/{task_id}/complete")

        assert db.query(Task).filter(Task.id == task_id).first() is None

    def test_complete_deadline_deletes_task(self, client, db):
        data = create_task(client, DEADLINE_PAYLOAD)
        task_id = data["id"]

        client.post(f"/tasks/{task_id}/complete")

        assert db.query(Task).filter(Task.id == task_id).first() is None

    def test_complete_recurring_removes_projection_keeps_task(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        client.post(f"/tasks/{task_id}/complete")

        # Task should still exist (recurring)
        assert db.query(Task).filter(Task.id == task_id).first() is not None
        # Today's projection should be gone
        today_proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == date.today(),
        ).first()
        assert today_proj is None

    def test_complete_nonexistent_task_returns_404(self, client, db):
        resp = client.post("/tasks/does-not-exist/complete")
        assert resp.status_code == 404

    def test_complete_with_actual_duration_records_it(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        client.post(f"/tasks/{task_id}/complete", json={"actual_duration": 20, "notes": "Quick trip"})

        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed.actual_duration == 20
        assert completed.notes == "Quick trip"


# ---------------------------------------------------------------------------
# Task deferral
# ---------------------------------------------------------------------------

class TestDeferTask:
    def test_defer_increments_deferred_count(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.post(f"/tasks/{task_id}/defer")
        assert resp.status_code == 200
        import json as _json
        trigger = _json.loads(resp.headers.get("hx-trigger", "{}"))
        assert "showUndo" in trigger

        task = db.query(Task).filter(Task.id == task_id).first()
        db.refresh(task)
        assert task.deferred_count == 1

    def test_defer_moves_todays_projection_to_tomorrow(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        client.post(f"/tasks/{task_id}/defer")

        tomorrow = date.today() + timedelta(days=1)
        proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == tomorrow,
        ).first()
        assert proj is not None

        # Today's projection should be gone
        today_proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == date.today(),
        ).first()
        assert today_proj is None

    def test_defer_nonexistent_task_returns_404(self, client, db):
        resp = client.post("/tasks/does-not-exist/defer")
        assert resp.status_code == 404

    def test_defer_daily_task_when_tomorrow_already_projected(self, client, db):
        """
        Fallout of the finding-9 fix: a daily recurrence now has tomorrow's
        occurrence generated up front (previously, a past start_date meant
        no future projections existed at all, so this collision was
        unreachable). Deferring today's occurrence must not try to move it
        onto the same (task_id, due_date) as the already-existing tomorrow
        projection — it should just drop today's row instead.
        """
        data = create_task(client, {
            "type": "recurring",
            "title": "Daily habit",
            "importance": 2,
            "urgency": 2,
            "estimated_duration": 10,
            "recurrence": {"interval_type": "daily", "interval_multiple": 1},
        })
        task_id = data["id"]

        tomorrow = date.today() + timedelta(days=1)
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == tomorrow
        ).first() is not None, "sanity check: tomorrow's projection should already exist"

        resp = client.post(f"/tasks/{task_id}/defer")
        assert resp.status_code == 200

        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == date.today()
        ).first() is None
        # Exactly one row for tomorrow (not a duplicate, not gone).
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == tomorrow
        ).count() == 1


# ---------------------------------------------------------------------------
# Task deletion
# ---------------------------------------------------------------------------

class TestDeleteTask:
    def test_delete_removes_task(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.delete(f"/tasks/{task_id}")
        assert resp.status_code == 200

        assert db.query(Task).filter(Task.id == task_id).first() is None

    def test_delete_does_not_record_in_completed_tasks(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        client.delete(f"/tasks/{task_id}")

        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is None

    def test_delete_recurring_removes_recurrence_and_projections(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        client.delete(f"/tasks/{task_id}")

        assert db.query(Task).filter(Task.id == task_id).first() is None
        assert db.query(Recurrence).filter(Recurrence.task_id == task_id).count() == 0
        assert db.query(Projection).filter(Projection.task_id == task_id).count() == 0

    def test_delete_nonexistent_task_returns_404(self, client, db):
        resp = client.delete("/tasks/does-not-exist")
        assert resp.status_code == 404

    def test_undo_delete_restores_tombstones(self, client, db):
        """
        Review finding 5: delete_task hard-deletes ProjectionExclusion rows
        but the ActionLog snapshot only ever covered Projection rows, so
        _undo_delete had no way to bring tombstones back — a regeneration
        after undoing a delete could resurrect occurrences the user had
        deliberately skipped before deleting the task.
        """
        from app.models.recurrence import ProjectionExclusion

        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        skipped_date = date.today() + timedelta(days=5)
        db.add(ProjectionExclusion(task_id=task_id, due_date=skipped_date))
        db.commit()

        resp = client.delete(f"/tasks/{task_id}")
        assert resp.status_code == 200
        assert db.query(ProjectionExclusion).filter(ProjectionExclusion.task_id == task_id).count() == 0

        import json as _json
        log_id = _json.loads(resp.headers["hx-trigger"])["showUndo"]["ids"][0]
        undo_resp = client.post(f"/undo/{log_id}")
        assert undo_resp.status_code == 200

        assert db.query(Task).filter(Task.id == task_id).first() is not None
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id,
            ProjectionExclusion.due_date == skipped_date,
        ).first() is not None


# ---------------------------------------------------------------------------
# Recurring task delete modal / delete-instance
# ---------------------------------------------------------------------------

class TestDeleteInstance:
    def _task_with_todays_projection(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        add_todays_projection(db, task_id)
        return task_id

    def test_delete_instance_removes_only_todays_projection(self, client, db):
        task_id = self._task_with_todays_projection(client, db)

        total_before = db.query(Projection).filter(Projection.task_id == task_id).count()
        assert total_before > 1

        resp = client.post(f"/tasks/{task_id}/delete-instance")
        assert resp.status_code == 200

        remaining = db.query(Projection).filter(Projection.task_id == task_id).count()
        assert remaining == total_before - 1
        assert db.query(Task).filter(Task.id == task_id).first() is not None

    def test_delete_instance_returns_undo_trigger(self, client, db):
        task_id = self._task_with_todays_projection(client, db)

        resp = client.post(f"/tasks/{task_id}/delete-instance")
        assert "showUndo" in resp.headers.get("hx-trigger", "")

    def test_delete_instance_writes_action_log(self, client, db):
        from app.models.action_log import ActionLog
        task_id = self._task_with_todays_projection(client, db)

        client.post(f"/tasks/{task_id}/delete-instance")

        log = db.query(ActionLog).filter(
            ActionLog.task_id == task_id,
            ActionLog.action_type == "delete_instance",
        ).first()
        assert log is not None
        assert date.today().isoformat() in log.projections_snapshot

    def test_undo_delete_instance_restores_projection(self, client, db):
        from app.models.action_log import ActionLog
        task_id = self._task_with_todays_projection(client, db)

        total_before = db.query(Projection).filter(Projection.task_id == task_id).count()
        client.post(f"/tasks/{task_id}/delete-instance")

        log = db.query(ActionLog).filter(
            ActionLog.task_id == task_id,
            ActionLog.action_type == "delete_instance",
        ).first()
        client.post(f"/undo/{log.id}")

        assert db.query(Projection).filter(Projection.task_id == task_id).count() == total_before

    def test_delete_confirm_modal_returns_html(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        resp = client.get(f"/tasks/{task_id}/delete-confirm")
        assert resp.status_code == 200
        assert "delete-confirm-modal" in resp.text
        assert "delete-instance" in resp.text

    def test_delete_instance_no_projection_returns_404(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.post(f"/tasks/{task_id}/delete-instance")
        assert resp.status_code == 404

    def test_delete_instance_finds_carried_forward_vrt_projection(self, client, db):
        """
        Theme A component A2: a VRT's carried-forward projection can have
        due_date < today (see docs/design-theme-a.md §3). "Delete today
        only" must find and remove that past-dated projection instead of
        assuming due_date == today and 404ing.
        """
        from app.models.recurrence import ProjectionExclusion

        payload = {
            "type": "variable_recurring",
            "title": "Overdue VRT",
            "importance": 2,
            "urgency": 2,
            "estimated_duration": 60,
            "recurrence": {
                "interval_type": "monthly",
                "interval_multiple": 1,
                "start_date": "2026-01-01T00:00:00",
            },
        }
        data = create_task(client, payload)
        task_id = data["id"]

        db.query(Projection).filter(Projection.task_id == task_id).delete()
        past_due = date.today() - timedelta(days=6)
        db.add(Projection(task_id=task_id, due_date=past_due))
        db.commit()

        resp = client.post(f"/tasks/{task_id}/delete-instance")
        assert resp.status_code == 200, resp.text

        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == past_due
        ).first() is None
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id, ProjectionExclusion.due_date == past_due
        ).first() is not None

    def test_delete_instance_not_resurrected_by_regeneration(self, client, db):
        """
        Regression test: "delete today only" must survive a projection
        regeneration (e.g. the periodic refresh job, or the admin
        regenerate-all endpoint) — the deleted occurrence should not
        reappear.
        """
        from app.models.recurrence import ProjectionExclusion

        task_id = self._task_with_todays_projection(client, db)

        resp = client.post(f"/tasks/{task_id}/delete-instance")
        assert resp.status_code == 200
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == date.today()
        ).first() is None
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id, ProjectionExclusion.due_date == date.today()
        ).first() is not None

        # Regenerate everything, as the admin "regenerate all" endpoint does.
        resp = client.post("/admin/refresh-projections")
        assert resp.status_code == 200

        still_gone = db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == date.today()
        ).first()
        assert still_gone is None, "today's deleted occurrence was resurrected by regeneration"

    def test_undo_delete_instance_then_regeneration_recreates_it(self, client, db):
        """
        Undoing a "delete today only" should clear the tombstone too, so a
        later regeneration behaves as if the deletion never happened.
        """
        from app.models.action_log import ActionLog
        from app.models.recurrence import ProjectionExclusion

        task_id = self._task_with_todays_projection(client, db)
        client.post(f"/tasks/{task_id}/delete-instance")

        log = db.query(ActionLog).filter(
            ActionLog.task_id == task_id,
            ActionLog.action_type == "delete_instance",
        ).first()
        client.post(f"/undo/{log.id}")

        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == date.today()
        ).first() is not None
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id, ProjectionExclusion.due_date == date.today()
        ).first() is None

        # A regeneration after the undo should keep (or recreate) today's
        # occurrence rather than treating it as excluded.
        client.post("/admin/refresh-projections")
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == date.today()
        ).first() is not None


# ---------------------------------------------------------------------------
# Variable recurring completion
# ---------------------------------------------------------------------------

class TestVariableRecurringCompletion:
    def _make_variable_recurring(self, client, db):
        payload = {
            "type": "variable_recurring",
            "title": "Dentist",
            "importance": 2,
            "urgency": 2,
            "estimated_duration": 60,
            "recurrence": {
                "interval_type": "monthly",
                "interval_multiple": 1,
                "start_date": "2026-01-01T00:00:00",
            },
        }
        return create_task(client, payload)

    def test_variable_recurring_completion_schedules_next_occurrence(self, client, db):
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        resp = client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 30},
        )
        assert resp.status_code == 200

        expected_date = date.today() + timedelta(days=30)
        proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == expected_date,
        ).first()
        assert proj is not None

    def test_variable_recurring_completion_removes_todays_projection(self, client, db):
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 14},
        )

        today_proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == date.today(),
        ).first()
        assert today_proj is None

    def test_variable_recurring_completion_records_in_completed_tasks(self, client, db):
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 7, "actual_duration": 45},
        )

        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None
        assert completed.actual_duration == 45

    def test_completing_non_variable_task_as_variable_returns_400(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 7},
        )
        assert resp.status_code == 400

    def test_explicit_next_date_overrides_prior_tombstone(self, client, db):
        """
        Review finding 3: complete_variable_recurring_task used to insert
        the new Projection without checking ProjectionExclusion, so if the
        user's chosen next_date happened to match a date they'd previously
        "deleted today only" for this task, the Projection and the
        Exclusion tombstone ended up coexisting for that date forever (a
        later regeneration never cleans up an exclusion once a live
        projection already exists at that date).

        Decision: an explicit user-chosen date overrides the tombstone —
        the tombstone should be removed, not the projection skipped.
        """
        from app.models.recurrence import ProjectionExclusion

        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        target_date = date.today() + timedelta(days=10)
        db.add(ProjectionExclusion(task_id=task_id, due_date=target_date))
        db.commit()

        resp = client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 10},
        )
        assert resp.status_code == 200, resp.text

        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id,
            ProjectionExclusion.due_date == target_date,
        ).first() is None
        assert db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == target_date,
        ).first() is not None


# ---------------------------------------------------------------------------
# Theme A, component A2 — carried-forward (overdue) VRT completion/defer.
#
# A VRT projection can now have due_date < today (carried forward instead of
# vanishing — see docs/design-theme-a.md §3 A2). These endpoints used to
# assume due_date == today / >= today for a VRT's single open projection;
# they must find and act on the past-dated one too.
# ---------------------------------------------------------------------------

class TestCarriedForwardVrtCompletion:
    def _make_variable_recurring(self, client, db):
        payload = {
            "type": "variable_recurring",
            "title": "Overdue VRT",
            "importance": 2,
            "urgency": 2,
            "estimated_duration": 60,
            "recurrence": {
                "interval_type": "monthly",
                "interval_multiple": 1,
                "start_date": "2026-01-01T00:00:00",
            },
        }
        return create_task(client, payload)

    def _carry_forward(self, db, task_id: str, days_overdue: int) -> date:
        """Replace whatever anchor projection create_task made with a single
        past-dated one, simulating an overdue carried-forward VRT."""
        db.query(Projection).filter(Projection.task_id == task_id).delete()
        past_due = date.today() - timedelta(days=days_overdue)
        db.add(Projection(task_id=task_id, due_date=past_due))
        db.commit()
        return past_due

    def test_completing_carried_forward_vrt_removes_past_projection(self, client, db):
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        past_due = self._carry_forward(db, task_id, days_overdue=10)

        resp = client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 14},
        )
        assert resp.status_code == 200, resp.text

        # The past-dated projection must be gone, not just filtered by
        # due_date >= today (the pre-fix behaviour left it as a phantom row).
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == past_due
        ).first() is None

    def test_completing_carried_forward_vrt_schedules_next_occurrence(self, client, db):
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        self._carry_forward(db, task_id, days_overdue=10)

        client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 14},
        )

        expected_next = date.today() + timedelta(days=14)
        remaining = db.query(Projection).filter(Projection.task_id == task_id).all()
        assert len(remaining) == 1
        assert remaining[0].due_date == expected_next

    def test_carried_forward_vrt_complete_form_still_accessible(self, client, db):
        """The 'when next?' prompt (GET .../complete/variable) is keyed off
        task type, not date — a carried-forward VRT must still get it."""
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        self._carry_forward(db, task_id, days_overdue=10)

        resp = client.get(f"/tasks/{task_id}/complete/variable")
        assert resp.status_code == 200

    def test_deferring_carried_forward_vrt_moves_projection_to_tomorrow(self, client, db):
        data = self._make_variable_recurring(client, db)
        task_id = data["id"]
        past_due = self._carry_forward(db, task_id, days_overdue=4)

        resp = client.post(f"/tasks/{task_id}/defer")
        assert resp.status_code == 200, resp.text

        tomorrow = date.today() + timedelta(days=1)
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == tomorrow
        ).first() is not None
        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == past_due
        ).first() is None


# ---------------------------------------------------------------------------
# Workout completion
# ---------------------------------------------------------------------------

class TestWorkoutCompletion:
    def _setup_workout(self, client, db):
        mg = MuscleGroup(name="Chest", recovery_time=2)
        db.add(mg)
        db.commit()
        db.refresh(mg)

        ex = Exercise(name="Bench Press", intensity="heavy")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        db.add(ExerciseMuscle(exercise_id=ex.id, muscle_id=mg.id))
        db.commit()

        data = create_task(client, WORKOUT_PAYLOAD)
        return data["id"], ex.id

    def test_complete_workout_records_performed_set(self, client, db):
        task_id, exercise_id = self._setup_workout(client, db)
        add_todays_projection(db, task_id)

        resp = client.post(
            f"/tasks/{task_id}/complete/workout",
            data={
                "exercise_id": exercise_id,
                "sets": 3,
                "reps": 10,
                "weight_kg": 60.0,
                "intensity": "heavy",
            },
        )
        assert resp.status_code == 200

        performed = db.query(PerformedSet).filter(
            PerformedSet.exercise_id == exercise_id
        ).first()
        assert performed is not None
        assert performed.reps == 10
        assert performed.weight_kg == 60.0
        assert performed.num_sets == 3
        assert performed.intensity == "heavy"

    def test_complete_workout_removes_todays_projection(self, client, db):
        task_id, exercise_id = self._setup_workout(client, db)
        add_todays_projection(db, task_id)

        client.post(
            f"/tasks/{task_id}/complete/workout",
            data={
                "exercise_id": exercise_id,
                "sets": 3,
                "reps": 8,
                "weight_kg": 80.0,
                "intensity": "heavy",
            },
        )

        proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == date.today(),
        ).first()
        assert proj is None

    def test_workout_completion_form_returns_200(self, client, db):
        task_id, _ = self._setup_workout(client, db)

        resp = client.get(f"/tasks/{task_id}/complete/workout")
        assert resp.status_code == 200

    def test_completing_non_workout_task_as_workout_returns_400(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.post(
            f"/tasks/{task_id}/complete/workout",
            data={
                "exercise_id": 1,
                "sets": 3,
                "reps": 10,
                "weight_kg": 60.0,
                "intensity": "heavy",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Timeline reorder (manual scheduling)
# ---------------------------------------------------------------------------

class TestTimelineReorder:
    def test_reorder_sets_manual_scheduled_time(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        today = date.today()
        resp = client.post(
            "/tasks/timeline/reorder",
            json={
                "tasks": [
                    {"task_id": task_id, "scheduled_hour": 14, "scheduled_minute": 30}
                ]
            },
        )
        assert resp.status_code == 200

        task = db.query(Task).filter(Task.id == task_id).first()
        db.refresh(task)
        assert task.manual_scheduled_time is not None
        assert task.manual_scheduled_time.hour == 14
        assert task.manual_scheduled_time.minute == 30
        assert task.manual_scheduled_time.date() == today


# ---------------------------------------------------------------------------
# Week view — single occurrence deletion
# ---------------------------------------------------------------------------

class TestDeleteProjection:
    def test_delete_single_occurrence_removes_only_that_date(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        # Verify projections exist
        projections = db.query(Projection).filter(Projection.task_id == task_id).all()
        assert len(projections) > 0

        # Pick one to delete
        target = projections[0]
        target_date = target.due_date.isoformat()

        resp = client.delete(
            f"/tasks/week/projection/{task_id}",
            params={"date": target_date},
        )
        assert resp.status_code == 200

        # That specific projection gone
        deleted = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == target.due_date,
        ).first()
        assert deleted is None

        # Other projections still exist
        remaining = db.query(Projection).filter(Projection.task_id == task_id).count()
        assert remaining >= 1

    def test_delete_nonexistent_projection_returns_404(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        resp = client.delete(
            f"/tasks/week/projection/{task_id}",
            params={"date": "2099-12-31"},
        )
        assert resp.status_code == 404

    def test_deleted_projection_not_resurrected_by_regeneration(self, client, db):
        """
        Regression test for the projection-resurrection bug: deleting one
        occurrence via the week view must survive a later regeneration
        (periodic refresh job / admin regenerate-all), not just the moment
        of deletion.

        Uses a recurrence starting today (rather than the module's
        RECURRING_PAYLOAD, whose fixed start_date is in the past) so the
        deleted occurrence falls inside admin/refresh-projections' actual
        regeneration window (today onward).
        """
        from app.models.recurrence import ProjectionExclusion

        task = Task(type="recurring", title="Daily task", importance=2, status="pending")
        db.add(task)
        db.commit()
        db.refresh(task)
        recurrence = Recurrence(
            task_id=task.id, interval_type="daily", interval_multiple=1, start_date=date.today(),
        )
        db.add(recurrence)
        db.commit()
        client.post("/admin/refresh-projections")

        target_date = date.today() + timedelta(days=3)
        assert db.query(Projection).filter(
            Projection.task_id == task.id, Projection.due_date == target_date
        ).first() is not None

        resp = client.delete(
            f"/tasks/week/projection/{task.id}",
            params={"date": target_date.isoformat()},
        )
        assert resp.status_code == 200
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task.id,
            ProjectionExclusion.due_date == target_date,
        ).first() is not None

        resp = client.post("/admin/refresh-projections")
        assert resp.status_code == 200

        resurrected = db.query(Projection).filter(
            Projection.task_id == task.id,
            Projection.due_date == target_date,
        ).first()
        assert resurrected is None, "deleted occurrence was resurrected by regeneration"

    def test_delete_projection_writes_action_log_and_undo_restores_it(self, client, db):
        """
        Review finding 7: the week-view single-occurrence delete
        (DELETE /tasks/week/projection/{task_id}) wrote a tombstone but no
        ActionLog and offered no undo, unlike its sibling
        /{task_id}/delete-instance which does the identical thing WITH
        undo. Fix: give this route the same ActionLog + undo treatment —
        undo should restore the projection and clear the tombstone.
        """
        from app.models.action_log import ActionLog
        from app.models.recurrence import ProjectionExclusion

        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        target = db.query(Projection).filter(Projection.task_id == task_id).first()
        target_date = target.due_date

        resp = client.delete(
            f"/tasks/week/projection/{task_id}",
            params={"date": target_date.isoformat()},
        )
        assert resp.status_code == 200
        assert "showUndo" in resp.headers.get("hx-trigger", "")

        log = db.query(ActionLog).filter(
            ActionLog.task_id == task_id,
            ActionLog.action_type == "delete_instance",
        ).first()
        assert log is not None, "week-view delete must write an ActionLog entry"
        assert target_date.isoformat() in log.projections_snapshot

        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == target_date
        ).first() is None
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id, ProjectionExclusion.due_date == target_date
        ).first() is not None

        undo_resp = client.post(f"/undo/{log.id}")
        assert undo_resp.status_code == 200

        assert db.query(Projection).filter(
            Projection.task_id == task_id, Projection.due_date == target_date
        ).first() is not None, "undo must restore the deleted projection"
        assert db.query(ProjectionExclusion).filter(
            ProjectionExclusion.task_id == task_id, ProjectionExclusion.due_date == target_date
        ).first() is None, "undo must clear the tombstone"


# ---------------------------------------------------------------------------
# Defer sets snooze
# ---------------------------------------------------------------------------

class TestDeferSetsSnooze:
    def test_defer_errand_sets_snooze_until_tomorrow(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.post(f"/tasks/{task_id}/defer")
        assert resp.status_code == 200

        task = db.query(Task).filter(Task.id == task_id).first()
        db.refresh(task)
        assert task.snooze_until == str(date.today() + timedelta(days=1))
        assert task.deferred_count == 1


# ---------------------------------------------------------------------------
# Variable recurring with allowed days
# ---------------------------------------------------------------------------

class TestVariableRecurringAllowedDays:
    def _make_variable_recurring_with_allowed_days(self, client, db, allowed_days=None):
        payload = {
            "type": "variable_recurring",
            "title": "Dentist",
            "importance": 2,
            "urgency": 2,
            "estimated_duration": 60,
            "recurrence": {
                "interval_type": "monthly",
                "interval_multiple": 1,
                "start_date": "2026-01-01T00:00:00",
            },
        }
        if allowed_days is not None:
            payload["allowed_days"] = allowed_days
        return create_task(client, payload)

    def test_allowed_days_shifts_to_next_allowed(self, client, db):
        # allowed_days="0" means Sunday (stored as 0, python weekday 6)
        data = self._make_variable_recurring_with_allowed_days(client, db, allowed_days="0")
        task_id = data["id"]
        add_todays_projection(db, task_id)

        resp = client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 1},
        )
        assert resp.status_code == 200

        proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date > date.today(),
        ).order_by(Projection.due_date).first()
        assert proj is not None
        assert proj.due_date.weekday() == 6  # Sunday in Python

    def test_without_allowed_days_exact_days(self, client, db):
        data = self._make_variable_recurring_with_allowed_days(client, db)
        task_id = data["id"]
        add_todays_projection(db, task_id)

        resp = client.post(
            f"/tasks/{task_id}/complete/variable",
            data={"days_until_next": 5},
        )
        assert resp.status_code == 200

        expected_date = date.today() + timedelta(days=5)
        proj = db.query(Projection).filter(
            Projection.task_id == task_id,
            Projection.due_date == expected_date,
        ).first()
        assert proj is not None


# ---------------------------------------------------------------------------
# Preset CRUD
# ---------------------------------------------------------------------------

class TestPresetCRUD:
    def test_create_preset(self, client, db):
        resp = client.post("/presets/", json=PRESET_PAYLOAD)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Weekly Standup"
        assert data["title"] == "Team Standup"

        preset = db.query(TaskPreset).filter(TaskPreset.name == "Weekly Standup").first()
        assert preset is not None

    def test_get_preset_by_id(self, client, db):
        created = client.post("/presets/", json=PRESET_PAYLOAD).json()
        preset_id = created["id"]

        resp = client.get(f"/presets/{preset_id}")
        assert resp.status_code == 200
        assert resp.json()["name"] == "Weekly Standup"

    def test_delete_preset(self, client, db):
        created = client.post("/presets/", json=PRESET_PAYLOAD).json()
        preset_id = created["id"]

        resp = client.delete(f"/presets/{preset_id}")
        assert resp.status_code == 200

        assert db.query(TaskPreset).filter(TaskPreset.id == preset_id).first() is None

    def test_get_nonexistent_preset_returns_404(self, client, db):
        resp = client.get("/presets/999")
        assert resp.status_code == 404

    def test_list_presets_ordered_by_name(self, client, db):
        client.post("/presets/", json={**PRESET_PAYLOAD, "name": "Zebra"})
        client.post("/presets/", json={**PRESET_PAYLOAD, "name": "Alpha"})

        resp = client.get("/presets/")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert names.index("Alpha") < names.index("Zebra")


# ---------------------------------------------------------------------------
# Task updates
# ---------------------------------------------------------------------------

class TestUpdateTask:
    def test_update_errand_fields(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(f"/tasks/{task_id}", json={"title": "Buy bread", "importance": 3})
        assert resp.status_code == 200
        body = resp.json()
        assert body["title"] == "Buy bread"
        assert body["importance"] == 3
        assert body["urgency"] == ERRAND_PAYLOAD["urgency"]  # unchanged

    def test_update_recurring_task_recurrence(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        resp = client.put(
            f"/tasks/{task_id}",
            json={"recurrence": {"interval_type": "weekly", "interval_multiple": 1, "start_date": "2026-01-01T00:00:00"}},
        )
        assert resp.status_code == 200

        recurrence = db.query(Recurrence).filter(Recurrence.task_id == task_id).first()
        db.refresh(recurrence)
        assert recurrence.interval_type == "weekly"

    def test_update_nonexistent_task_returns_404(self, client, db):
        resp = client.put("/tasks/does-not-exist", json={"title": "x"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Update task: type change (regression tests for the "editing type silently
# fails" bug — see _replace_task_for_type_change in app/routers/tasks.py)
# ---------------------------------------------------------------------------

class TestUpdateTaskTypeChange:
    def test_change_errand_to_deadline_creates_new_task_and_removes_old(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        old_id = data["id"]

        resp = client.put(
            f"/tasks/{old_id}",
            json={
                "type": "deadline",
                "title": "Buy milk",
                "importance": 2,
                "deadline_at": "2099-05-01T12:00:00",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["type"] == "deadline"
        assert body["deadline_at"].startswith("2099-05-01")
        new_id = body["id"]
        assert new_id != old_id

        assert db.query(Task).filter(Task.id == old_id).first() is None
        assert db.query(Task).filter(Task.id == new_id).first() is not None

    def test_change_to_appointment_without_scheduled_at_returns_422_and_leaves_task_untouched(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(f"/tasks/{task_id}", json={"type": "appointment", "title": "Buy milk", "importance": 2})
        assert resp.status_code == 422

        # Original task must be completely unaffected by the rejected change.
        task = db.query(Task).filter(Task.id == task_id).first()
        assert task is not None
        assert task.type == "errand"

    def test_change_to_deadline_without_deadline_at_returns_422(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(f"/tasks/{task_id}", json={"type": "deadline", "title": "x", "importance": 2})
        assert resp.status_code == 422
        assert db.query(Task).filter(Task.id == task_id).first().type == "errand"

    def test_change_to_recurring_without_recurrence_returns_422(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(f"/tasks/{task_id}", json={"type": "recurring", "title": "x", "importance": 2})
        assert resp.status_code == 422
        assert db.query(Task).filter(Task.id == task_id).first().type == "errand"

    def test_change_to_recurring_generates_projections(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(
            f"/tasks/{task_id}",
            json={
                "type": "recurring",
                "title": "Daily stretch",
                "importance": 2,
                "urgency": 2,
                "recurrence": {
                    "interval_type": "daily",
                    "interval_multiple": 1,
                    "start_date": date.today().isoformat() + "T00:00:00",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        recurrence = db.query(Recurrence).filter(Recurrence.task_id == new_id).first()
        assert recurrence is not None
        projections = db.query(Projection).filter(Projection.task_id == new_id).all()
        assert len(projections) > 0, "recurring type change must generate projections, not just a bare Recurrence row"

    def test_change_away_from_recurring_leaves_no_stale_projection(self, client, db):
        """
        The duplicate-appearance half of the bug: switching a recurring task
        to another type must not leave its old Recurrence/Projection rows
        around, since get_fixed_tasks/get_flexible_tasks match projections
        to tasks by id alone (no type filter) and would otherwise show the
        task a second time under its old recurring identity.
        """
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        assert db.query(Projection).filter(Projection.task_id == task_id).count() > 0

        resp = client.put(
            f"/tasks/{task_id}",
            json={"type": "errand", "title": "Morning walk", "importance": 2, "urgency": 2},
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        # Nothing left keyed to the old (deleted) task id.
        assert db.query(Projection).filter(Projection.task_id == task_id).count() == 0
        assert db.query(Recurrence).filter(Recurrence.task_id == task_id).count() == 0
        # And the new task's own id has no projections either (it's an errand now).
        assert db.query(Projection).filter(Projection.task_id == new_id).count() == 0

    def test_type_change_preserves_tags(self, client, db):
        from app.models.tag import Tag, TaskTag

        tag = Tag(name="chores", icon="tag", color="#7eb8d4")
        db.add(tag)
        db.commit()
        db.refresh(tag)

        data = create_task(client, {**ERRAND_PAYLOAD, "tag_ids": [tag.id]})
        task_id = data["id"]
        assert db.query(TaskTag).filter(TaskTag.task_id == task_id).count() == 1

        resp = client.put(
            f"/tasks/{task_id}",
            json={"type": "deadline", "title": "x", "importance": 2, "deadline_at": "2099-05-01T12:00:00"},
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        new_tags = db.query(TaskTag).filter(TaskTag.task_id == new_id).all()
        assert [t.tag_id for t in new_tags] == [tag.id]
        assert db.query(TaskTag).filter(TaskTag.task_id == task_id).count() == 0

    def test_type_change_no_undo_header(self, client, db):
        """
        Documented limitation: a type-change edit doesn't offer undo (see
        _replace_task_for_type_change docstring), so no X-Undo-Log-Id header
        should be present — showing an undo toast that doesn't work would be
        worse than not offering one.
        """
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(
            f"/tasks/{task_id}",
            json={"type": "deadline", "title": "x", "importance": 2, "deadline_at": "2099-05-01T12:00:00"},
        )
        assert resp.status_code == 200
        assert "X-Undo-Log-Id" not in resp.headers

    def test_same_type_update_unaffected_by_type_change_path(self, client, db):
        """Sanity check: submitting the same type is still a normal in-place edit."""
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        resp = client.put(f"/tasks/{task_id}", json={"type": "errand", "title": "Buy bread"})
        assert resp.status_code == 200
        assert resp.json()["id"] == task_id
        assert resp.json()["title"] == "Buy bread"

    def test_type_change_preserves_snooze_and_deferred_count(self, client, db):
        """
        Review finding 1: _replace_task_for_type_change's Task(...) call
        omitted snooze_until/deferred_count/manual_scheduled_time, so a
        snoozed/deferred task reappeared in today's list and lost its defer
        weight the moment its type was edited.
        """
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        # Defer twice: errands have no projection, so this sets snooze_until
        # and bumps deferred_count.
        client.post(f"/tasks/{task_id}/defer")
        client.post(f"/tasks/{task_id}/defer")

        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.deferred_count == 2
        assert task.snooze_until == str(date.today() + timedelta(days=1))

        resp = client.put(
            f"/tasks/{task_id}",
            json={"type": "deadline", "title": "x", "importance": 2, "deadline_at": "2099-05-01T12:00:00"},
        )
        assert resp.status_code == 200, resp.text
        new_id = resp.json()["id"]

        new_task = db.query(Task).filter(Task.id == new_id).first()
        assert new_task.deferred_count == 2
        assert new_task.snooze_until == str(date.today() + timedelta(days=1))

    def test_type_change_preserves_manual_scheduled_time(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        client.post(
            "/tasks/timeline/reorder",
            json={"tasks": [{"task_id": task_id, "scheduled_hour": 14, "scheduled_minute": 30}]},
        )
        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.manual_scheduled_time is not None

        resp = client.put(
            f"/tasks/{task_id}",
            json={"type": "deadline", "title": "x", "importance": 2, "deadline_at": "2099-05-01T12:00:00"},
        )
        assert resp.status_code == 200, resp.text
        new_task = db.query(Task).filter(Task.id == resp.json()["id"]).first()
        assert new_task.manual_scheduled_time is not None
        assert new_task.manual_scheduled_time.hour == 14
        assert new_task.manual_scheduled_time.minute == 30

    def test_undo_after_type_change_does_not_resurrect_old_task(self, client, db):
        """
        Review finding 4: completing a recurring task logs an undoable
        "complete" action against its id. If the task's type is then edited
        (which replaces it with a new id via _replace_task_for_type_change),
        the old ActionLog entry used to survive — clicking its undo toast
        would recreate the old task from its snapshot, leaving two live
        tasks behind (old_id resurrected + new_id from the type change).
        """
        data = create_task(client, RECURRING_PAYLOAD)
        old_id = data["id"]
        add_todays_projection(db, old_id)

        complete_resp = client.post(f"/tasks/{old_id}/complete")
        import json as _json
        log_id = _json.loads(complete_resp.headers["hx-trigger"])["showUndo"]["ids"][0]

        change_resp = client.put(
            f"/tasks/{old_id}",
            json={"type": "errand", "title": "Morning walk", "importance": 2, "urgency": 2},
        )
        assert change_resp.status_code == 200, change_resp.text
        new_id = change_resp.json()["id"]

        undo_resp = client.post(f"/undo/{log_id}")
        assert undo_resp.status_code == 404, "the stale undo entry should have been invalidated by the type change"

        assert db.query(Task).filter(Task.id == old_id).first() is None
        assert db.query(Task).filter(Task.id == new_id).first() is not None
        assert db.query(Task).count() == 1


# ---------------------------------------------------------------------------
# Admin: refresh projections
# ---------------------------------------------------------------------------

class TestAdminRefreshProjections:
    def test_refresh_projections_creates_projections(self, client, db):
        task = Task(type="recurring", title="Daily Task", importance=2, status="pending")
        db.add(task)
        db.commit()
        db.refresh(task)

        recurrence = Recurrence(
            task_id=task.id,
            interval_type="daily",
            interval_multiple=1,
            start_date=date.today(),
        )
        db.add(recurrence)
        db.commit()

        resp = client.post("/admin/refresh-projections")
        assert resp.status_code == 200
        assert resp.json()["projections_created"] > 0

        projections = db.query(Projection).filter(Projection.task_id == task.id).all()
        assert len(projections) > 0


# ---------------------------------------------------------------------------
# Admin: task CRUD
# ---------------------------------------------------------------------------

class TestAdminTaskCRUD:
    def test_admin_create_recurring_task(self, client, db):
        resp = client.post(
            "/admin/tasks",
            data={
                "title": "Daily Walk",
                "type": "recurring",
                "importance": "2",
                "estimated_duration": "30",
                "interval_type": "daily",
                "start_date": "2026-01-01",
                "interval_multiple": "1",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        task = db.query(Task).filter(Task.title == "Daily Walk").first()
        assert task is not None

        recurrence = db.query(Recurrence).filter(Recurrence.task_id == task.id).first()
        assert recurrence is not None
        assert recurrence.interval_type == "daily"

        projections = db.query(Projection).filter(Projection.task_id == task.id).all()
        assert len(projections) > 0

    def test_admin_update_task(self, client, db):
        task = Task(
            type="errand",
            title="Old Title",
            importance=2,
            urgency=1,
            estimated_duration=15,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        resp = client.post(
            f"/admin/tasks/{task.id}",
            data={"title": "New Title", "type": "errand", "importance": "3"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        db.refresh(task)
        assert task.title == "New Title"
        assert task.importance == 3

    def test_admin_delete_task(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]

        resp = client.delete(f"/admin/tasks/{task_id}")
        assert resp.status_code == 200

        assert db.query(Task).filter(Task.id == task_id).first() is None
        assert db.query(Recurrence).filter(Recurrence.task_id == task_id).count() == 0
        assert db.query(Projection).filter(Projection.task_id == task_id).count() == 0

    def test_admin_update_away_from_recurring_clears_exclusions(self, client, db):
        """
        Review finding 6: admin_task_update's "switched away from recurring"
        branch deleted Recurrence + Projection but not ProjectionExclusion,
        leaving orphan tombstones that would silently suppress those dates
        if the task were ever made recurring again.
        """
        from app.models.recurrence import ProjectionExclusion

        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        db.add(ProjectionExclusion(task_id=task_id, due_date=date.today() + timedelta(days=3)))
        db.commit()
        assert db.query(ProjectionExclusion).filter(ProjectionExclusion.task_id == task_id).count() == 1

        resp = client.post(
            f"/admin/tasks/{task_id}",
            data={"title": "Morning walk", "type": "errand", "importance": "2"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        assert db.query(ProjectionExclusion).filter(ProjectionExclusion.task_id == task_id).count() == 0
        assert db.query(Recurrence).filter(Recurrence.task_id == task_id).count() == 0
        assert db.query(Projection).filter(Projection.task_id == task_id).count() == 0

    def test_admin_task_list_refreshes_via_taskupdated_listener(self, client, db):
        """
        Review finding 2: the admin tasks page's Done/Defer/Delete buttons
        didn't all fire a taskUpdated trigger, AND the page had no
        `taskUpdated from:body` listener at all — so even a button that did
        fire the event had nothing wired up to react to it. Full-page loads
        get the whole page (with the listener wired to #task-list); an
        htmx-driven request (as the listener itself makes) gets just the
        row fragment back.
        """
        create_task(client, ERRAND_PAYLOAD)

        full_page = client.get("/admin/tasks")
        assert full_page.status_code == 200
        assert "taskUpdated from:body" in full_page.text
        assert "<html" in full_page.text.lower()

        fragment = client.get("/admin/tasks", headers={"HX-Request": "true"})
        assert fragment.status_code == 200
        assert "<html" not in fragment.text.lower()
        assert "Buy milk" in fragment.text

    def test_completed_tasks_history(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]
        client.post(f"/tasks/{task_id}/complete")

        today_str = date.today().isoformat()
        resp = client.get(f"/admin/completed-tasks?from_date={today_str}&to_date={today_str}")
        assert resp.status_code == 200

        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None

    def test_completed_task_snapshot_survives_task_deletion(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]
        client.post(f"/tasks/{task_id}/complete")

        # errand is deleted on completion — verify snapshot is still intact
        assert db.query(Task).filter(Task.id == task_id).first() is None
        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed.task_title == "Buy milk"
        assert completed.task_type == "errand"


# ---------------------------------------------------------------------------
# Task lifecycle edge cases
# ---------------------------------------------------------------------------

class TestTaskLifecycleEdgeCases:
    def test_complete_already_deleted_task_returns_404(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        client.post(f"/tasks/{task_id}/complete")
        resp = client.post(f"/tasks/{task_id}/complete")
        assert resp.status_code == 404

    def test_defer_recurring_no_today_projection(self, client, db):
        task = Task(
            type="recurring",
            title="Walk",
            importance=2,
            urgency=1,
            estimated_duration=30,
            status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        resp = client.post(f"/tasks/{task.id}/defer")
        assert resp.status_code == 200

        db.refresh(task)
        assert task.deferred_count == 1
        assert task.snooze_until is not None

    def test_create_appointment_without_scheduled_at(self, client, db):
        resp = client.post("/tasks/", json={"type": "appointment", "title": "No time", "importance": 2})
        assert resp.status_code == 200
        assert resp.json()["type"] == "appointment"

    def test_create_deadline_without_deadline_at(self, client, db):
        resp = client.post("/tasks/", json={"type": "deadline", "title": "No deadline", "importance": 2})
        assert resp.status_code == 200
        assert resp.json()["type"] == "deadline"


# ---------------------------------------------------------------------------
# Admin: muscle groups
# ---------------------------------------------------------------------------

class TestAdminMuscleGroups:
    def test_create_muscle_group(self, client, db):
        resp = client.post(
            "/admin/muscle-groups",
            data={"name": "Biceps", "recovery_time": 2},
        )
        assert resp.status_code == 200

        mg = db.query(MuscleGroup).filter(MuscleGroup.name == "Biceps").first()
        assert mg is not None
        assert mg.recovery_time == 2

    def test_update_muscle_group_recovery_time(self, client, db):
        mg = MuscleGroup(name="Quads", recovery_time=2)
        db.add(mg)
        db.commit()
        db.refresh(mg)

        resp = client.put(
            f"/admin/muscle-groups/{mg.id}",
            data={"recovery_time": 3},
        )
        assert resp.status_code == 200

        db.refresh(mg)
        assert mg.recovery_time == 3

    def test_delete_muscle_group_cascades(self, client, db):
        mg = MuscleGroup(name="Chest", recovery_time=2)
        db.add(mg)
        db.commit()
        db.refresh(mg)

        ex = Exercise(name="Press", intensity="heavy")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        db.add(ExerciseMuscle(exercise_id=ex.id, muscle_id=mg.id))
        db.commit()

        resp = client.delete(f"/admin/muscle-groups/{mg.id}")
        assert resp.status_code == 200

        assert db.query(MuscleGroup).filter(MuscleGroup.id == mg.id).first() is None
        assert db.query(ExerciseMuscle).filter(ExerciseMuscle.muscle_id == mg.id).count() == 0


# ---------------------------------------------------------------------------
# Admin: exercises
# ---------------------------------------------------------------------------

class TestAdminExercises:
    def test_create_exercise_with_muscles(self, client, db):
        mg1 = MuscleGroup(name="Chest", recovery_time=2)
        mg2 = MuscleGroup(name="Triceps", recovery_time=2)
        db.add_all([mg1, mg2])
        db.commit()
        db.refresh(mg1)
        db.refresh(mg2)

        resp = client.post(
            "/admin/exercises",
            data={"name": "Bench Press", "intensity": "heavy", "muscle_ids": [mg1.id, mg2.id]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        ex = db.query(Exercise).filter(Exercise.name == "Bench Press").first()
        assert ex is not None
        assert db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == ex.id).count() == 2

    def test_update_exercise_changes_muscles(self, client, db):
        mg1 = MuscleGroup(name="Chest", recovery_time=2)
        mg2 = MuscleGroup(name="Shoulders", recovery_time=2)
        db.add_all([mg1, mg2])
        db.commit()
        db.refresh(mg1)
        db.refresh(mg2)

        ex = Exercise(name="Press", intensity="heavy")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        db.add(ExerciseMuscle(exercise_id=ex.id, muscle_id=mg1.id))
        db.commit()

        resp = client.post(
            f"/admin/exercises/{ex.id}",
            data={"name": "Press", "intensity": "heavy", "muscle_ids": [mg2.id]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        ems = db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == ex.id).all()
        assert len(ems) == 1
        assert ems[0].muscle_id == mg2.id

    def test_delete_exercise_cascades(self, client, db):
        mg = MuscleGroup(name="Back", recovery_time=2)
        db.add(mg)
        db.commit()
        db.refresh(mg)

        ex = Exercise(name="Pull-up", intensity="heavy")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        db.add(ExerciseMuscle(exercise_id=ex.id, muscle_id=mg.id))
        db.commit()

        resp = client.delete(f"/admin/exercises/{ex.id}")
        assert resp.status_code == 200

        assert db.query(Exercise).filter(Exercise.id == ex.id).first() is None
        assert db.query(ExerciseMuscle).filter(ExerciseMuscle.exercise_id == ex.id).count() == 0


# ---------------------------------------------------------------------------
# Admin: workout history
# ---------------------------------------------------------------------------

class TestAdminWorkoutHistory:
    def test_add_workout_history(self, client, db):
        ex = Exercise(name="Squat", intensity="heavy")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        resp = client.post(
            "/admin/workout-history/add",
            data={
                "exercise_id": ex.id,
                "performed_date": "2026-01-01",
                "reps": 10,
                "weight_kg": 60.0,
                "num_sets": 3,
                "intensity": "heavy",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        performed = db.query(PerformedSet).filter(PerformedSet.exercise_id == ex.id).first()
        assert performed is not None
        assert performed.reps == 10

    def test_delete_workout_history(self, client, db):
        ex = Exercise(name="Deadlift", intensity="heavy")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        ps = PerformedSet(exercise_id=ex.id, reps=5, weight_kg=100.0, num_sets=3, intensity="heavy")
        db.add(ps)
        db.commit()
        db.refresh(ps)

        resp = client.delete(f"/admin/workout-history/{ps.id}")
        assert resp.status_code == 200

        assert db.query(PerformedSet).filter(PerformedSet.id == ps.id).first() is None

    def test_edit_workout_history(self, client, db):
        ex = Exercise(name="Row", intensity="light")
        db.add(ex)
        db.commit()
        db.refresh(ex)

        ps = PerformedSet(exercise_id=ex.id, reps=8, weight_kg=50.0, num_sets=3, intensity="heavy")
        db.add(ps)
        db.commit()
        db.refresh(ps)

        resp = client.post(
            f"/admin/workout-history/{ps.id}",
            data={
                "exercise_id": ex.id,
                "num_sets": 4,
                "reps": 12,
                "weight_kg": 70.0,
                "intensity": "light",
            },
        )
        assert resp.status_code == 200

        db.refresh(ps)
        assert ps.reps == 12
        assert ps.weight_kg == 70.0
        assert ps.num_sets == 4
        assert ps.intensity == "light"


# ---------------------------------------------------------------------------
# Scenario-level tests
# ---------------------------------------------------------------------------

class TestFullDaySchedulingScenario:
    def test_full_day_mix_of_task_types(self, client, db):
        appt1 = Task(
            type="appointment", title="Morning Meeting",
            scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 10, 0),
            estimated_duration=60, importance=3, allow_afternoon=False, status="pending",
        )
        appt2 = Task(
            type="appointment", title="Afternoon Call",
            scheduled_at=datetime(FUTURE_DATE.year, FUTURE_DATE.month, FUTURE_DATE.day, 14, 0),
            estimated_duration=60, importance=2, allow_afternoon=False, status="pending",
        )
        recurring = Task(
            type="recurring", title="Standup",
            scheduled_time=time(9, 30),
            estimated_duration=15, importance=2, urgency=2,
            allow_afternoon=False, status="pending",
        )
        errand1 = Task(
            type="errand", title="E1",
            estimated_duration=30, importance=2, urgency=2,
            allow_afternoon=False, status="pending",
        )
        errand2 = Task(
            type="errand", title="E2",
            estimated_duration=30, importance=1, urgency=1,
            allow_afternoon=False, status="pending",
        )
        deadline = Task(
            type="deadline", title="Report",
            deadline_at=datetime(2099, 6, 1, 17, 0),
            estimated_duration=60, importance=3,
            allow_afternoon=False, status="pending",
        )

        db.add_all([appt1, appt2, recurring, errand1, errand2, deadline])
        db.commit()
        db.refresh(recurring)

        rec_rule = Recurrence(
            task_id=recurring.id,
            interval_type="daily",
            interval_multiple=1,
            start_date=FUTURE_DATE,
        )
        db.add(rec_rule)
        db.add(Projection(task_id=recurring.id, due_date=FUTURE_DATE))
        db.commit()

        schedule = build_daily_schedule(db, FUTURE_DATE)

        assert len(schedule) >= 5

        meeting = next((s for s in schedule if s.task.title == "Morning Meeting"), None)
        assert meeting is not None
        assert meeting.start_time == time(10, 0)

        call = next((s for s in schedule if s.task.title == "Afternoon Call"), None)
        assert call is not None
        assert call.start_time == time(14, 0)

        # No two tasks should have overlapping time slots
        for i, a in enumerate(schedule):
            for b in schedule[i + 1:]:
                overlap = a.start_time < b.end_time and b.start_time < a.end_time
                assert not overlap, f"{a.task.title} ({a.start_time}-{a.end_time}) overlaps {b.task.title} ({b.start_time}-{b.end_time})"

    def test_week_view_returns_tasks_in_range(self, client, db):
        task = Task(
            type="recurring", title="Weekly Review",
            importance=2, urgency=1, estimated_duration=30,
            allow_afternoon=False, status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)

        db.add(Projection(task_id=task.id, due_date=date(2099, 1, 12)))

        appt = Task(
            type="appointment", title="Budget Meeting",
            scheduled_at=datetime(2099, 1, 14, 10, 0),
            estimated_duration=60, importance=3, status="pending",
        )
        db.add(appt)
        db.commit()

        resp = client.get("/tasks/week?start=2099-01-10&end=2099-01-16")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

        titles = [t["title"] for t in data]
        assert "Weekly Review" in titles
        assert "Budget Meeting" in titles

    def test_week_view_today_mirrors_live_list(self, client, db):
        """
        When today falls inside the requested range, today's column should
        show exactly the daily live list — the same task set that "/"
        renders via get_prioritised_tasks_with_metadata — rather than the
        plain appointment+projection query (which never surfaces errands
        or deadlines).

        Asserted time-independently: gap scheduling depends on the time of
        day (an evening run may drop flexible tasks like the errand), so
        whatever the algorithm yields right now, the week JSON must match
        it. No assertion depends on any specific flexible fixture actually
        surviving prioritisation.
        """
        from app.services.prioritisation import get_prioritised_tasks_with_metadata

        today = date.today()

        errand = Task(
            type="errand", title="Buy milk",
            importance=2, urgency=2, estimated_duration=15,
            status="pending",
        )
        deadline = Task(
            type="deadline", title="Submit report",
            deadline_at=datetime.combine(today, time(23, 59)),
            estimated_duration=30, importance=3, status="pending",
        )
        recurring = Task(
            type="recurring", title="Morning Standup",
            scheduled_time=time(9, 30),
            importance=2, urgency=2, estimated_duration=15,
            allow_afternoon=False, status="pending",
        )
        db.add_all([errand, deadline, recurring])
        db.commit()
        db.refresh(recurring)
        recurring_id = recurring.id
        db.add(Projection(task_id=recurring.id, due_date=today))
        db.commit()

        start = today.isoformat()
        end = (today + timedelta(days=6)).isoformat()
        resp = client.get(f"/tasks/week?start={start}&end={end}")
        assert resp.status_code == 200
        data = resp.json()

        today_entries = [
            t for t in data if t["scheduled_at"].split("T")[0] == today.isoformat()
        ]

        # Compute the live list AFTER the week request so both sides see
        # post-auto-complete-sweep state. Compare task-id sets (not titles
        # or scheduled times) so crossing a minute boundary between the two
        # calls can't skew the comparison.
        scheduled, _ = get_prioritised_tasks_with_metadata(db, today)
        live_ids = {pt.task.id for pt in scheduled}

        week_today_ids = [t["id"] for t in today_entries]
        assert len(week_today_ids) == len(set(week_today_ids))  # no duplicates
        assert set(week_today_ids) == live_ids

        # The time-bound recurring task is fixed — always in the live list
        # regardless of time of day — proving the id-set comparison isn't
        # vacuously comparing two empty sets.
        assert recurring_id in live_ids

        # projection_date convention: only projection-backed types carry it
        # (the frontend uses its presence to choose single-occurrence delete
        # vs whole-task delete); errands/deadlines/appointments must not.
        for entry in today_entries:
            if entry["type"] in ("recurring", "variable_recurring", "workout"):
                assert entry.get("projection_date") == today.isoformat()
            else:
                assert "projection_date" not in entry

    def test_week_view_future_projection_unaffected_by_today_injection(self, client, db):
        today = date.today()
        future_day = today + timedelta(days=3)

        recurring = Task(
            type="recurring", title="Team Sync",
            importance=2, urgency=1, estimated_duration=30,
            allow_afternoon=False, status="pending",
        )
        db.add(recurring)
        db.commit()
        db.refresh(recurring)
        db.add(Projection(task_id=recurring.id, due_date=future_day))
        db.commit()

        start = today.isoformat()
        end = (today + timedelta(days=6)).isoformat()
        resp = client.get(f"/tasks/week?start={start}&end={end}")
        assert resp.status_code == 200
        data = resp.json()

        entries = [t for t in data if t["title"] == "Team Sync"]
        assert len(entries) == 1
        assert entries[0]["projection_date"] == future_day.isoformat()

    def test_week_view_no_injection_when_today_outside_range(self, client, db):
        """
        Today's live list (errands especially) must not leak into a week
        that doesn't include today — behaviour should be identical to the
        original appointment+projection-only query.
        """
        today = date.today()
        errand = Task(
            type="errand", title="Buy milk",
            importance=2, urgency=2, estimated_duration=15,
            status="pending",
        )
        db.add(errand)
        db.commit()

        start = (today + timedelta(days=7)).isoformat()
        end = (today + timedelta(days=13)).isoformat()
        resp = client.get(f"/tasks/week?start={start}&end={end}")
        assert resp.status_code == 200
        data = resp.json()

        assert data == []


# ---------------------------------------------------------------------------
# Auto-complete sweep for overdue appointments/deadlines
# ---------------------------------------------------------------------------

class TestAutoCompleteOverdueSweep:
    def _overdue_appointment(self, db, title="Yesterday's meeting"):
        task = Task(
            type="appointment", title=title,
            scheduled_at=datetime.combine(date.today() - timedelta(days=1), time(10, 0)),
            estimated_duration=60, importance=2, status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def _overdue_deadline(self, db, title="Last week's report"):
        task = Task(
            type="deadline", title=title,
            deadline_at=datetime.combine(date.today() - timedelta(days=1), time(17, 0)),
            estimated_duration=60, importance=2, status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def test_overdue_appointment_auto_completed_on_load(self, client, db):
        task = self._overdue_appointment(db)
        task_id = task.id

        resp = client.get("/tasks/")
        assert resp.status_code == 200

        assert db.query(Task).filter(Task.id == task_id).first() is None
        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None
        assert completed.auto_completed is True

        import json as _json
        trigger = _json.loads(resp.headers.get("hx-trigger", "{}"))
        assert "showUndo" in trigger
        assert "auto-completed" in trigger["showUndo"]["label"]

    def test_overdue_deadline_auto_completed_on_load(self, client, db):
        task = self._overdue_deadline(db)
        task_id = task.id

        resp = client.get("/tasks/")
        assert resp.status_code == 200

        assert db.query(Task).filter(Task.id == task_id).first() is None
        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None
        assert completed.auto_completed is True

    def test_deadline_due_today_not_auto_completed(self, client, db):
        task = Task(
            type="deadline", title="Due today",
            deadline_at=datetime.combine(date.today(), time(17, 0)),
            estimated_duration=60, importance=2, status="pending",
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        task_id = task.id

        resp = client.get("/tasks/")
        assert resp.status_code == 200

        assert db.query(Task).filter(Task.id == task_id).first() is not None
        assert db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first() is None

    def test_multiple_overdue_tasks_swept_with_combined_label(self, client, db):
        appt = self._overdue_appointment(db, title="Old meeting")
        deadline = self._overdue_deadline(db, title="Old report")

        resp = client.get("/tasks/")
        assert resp.status_code == 200

        import json as _json
        trigger = _json.loads(resp.headers.get("hx-trigger", "{}"))
        assert "2 tasks auto-completed (overdue)" in trigger["showUndo"]["label"]
        assert len(trigger["showUndo"]["ids"]) == 2

        assert db.query(Task).filter(Task.id == appt.id).first() is None
        assert db.query(Task).filter(Task.id == deadline.id).first() is None

    def test_undo_batch_restores_appointment_as_due_today(self, client, db):
        task = self._overdue_appointment(db)
        task_id = task.id

        resp = client.get("/tasks/")
        import json as _json
        trigger = _json.loads(resp.headers.get("hx-trigger", "{}"))
        log_id = trigger["showUndo"]["ids"][0]

        undo_resp = client.post(f"/undo/batch/{log_id}")
        assert undo_resp.status_code == 200

        restored = db.query(Task).filter(Task.id == task_id).first()
        assert restored is not None
        assert restored.scheduled_at.date() == date.today()
        assert db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first() is None

    def test_undo_single_restores_deadline_as_due_today(self, client, db):
        task = self._overdue_deadline(db)
        task_id = task.id

        resp = client.get("/tasks/")
        import json as _json
        trigger = _json.loads(resp.headers.get("hx-trigger", "{}"))
        log_id = trigger["showUndo"]["ids"][0]

        undo_resp = client.post(f"/undo/{log_id}")
        assert undo_resp.status_code == 200

        restored = db.query(Task).filter(Task.id == task_id).first()
        assert restored is not None
        assert restored.deadline_at.date() == date.today()
        assert db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first() is None


# ---------------------------------------------------------------------------
# Tag CRUD and task-tag associations
# ---------------------------------------------------------------------------

from app.models.tag import Tag, TaskTag

TAG_PAYLOAD = {"name": "Work", "icon": "briefcase", "color": "#7eb8d4"}
TAG_PAYLOAD_2 = {"name": "Health", "icon": "dumbbell", "color": "#8fbe8f"}


def create_tag(client, payload: dict) -> dict:
    resp = client.post("/admin/tags", data=payload)
    assert resp.status_code in (200, 303), resp.text
    # Follow redirect to get JSON; instead query DB directly in tests
    # Return the last created tag via GET
    resp2 = client.get("/admin/tags")
    assert resp2.status_code == 200
    return None  # Tags are returned via HTML; use DB queries in tests


class TestTagCRUD:
    def test_create_tag(self, client, db):
        resp = client.post("/admin/tags", data=TAG_PAYLOAD)
        assert resp.status_code in (200, 303)
        tag = db.query(Tag).filter(Tag.name == "Work").first()
        assert tag is not None
        assert tag.icon == "briefcase"
        assert tag.color == "#7eb8d4"

    def test_update_tag(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        tag = db.query(Tag).first()
        resp = client.post(f"/admin/tags/{tag.id}", data={"name": "Career", "icon": "building", "color": "#d4856a"})
        assert resp.status_code in (200, 303)
        db.refresh(tag)
        assert tag.name == "Career"
        assert tag.icon == "building"
        assert tag.color == "#d4856a"

    def test_delete_tag(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        tag = db.query(Tag).first()
        tag_id = tag.id
        resp = client.delete(f"/admin/tags/{tag_id}")
        assert resp.status_code == 200
        assert db.query(Tag).filter(Tag.id == tag_id).first() is None

    def test_delete_tag_removes_task_tag_rows(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        tag = db.query(Tag).first()

        task_data = {**ERRAND_PAYLOAD, "tag_ids": [tag.id]}
        task = create_task(client, task_data)
        task_id = task["id"]

        assert db.query(TaskTag).filter(TaskTag.task_id == task_id).count() == 1

        client.delete(f"/admin/tags/{tag.id}")
        assert db.query(TaskTag).filter(TaskTag.task_id == task_id).count() == 0

    def test_delete_nonexistent_tag_returns_404(self, client, db):
        resp = client.delete("/admin/tags/9999")
        assert resp.status_code == 404


class TestTaskTagAssociation:
    def test_create_task_with_tags(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        tag = db.query(Tag).first()

        task_data = {**ERRAND_PAYLOAD, "tag_ids": [tag.id]}
        task = create_task(client, task_data)

        associations = db.query(TaskTag).filter(TaskTag.task_id == task["id"]).all()
        assert len(associations) == 1
        assert associations[0].tag_id == tag.id

    def test_create_task_with_multiple_tags(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        client.post("/admin/tags", data=TAG_PAYLOAD_2)
        tags = db.query(Tag).all()
        assert len(tags) == 2

        tag_ids = [t.id for t in tags]
        task_data = {**ERRAND_PAYLOAD, "tag_ids": tag_ids}
        task = create_task(client, task_data)

        associations = db.query(TaskTag).filter(TaskTag.task_id == task["id"]).all()
        assert len(associations) == 2

    def test_create_task_without_tags(self, client, db):
        task = create_task(client, ERRAND_PAYLOAD)
        associations = db.query(TaskTag).filter(TaskTag.task_id == task["id"]).all()
        assert len(associations) == 0

    def test_update_task_replaces_tags(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        client.post("/admin/tags", data=TAG_PAYLOAD_2)
        tags = db.query(Tag).all()
        tag1, tag2 = tags[0], tags[1]

        task = create_task(client, {**ERRAND_PAYLOAD, "tag_ids": [tag1.id]})
        task_id = task["id"]

        resp = client.put(f"/tasks/{task_id}", json={"tag_ids": [tag2.id]})
        assert resp.status_code == 200

        associations = db.query(TaskTag).filter(TaskTag.task_id == task_id).all()
        assert len(associations) == 1
        assert associations[0].tag_id == tag2.id

    def test_update_task_clears_tags_with_empty_list(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        tag = db.query(Tag).first()

        task = create_task(client, {**ERRAND_PAYLOAD, "tag_ids": [tag.id]})
        task_id = task["id"]

        resp = client.put(f"/tasks/{task_id}", json={"tag_ids": []})
        assert resp.status_code == 200

        assert db.query(TaskTag).filter(TaskTag.task_id == task_id).count() == 0

    def test_update_task_without_tag_ids_preserves_tags(self, client, db):
        client.post("/admin/tags", data=TAG_PAYLOAD)
        tag = db.query(Tag).first()

        task = create_task(client, {**ERRAND_PAYLOAD, "tag_ids": [tag.id]})
        task_id = task["id"]

        # Update title only — no tag_ids in payload
        resp = client.put(f"/tasks/{task_id}", json={"title": "Updated title"})
        assert resp.status_code == 200

        assert db.query(TaskTag).filter(TaskTag.task_id == task_id).count() == 1


# ---------------------------------------------------------------------------
# Theme A, component A4 — errand auto-deadlines: creation, migration,
# sweep behaviour, and the half-life prompt endpoints/banner.
# ---------------------------------------------------------------------------

from app.config import settings
from app.database import run_migrations
from app.routers.tasks import auto_complete_overdue_tasks, get_errands_due_for_prompt


class TestErrandAutoDeadlineCreation:
    def test_new_errand_gets_auto_deadline(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)

        assert data["deadline_auto"] is True
        assert data["deadline_at"] is not None
        deadline = datetime.fromisoformat(data["deadline_at"])
        expected = date.today() + timedelta(days=settings.errand_auto_deadline_days)
        # Allow a day of slack for midnight-crossing test runs
        assert abs((deadline.date() - expected).days) <= 1

    def test_explicit_errand_deadline_respected_as_user_confirmed(self, client, db):
        payload = {**ERRAND_PAYLOAD, "deadline_at": "2099-05-01T23:59:00"}
        data = create_task(client, payload)

        assert data["deadline_auto"] is False
        assert datetime.fromisoformat(data["deadline_at"]).date() == date(2099, 5, 1)

    def test_non_errand_types_unaffected(self, client, db):
        data = create_task(client, DEADLINE_PAYLOAD)
        assert data["deadline_auto"] is False
        assert datetime.fromisoformat(data["deadline_at"]).date() == date(2099, 4, 15)

        data = create_task(client, APPOINTMENT_PAYLOAD)
        assert data["deadline_at"] is None


class TestErrandAutoDeadlineMigration:
    def _add_dateless_errand(self, db, created_days_ago: int) -> str:
        task = Task(
            type="errand",
            title=f"Old errand {created_days_ago}",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            deadline_at=None,
            created_at=datetime.now() - timedelta(days=created_days_ago),
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task.id

    def test_migration_dates_old_errand_at_today_plus_30_floor(self, db):
        # created 400 days ago: created+365 is already past, so the
        # today+30 floor wins — old stock moves but nothing is instantly due.
        task_id = self._add_dateless_errand(db, created_days_ago=400)

        run_migrations(db.get_bind())
        db.expire_all()

        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.deadline_at is not None
        assert bool(task.deadline_auto) is True
        assert abs((task.deadline_at.date() - (date.today() + timedelta(days=30))).days) <= 1

    def test_migration_dates_recent_errand_at_created_plus_365(self, db):
        task_id = self._add_dateless_errand(db, created_days_ago=10)

        run_migrations(db.get_bind())
        db.expire_all()

        task = db.query(Task).filter(Task.id == task_id).first()
        expected = date.today() - timedelta(days=10) + timedelta(days=365)
        assert abs((task.deadline_at.date() - expected).days) <= 1

    def test_migration_is_idempotent(self, db):
        task_id = self._add_dateless_errand(db, created_days_ago=100)

        run_migrations(db.get_bind())
        db.expire_all()
        first_deadline = db.query(Task).filter(Task.id == task_id).first().deadline_at

        run_migrations(db.get_bind())
        db.expire_all()
        second_deadline = db.query(Task).filter(Task.id == task_id).first().deadline_at

        assert first_deadline == second_deadline

    def test_migration_leaves_dated_errands_alone(self, db):
        chosen = datetime(2099, 5, 1, 23, 59)
        task = Task(
            type="errand",
            title="Already dated",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            deadline_at=chosen,
            deadline_auto=False,
        )
        db.add(task)
        db.commit()
        task_id = task.id

        run_migrations(db.get_bind())
        db.expire_all()

        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.deadline_at == chosen
        assert bool(task.deadline_auto) is False


class TestErrandSweepBehaviour:
    def _add_errand(self, db, title, deadline_at, deadline_auto):
        task = Task(
            type="errand",
            title=title,
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            deadline_at=deadline_at,
            deadline_auto=deadline_auto,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def test_expired_auto_deadline_errand_not_swept(self, db):
        task = self._add_errand(
            db, "Expired auto", datetime.now() - timedelta(days=30), deadline_auto=True
        )

        swept = auto_complete_overdue_tasks(db)

        assert swept == []
        assert db.query(Task).filter(Task.id == task.id).first() is not None
        assert db.query(CompletedTask).filter(CompletedTask.task_id == task.id).first() is None

    def test_overdue_user_confirmed_errand_is_swept(self, db):
        task = self._add_errand(
            db, "Overdue confirmed", datetime.now() - timedelta(days=2), deadline_auto=False
        )
        task_id = task.id

        swept = auto_complete_overdue_tasks(db)

        assert len(swept) == 1
        assert db.query(Task).filter(Task.id == task_id).first() is None
        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None
        assert bool(completed.auto_completed) is True

    def test_user_confirmed_errand_due_today_not_swept(self, db):
        """Due today = pinned all day; the sweep only takes it once the
        date has fully passed, same as deadline tasks."""
        task = self._add_errand(
            db, "Due today confirmed",
            datetime.combine(date.today(), time(23, 59)),
            deadline_auto=False,
        )

        swept = auto_complete_overdue_tasks(db)

        assert swept == []
        assert db.query(Task).filter(Task.id == task.id).first() is not None


class TestErrandPromptEndpoints:
    def test_errand_deadline_endpoint_sets_date_and_clears_auto(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]
        assert data["deadline_auto"] is True

        resp = client.post(
            f"/tasks/{task_id}/errand-deadline",
            data={"deadline_date": "2099-05-01"},
        )
        assert resp.status_code == 200, resp.text
        assert "showUndo" in resp.headers.get("hx-trigger", "")

        db.expire_all()
        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.deadline_at.date() == date(2099, 5, 1)
        assert bool(task.deadline_auto) is False

    def test_errand_snooze_endpoint_extends_and_keeps_auto(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]
        before = datetime.fromisoformat(data["deadline_at"])

        resp = client.post(f"/tasks/{task_id}/errand-snooze")
        assert resp.status_code == 200, resp.text

        db.expire_all()
        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.deadline_at == before + timedelta(days=182)
        assert bool(task.deadline_auto) is True

    def test_errand_deadline_endpoint_rejects_non_errand(self, client, db):
        data = create_task(client, DEADLINE_PAYLOAD)

        resp = client.post(
            f"/tasks/{data['id']}/errand-deadline",
            data={"deadline_date": "2099-05-01"},
        )
        assert resp.status_code == 400

    def test_prompt_actions_are_undoable(self, client, db):
        from app.models.action_log import ActionLog

        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]
        original_deadline = datetime.fromisoformat(data["deadline_at"])

        client.post(f"/tasks/{task_id}/errand-deadline", data={"deadline_date": "2099-05-01"})
        log = db.query(ActionLog).filter(
            ActionLog.task_id == task_id, ActionLog.action_type == "edit"
        ).order_by(ActionLog.id.desc()).first()
        assert log is not None

        client.post(f"/undo/{log.id}")
        db.expire_all()
        task = db.query(Task).filter(Task.id == task_id).first()
        assert task.deadline_at == original_deadline
        assert bool(task.deadline_auto) is True


class TestErrandPromptSelection:
    def _add_auto_errand(self, db, title, created_days_ago, deadline_in_days):
        task = Task(
            type="errand",
            title=title,
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            created_at=datetime.now() - timedelta(days=created_days_ago),
            deadline_at=datetime.now() + timedelta(days=deadline_in_days),
            deadline_auto=True,
        )
        db.add(task)
        db.commit()
        db.refresh(task)
        return task

    def test_past_half_life_errand_is_prompted(self, db):
        # span 300d, elapsed 200d -> 66% > 50%
        task = self._add_auto_errand(db, "Past half-life", 200, 100)

        due = get_errands_due_for_prompt(db)
        assert task.id in [t.id for t in due]

    def test_fresh_errand_not_prompted(self, db):
        # span 365d, elapsed 0d
        task = self._add_auto_errand(db, "Fresh", 0, 365)

        due = get_errands_due_for_prompt(db)
        assert task.id not in [t.id for t in due]

    def test_user_confirmed_deadline_never_prompted(self, db):
        task = Task(
            type="errand",
            title="Confirmed",
            estimated_duration=15,
            importance=1,
            urgency=1,
            status="pending",
            created_at=datetime.now() - timedelta(days=300),
            deadline_at=datetime.now() + timedelta(days=10),
            deadline_auto=False,
        )
        db.add(task)
        db.commit()

        due = get_errands_due_for_prompt(db)
        assert task.id not in [t.id for t in due]

    def test_capped_at_three_with_expired_first(self, db):
        expired = self._add_auto_errand(db, "Fully expired", 400, -5)
        for i in range(4):
            self._add_auto_errand(db, f"Past half-life {i}", 200, 100)

        due = get_errands_due_for_prompt(db)
        assert len(due) == 3
        assert due[0].id == expired.id

    def test_banner_endpoint_renders_due_errands(self, client, db):
        task = self._add_auto_errand(db, "BannerErrandXYZ", 200, 100)

        resp = client.get("/tasks/errand-prompts")
        assert resp.status_code == 200
        assert "BannerErrandXYZ" in resp.text
        assert "When will you actually do this?" in resp.text
        assert f"/tasks/{task.id}/errand-deadline" in resp.text
        assert f"/tasks/{task.id}/errand-snooze" in resp.text

    def test_banner_endpoint_empty_when_nothing_due(self, client, db):
        self._add_auto_errand(db, "Fresh", 0, 365)

        resp = client.get("/tasks/errand-prompts")
        assert resp.status_code == 200
        assert "errand-prompt-banner" not in resp.text
