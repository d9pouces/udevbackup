import tempfile
from importlib.resources import as_file, files

import pytest

from test_udevbackup.utils import (
    UUID_LUKS_2_PARTITION,
    UUID_LUKS_3_PARTITION,
    UUID_LUKSED_PARTITION,
    UUID_RAW_PARTITION,
    prepare_config,
)
from udevbackup.cli import load_config, main


def test_load_config(monkeypatch):
    with as_file(files("test_udevbackup") / "data/empty") as config_dir:
        config = load_config(config_dir)
    assert config.rules == {}

    with pytest.raises(ValueError):
        with as_file(files("test_udevbackup") / "data/invalid") as config_dir:
            load_config(config_dir)

    with as_file(files("test_udevbackup") / "data/complete") as config_dir:
        config = load_config(config_dir)
    assert UUID_LUKS_2_PARTITION in config.rules
    rule = config.rules[UUID_LUKS_2_PARTITION]
    assert config.smtp_auth_password == "password123"
    assert config.smtp_auth_user == "smtp_user@example.com"
    assert config.smtp_from_email == "from@example.com"
    assert config.smtp_server == "smtp.example.com"
    assert config.smtp_smtp_port == 25
    assert config.smtp_to_email == "to@example.com"
    assert config.smtp_use_starttls is True
    assert config.smtp_use_tls is True
    assert config.use_stdout is True
    assert config.use_smtp is True
    assert config.use_log_file is True
    assert config.log_file == "./udevbackup.log"
    assert config.lock_file == "./udevbackup.lock"
    assert rule.name == "base"
    assert rule.fs_uuid == UUID_LUKSED_PARTITION
    assert rule.script == 'echo "Hello, World!"'
    assert rule.luks_uuid == UUID_LUKS_2_PARTITION
    assert rule.command == ["bash"]
    assert rule.stdout_path == f"{config.temp_directory}/base.out.txt"
    assert rule.stderr_path == f"{config.temp_directory}/base.err.txt"
    assert rule.mount_options == ["noatime,errors=remount-ro"]
    assert rule.user == "backupuser"
    assert rule.pre_script == 'echo "Pre-backup script running"'
    assert rule.post_script == 'echo "Post-backup script running"'


def test_main_show_complete(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        prepare_config(tmpdir, m)
        assert main(["-C", str(config_dir), "show"]) == 0


def test_main_show_invalid(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/invalid"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        prepare_config(tmpdir, m)
        assert main(["-C", str(config_dir), "show"]) == 1


def test_main_at_complete_env(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        m.setenv("ID_FS_UUID", UUID_RAW_PARTITION)
        config = prepare_config(tmpdir, m)
        assert main(["-C", str(config_dir), "at"]) == 0
        assert config.popen_commands_full == [["at", "now"]]
        assert len(config.popen_inputs) == 1
        assert (
            f"run --fs-uuid {UUID_RAW_PARTITION} -C {config_dir}".encode()
            in config.popen_inputs[0]
        )


def test_main_at_complete(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, m)
        assert main(["-C", str(config_dir), "at"]) == 1
        assert config.popen_commands_full == []


def test_main_at_no_at(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        m.setenv("ID_FS_UUID", UUID_RAW_PARTITION)
        config = prepare_config(tmpdir, m)
        config.popen_result["at"] = FileNotFoundError()
        assert main(["-C", str(config_dir), "at"]) == 3
        assert config.popen_commands_full == [["at", "now"]]
        assert len(config.popen_inputs) == 1
        assert (
            f"run --fs-uuid {UUID_RAW_PARTITION} -C {config_dir}".encode()
            in config.popen_inputs[0]
        )


def test_main_at_invalid_at(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        m.setenv("ID_FS_UUID", UUID_RAW_PARTITION)
        config = prepare_config(tmpdir, m)
        config.popen_result["at"] = 1
        assert main(["-C", str(config_dir), "at"]) == 2
        assert config.popen_commands_full == [["at", "now"]]
        assert len(config.popen_inputs) == 1
        assert (
            f"run --fs-uuid {UUID_RAW_PARTITION} -C {config_dir}".encode()
            in config.popen_inputs[0]
        )


def test_main_run_complete_fs_uuid(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, m)
        assert (
            main(["-C", str(config_dir), "run", "--fs-uuid", UUID_LUKS_3_PARTITION])
            == 4
        )
        assert config.popen_commands_full == []


def test_main_run_complete(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, m)
        assert main(["-C", str(config_dir), "run"]) == 1
        assert config.popen_commands_full == []


def test_main_run_example(monkeypatch):
    with as_file(
        files("test_udevbackup") / "data/complete"
    ) as config_dir, monkeypatch.context() as m, tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, m)
        assert main(["-C", str(config_dir), "example"]) == 0
        assert config.popen_commands_full == []
