from models import Task
from pathlib import Path
import json

class TodoManager:
    def __init__(self, file_path: str = "tasks.json"):
        self.id_counter = 0
        self.tasks = []
        self.tasks_path = Path(file_path)
        self._load_from_file()

    def _load_from_file(self):
        if self.tasks_path.exists():
            with self.tasks_path.open() as f:
                data = json.load(f)
                self.id_counter = data["id_counter"]
                self.tasks = [Task.from_dict(data) for data in data["tasks"]]
        else:
            self.id_counter = 0
            self.tasks = []

    def add_task(self, description: str, impact: int, urgency: int):
        self.id_counter += 1
        self.tasks.append(Task(self.id_counter, description, impact, urgency))
        self._save_to_file()

    def get_all_tasks(self) -> list[Task]:
        return sorted(self.tasks, key=lambda t: t.priority, reverse=True)

    def print_tasks(self):
        for task in self.get_all_tasks():
            print(task)

    def delete_task(self, task_id: int):
        if task_id in [task.id for task in self.tasks]:
            self.tasks = [
                task for task in self.tasks if task.id != task_id
            ]
            self._save_to_file()
            return True
        else:
            return False

    def toggle_task(self, task_id: int):
        for task in self.tasks:
            if task.id == task_id:
                task.toggle_complete()
        self._save_to_file()

    def _save_to_file(self):
        task_data = [task.to_dict() for task in self.tasks]
        data = {
            "id_counter": self.id_counter,
            "tasks": task_data
        }

        with self.tasks_path.open("w") as f:
            json.dump(data, f)