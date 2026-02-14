from models import Task
from pathlib import Path
import json

tasks_path = Path("tasks.json")

class TodoManager:
    def __init__(self):
        self.id_counter = 0
        self.tasks = []

    @classmethod
    def load_from_file(cls):
        manager = cls()
        if tasks_path.exists():
            with tasks_path.open() as f:
                data = json.load(f)
                manager.id_counter = data["id_counter"]
                manager.tasks = [Task.from_dict(data) for data in data["tasks"]]
        else:
            manager.id_counter = 0
            manager.tasks = []

        return manager

    def add_task(self, description: str):
        self.id_counter += 1
        self.tasks.append(Task(self.id_counter, description))
        self.save_to_file()

    def get_all_tasks(self) -> list[Task]:
        return self.tasks

    def print_tasks(self):
        for task in self.tasks:
            print(task)

    def delete_task(self, task_id: int):
        self.tasks = [
            task for task in self.tasks if task.id != task_id
        ]
        self.save_to_file()

    def toggle_task(self, task_id: int):
        for task in self.tasks:
            if task.id == task_id:
                task.toggle_complete()
        self.save_to_file()

    def save_to_file(self):
        task_data = [task.to_dict() for task in self.tasks]
        data = {
            "id_counter": self.id_counter,
            "tasks": task_data
        }

        with tasks_path.open("w") as f:
            json.dump(data, f)