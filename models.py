class Task():

    def __init__(self, id: int, description: str):
        self.id = id
        self.description = description
        self.is_complete = False

    def __repr__(self):
        if self.is_complete:
            return f"- [x] {self.id} {self.description}"
        else:
            return f"- [ ] {self.id} {self.description}"

    def to_dict(self):
        return {
            "id": self.id,
            "description": self.description,
            "is_complete": self.is_complete
        }

    @staticmethod
    def from_dict(data: dict):
        old_task = Task(data["id"], data["description"])
        old_task.is_complete = data["is_complete"]
        return old_task


    def toggle_complete(self):
        self.is_complete = not self.is_complete

    def __eq__(self, other):
        """verifies whether two tasks are identical"""
        return (
            (self.id == other.id)
            and (other.is_complete == self.is_complete)
            and (other.description == self.description)
        )

