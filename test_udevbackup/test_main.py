from udevbackup import cli
from udevbackup.__main__ import execute


def test_run(monkeypatch):
    is_executed: list[bool] = []
    monkeypatch.setattr(
        cli,
        "main",
        lambda: is_executed.append(True),
    )
    execute("__main__")
    assert is_executed == [True]
    execute("__other__")
    assert is_executed == [True]
