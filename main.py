from manager import TodoManager

if __name__ == '__main__':
    manager = TodoManager()

    while True:
        print("\nMenu:\n(A)dd\n(L)ist\n(D)elete\nMark a task (C)omplete\n(Q)uit")
        choice:str = input("Enter your choice (A/L/D/C/Q): ")

        match choice.upper():
            case "A":
                desc = input("Please write a short description of the task: ")
                try:
                    imp = int(input("Rate the importance of this task (1-10):"))
                    urg = int(input("Rate the urgency of this task (1-10):"))
                except ValueError:
                    print("Please type a number between 1 and 10")
                manager.add_task(desc, imp, urg)
            case "L":
                manager.print_tasks()
            case "D":
                try:
                    del_id = int(input("Please enter the id number of the task to delete: "))
                    if manager.delete_task(del_id):
                        print(f"Task {del_id} has been deleted")
                    else:
                        print(f"Task {del_id} does not exist")
                except ValueError:
                    print("Please type a number for the id")
                    continue
            case "C":
                done_id = int(input("Please enter the id number of the task to complete: "))
                manager.toggle_task(done_id)
            case "Q":
                print("Goodbye!")
                break
            case _:
                print("Please enter a valid choice")