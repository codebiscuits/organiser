from models import Task

class TodoManager:
    def __init__(self):
        self.id_counter: int = 0
        self.tasks = []

    def add_task(self, description: str):
        self.id_counter += 1
        self.tasks.append(Task(self.id_counter, description))

    def get_all_tasks(self) -> list[Task]:
        return self.tasks

    def print_tasks(self):
        for task in self.tasks:
            print(task)

    def delete_task(self, task_id: int):
        self.tasks = [
            task for task in self.tasks if task.id != task_id
        ]

    def toggle_task(self, task_id: int):
        for task in self.tasks:
            if task.id == task_id:
                task.toggle_complete()