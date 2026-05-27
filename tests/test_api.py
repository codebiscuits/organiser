"""
Integration tests for API routes — exercises the full HTTP → DB → response cycle.

Uses a fresh in-memory SQLite DB per test (via conftest.py fixtures).
Tests verify DB state after mutations rather than HTML content, since most
endpoints return HTMX fragments.
"""
import pytest
from datetime import date, datetime, timedelta

from app.models.task import Task, CompletedTask
from app.models.recurrence import Projection, Recurrence
from app.models.workout import Exercise, ExerciseMuscle, MuscleGroup, PerformedSet


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
        assert resp.headers.get("hx-trigger") == "taskUpdated"

        assert db.query(Task).filter(Task.id == task_id).first() is None

    def test_complete_errand_records_in_completed_tasks(self, client, db):
        data = create_task(client, ERRAND_PAYLOAD)
        task_id = data["id"]

        client.post(f"/tasks/{task_id}/complete")

        completed = db.query(CompletedTask).filter(CompletedTask.task_id == task_id).first()
        assert completed is not None

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
        assert resp.headers.get("hx-trigger") == "taskUpdated"

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
