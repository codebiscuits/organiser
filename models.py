from datetime import datetime

class Task():

    def __init__(self, id: int, description: str):
        self.id = id
        self.description = description
        self.is_complete = False
        self.created_at = datetime.now()
        self.completed_at = None

    def __repr__(self):
        created_at = self.created_at.strftime("%Y-%m-%d %H:%M")
        if self.is_complete:
            completed_at = self.completed_at.strftime("%Y-%m-%d %H:%M")
            return f"- [x] {self.id:<3} | {self.description:<20} | Created: {created_at} | Completed: {completed_at}"
        else:
            return f"- [ ] {self.id:<3} | {self.description:<20} | Created: {created_at}"

    def to_dict(self):
        if self.is_complete:
            completed_at = self.completed_at.isoformat()
        else:
            completed_at = None
        return {
            "id": self.id,
            "description": self.description,
            "is_complete": self.is_complete,
            "created_at": self.created_at.isoformat(),
            "completed_at": completed_at,
        }

    @staticmethod
    def from_dict(data: dict):
        old_task = Task(data["id"], data["description"])
        old_task.is_complete = data["is_complete"]
        old_task.created_at = datetime.fromisoformat(data["created_at"])
        if data["is_complete"]:
            old_task.completed_at = datetime.fromisoformat(data["completed_at"]) or None
        else:
            old_task.completed_at = None
        return old_task


    def toggle_complete(self):
        self.is_complete = not self.is_complete
        if self.is_complete:
            self.completed_at = datetime.now()
        else:
            self.completed_at = None

    def __eq__(self, other):
        """verifies whether two tasks are identical"""
        return (
            (self.id == other.id)
            and (other.is_complete == self.is_complete)
            and (other.description == self.description)
            and (other.created_at == self.created_at)
        )

