"""Regression coverage for PEFH90 through JSON/modal and admin form edits."""
from datetime import date, timedelta

import pytest

from app.models.recurrence import Projection, ProjectionExclusion, Recurrence


TODAY = date(2026, 11, 8)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    for module in ("app.routers.tasks", "app.routers.admin", "app.services.recurrence"):
        monkeypatch.setattr(f"{module}.date", FixedDate)


def create_recurring(client, task_type="recurring", **rule):
    recurrence = {"interval_type": "daily", "start_date": "2026-09-16", **rule}
    response = client.post("/tasks/", json={
        "type": task_type, "title": "Healthy Minds", "importance": 2,
        "recurrence": recurrence,
    })
    assert response.status_code == 200, response.text
    return response.json()["id"], recurrence


def update_rule(client, path, task_id, task_type, rule):
    if path == "api":
        response = client.put(f"/tasks/{task_id}", json={"recurrence": rule})
        assert response.status_code == 200, response.text
    else:
        response = client.post(f"/admin/tasks/{task_id}", data={
            "title": "Healthy Minds", "type": task_type, "importance": "2",
            **{key: value for key, value in rule.items() if value is not None},
        }, follow_redirects=False)
        assert response.status_code == 303, response.text


def projection_dates(db, task_id):
    return {row.due_date for row in db.query(Projection).filter_by(task_id=task_id)}


@pytest.mark.parametrize("path", ["api", "form"])
@pytest.mark.parametrize("task_type", ["recurring", "workout", "variable_recurring"])
def test_postponing_start_rebuilds_future_only_and_keeps_exclusions(client, db, path, task_type):
    task_id, rule = create_recurring(client, task_type)
    other_id, _ = create_recurring(client)
    other_dates = projection_dates(db, other_id)
    historical = Projection(task_id=task_id, due_date=date(2026, 9, 1))
    db.add(historical)
    excluded = date(2027, 1, 5)
    db.add(ProjectionExclusion(task_id=task_id, due_date=excluded))
    db.commit()
    historical_id = historical.instance_id

    update_rule(client, path, task_id, task_type, {**rule, "start_date": "2027-01-04"})

    dates = projection_dates(db, task_id)
    assert date(2026, 9, 1) in dates, "historical projections must not be rebuilt"
    assert db.get(Projection, historical_id).due_date == date(2026, 9, 1)
    future = {day for day in dates if day >= TODAY}
    assert future and min(future) == date(2027, 1, 4), "stale pre-start projections remain"
    if task_type == "variable_recurring":
        assert future == {date(2027, 1, 4)}, "variable tasks have one next occurrence, not a pattern"
    assert excluded not in future
    assert db.query(ProjectionExclusion).filter_by(task_id=task_id, due_date=excluded).count() == 1
    assert projection_dates(db, other_id) == other_dates
    db.expire_all()
    assert db.query(Recurrence).filter_by(task_id=task_id).one().start_date == date(2027, 1, 4)


@pytest.mark.parametrize("path", ["api", "form"])
@pytest.mark.parametrize("changes, expected", [
    ({"interval_multiple": 2}, {TODAY + timedelta(days=n) for n in range(0, 91, 2)}),
    ({"interval_type": "weekly", "day_of_week": "1,3"}, {
        TODAY + timedelta(days=n) for n in range(91)
        if (TODAY + timedelta(days=n)).weekday() in (0, 2)
    }),
    ({"interval_type": "monthly", "day_of_month": "15"}, {
        date(2026, 11, 15), date(2026, 12, 15), date(2027, 1, 15),
    }),
    ({"interval_type": "yearly", "month_of_year": "1", "day_of_month": "4"}, {
        date(2027, 1, 4),
    }),
    ({"end_date": "2026-11-09"}, {TODAY, date(2026, 11, 9)}),
    ({"end_date": "2026-11-07"}, set()),
])
def test_changed_rule_replaces_schedule(client, db, path, changes, expected):
    task_id, rule = create_recurring(client, start_date=TODAY.isoformat())
    # Include a stale row beyond the usual generation horizon: all future
    # rows must be removed, not just the rows inside the new window.
    db.add(Projection(task_id=task_id, due_date=TODAY + timedelta(days=200)))
    db.commit()
    update_rule(client, path, task_id, "recurring", {**rule, **changes})
    assert projection_dates(db, task_id) == expected


