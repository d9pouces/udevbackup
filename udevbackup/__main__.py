from udevbackup.cli import main


def execute(name):
    if name == "__main__":
        main()


execute(__name__)
