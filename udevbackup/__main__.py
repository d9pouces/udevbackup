from udevbackup import cli


def execute(name):
    if name == "__main__":
        cli.main()


execute(__name__)
