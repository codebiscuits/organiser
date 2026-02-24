from models import Task
import pytest

@pytest.fixture
def sample_task():
    """Provides a standard task instance for testing"""
    return Task(id=1, description="Test task")

def test_task_creation(sample_task):
    # Assert
    assert sample_task.id == 1
    assert sample_task.description == "Test task"

def test_toggle_sets_timestamp():
    # Arrange
    t = Task(1, "Test toggle")

    # Act (toggle on)
    t.toggle_complete()

    # Assert
    assert t.is_complete is True
    assert t.completed_at is not None

    # Act/assert again (toggle off)
    t.toggle_complete()
    assert t.is_complete is False
    assert t.completed_at is None

def test_serialisation_round_trip():
    # Arrange
    t = Task(1, "Test serialisation")
    t2 = Task(1, "Test serialisation 2")
    t2.toggle_complete()

    # Act
    t3 = t.from_dict(t.to_dict())
    t4 = t2.from_dict(t2.to_dict())

    # Assert
    assert t3 == t
    assert t4 == t2

def test_impact_urgency_bounds():
    t = Task(1, "Test impact/urgency bounds", 5, 5)
    assert t.priority == 25

    t3 = Task(1, "Test impact/urgency bounds", 11, 11)
    assert t3.priority == 100

    t4 = Task(1, "Test impact/urgency bounds", -1, -1)
    assert t4.priority == 1