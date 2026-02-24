import pytest
from manager import TodoManager
from models import Task

@pytest.fixture
def temp_manager(tmp_path: str):
    return TodoManager(f"{tmp_path}/tasks.json")

def test_add_task_increases_count(temp_manager):
    # Act
    temp_manager.add_task("Test task")

    # Assert
    assert len(temp_manager.tasks) == 1
    assert temp_manager.tasks[0].description == "Test task"

def test_persistence(tmp_path: str):
    mgr1 = TodoManager(f"{tmp_path}/tasks1.json")
    mgr1.add_task("Test task persistence")

    mgr2 = TodoManager(f"{tmp_path}/tasks1.json")
    print(f"temp path: {tmp_path}")
    assert len(mgr2.tasks) == 1
    assert mgr2.tasks[0].description == "Test task persistence"