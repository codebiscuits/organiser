from models import Task
from manager import TodoManager

if __name__ == '__main__':
    manager = TodoManager()

    while True:
        print("\nMenu:\n(A)dd\n(L)ist\n(D)elete\nMark a task (C)omplete\n(Q)uit")
        choice = input("Enter your choice (A/L/D/C/Q): ")

        match choice:
            case "A":
                desc = input("Please write a short description of the task")
                manager.add_task(desc)
            case "L":
                print(manager.get_all_tasks())
            case "D":
                try:
                    del_id = int(input("Please enter the id number of the task to delete"))
                    manager.delete_task(del_id)
                except ValueError:
                    print("Please type a number for the id")
                    continue
            case "C":
                done_id = int(input("Please enter the id number of the task to complete"))
                manager.toggle_task(done_id)
            case _:
                print("Please enter a valid choice")