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


# ---------------------------------------------------------------------------
# Recurring task delete modal / delete-instance
# ---------------------------------------------------------------------------

class TestDeleteInstance:
    def _task_with_todays_projection(self, client, db):
        data = create_task(client, RECURRING_PAYLOAD)
        task_id = data["id"]
        db.add(Projection(task_id=task_id, due_date=date.today()))
        db.commit()
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
