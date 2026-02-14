from models import Task
from manager import TodoManager

if __name__ == '__main__':
    manager = TodoManager.load_from_file()

    while True:
        print("\nMenu:\n(A)dd\n(L)ist\n(D)elete\nMark a task (C)omplete\n(Q)uit")
        choice:str = input("Enter your choice (A/L/D/C/Q): ")

        match choice.upper():
            case "A":
                desc = input("Please write a short description of the task: ")
                manager.add_task(desc)
            case "L":
                manager.print_tasks()
            case "D":
                try:
                    del_id = int(input("Please enter the id number of the task to delete"))
                    if manager.delete_task(del_id):
                        print(f"Task {del_id} has been deleted")
                    else:
                        print(f"Task {del_id} does not exist")
                except ValueError:
                    print("Please type a number for the id")
                    continue
            case "C":
                done_id = int(input("Please enter the id number of the task to complete"))
                manager.toggle_task(done_id)
            case "Q":
                print("Goodbye!")
                break
            case _:
                print("Please enter a valid choice")