@pytest.mark.parametrize("path", ["api", "form"])
def test_moving_start_back_into_past_generates_from_today(client, db, path):
    task_id, rule = create_recurring(client, start_date="2027-01-04")
    update_rule(client, path, task_id, "recurring", {**rule, "start_date": "2026-01-01"})
    assert projection_dates(db, task_id) == {TODAY + timedelta(days=n) for n in range(91)}


@pytest.mark.parametrize("edit", ["omitted", "none", "unchanged", "form"])
@pytest.mark.parametrize("task_type", ["recurring", "workout", "variable_recurring"])
def test_non_rule_edits_preserve_existing_schedule(client, db, edit, task_type):
    task_id, rule = create_recurring(client, task_type)
    # Simulate completion/removal of today's occurrence and a manually
    # deferred future occurrence. Unchanged edits must not regenerate either.
    db.query(Projection).filter_by(task_id=task_id, due_date=TODAY).delete()
    db.add(Projection(task_id=task_id, due_date=TODAY + timedelta(days=200)))
    db.commit()
    before = {(row.instance_id, row.due_date) for row in db.query(Projection).filter_by(task_id=task_id)}
    if edit == "form":
        update_rule(client, "form", task_id, task_type, rule)
    else:
        payload: dict = {"notes": "Notes only"}
        if edit == "none":
            payload["recurrence"] = None
        elif edit == "unchanged":
            payload["recurrence"] = rule
        response = client.put(f"/tasks/{task_id}", json=payload)
        assert response.status_code == 200, response.text
    after = {(row.instance_id, row.due_date) for row in db.query(Projection).filter_by(task_id=task_id)}
    assert after == before


@pytest.mark.parametrize("path", ["api", "form"])
def test_clearing_end_date_reopens_schedule(client, db, path):
    task_id, rule = create_recurring(client, end_date="2026-11-09")
    db.query(Projection).filter(
        Projection.task_id == task_id, Projection.due_date > date(2026, 11, 9),
    ).delete()
    db.commit()
    update_rule(client, path, task_id, "recurring", {**rule, "end_date": None})
    assert projection_dates(db, task_id) == {TODAY + timedelta(days=n) for n in range(91)}
    db.expire_all()
    assert db.query(Recurrence).filter_by(task_id=task_id).one().end_date is None


@pytest.mark.parametrize("path", ["api", "form"])
def test_variable_start_edit_respects_excluded_next_occurrence(client, db, path):
    task_id, rule = create_recurring(client, "variable_recurring")
    db.add(ProjectionExclusion(task_id=task_id, due_date=date(2027, 1, 4)))
    db.commit()
    update_rule(client, path, task_id, "variable_recurring", {**rule, "start_date": "2027-01-04"})
    assert projection_dates(db, task_id) == set()
    assert db.query(ProjectionExclusion).filter_by(task_id=task_id).count() == 1


def test_omitted_end_date_preserves_existing_limit(client, db):
    task_id, rule = create_recurring(client, start_date=TODAY.isoformat(), end_date="2026-11-12")
    rule.pop("end_date")
    update_rule(client, "api", task_id, "recurring", {**rule, "interval_multiple": 2})
    assert projection_dates(db, task_id) == {TODAY, date(2026, 11, 10), date(2026, 11, 12)}
    db.expire_all()
    assert db.query(Recurrence).filter_by(task_id=task_id).one().end_date == date(2026, 11, 12)


@pytest.mark.parametrize("path", ["api", "form"])
@pytest.mark.parametrize("original, changes, expected", [
    ({"interval_type": "weekly", "day_of_week": "1"}, {"day_of_week": "3"}, {
        TODAY + timedelta(days=n) for n in range(91)
        if (TODAY + timedelta(days=n)).weekday() == 2
    }),
    ({"interval_type": "monthly", "day_of_month": "15"}, {"day_of_month": "20"}, {
        date(2026, 11, 20), date(2026, 12, 20), date(2027, 1, 20),
    }),
    ({"interval_type": "yearly", "day_of_month": "4", "month_of_year": "1"},
     {"month_of_year": "12"}, {date(2026, 12, 4)}),
])
def test_changing_days_without_changing_interval_type(client, db, path, original, changes, expected):
    task_id, rule = create_recurring(client, start_date=TODAY.isoformat(), **original)
    update_rule(client, path, task_id, "recurring", {**rule, **changes})
    assert projection_dates(db, task_id) == expected
