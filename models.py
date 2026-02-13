class Task():

    def __init__(self, id: int, description: str):
        self.id = id
        self.description = description
        self.is_complete: bool = False

    def __repr__(self):
        if self.is_complete:
            return f"- [x] {self.id} {self.description}"
        else:
            return f"- [ ] {self.id} {self.description}"