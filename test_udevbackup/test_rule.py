import logging
import pathlib
import smtplib
import tempfile

from test_udevbackup.utils import (
    CRYPTTAB_CONTENT_1,
    CRYPTTAB_CONTENT_2,
    UUID_LUKS_1_PARTITION,
    UUID_LUKS_2_PARTITION,
    UUID_LUKS_3_PARTITION,
    UUID_LUKSED_PARTITION,
    UUID_RAW_PARTITION,
    FakeSMTP,
    prepare_config,
)
from udevbackup.rule import Config


def test_log_text(monkeypatch):
    """Test the log_text method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        config.log_text("Info.", level=logging.INFO)
        config.log_text("Warning.", level=logging.WARNING)
        config.log_text("Error.", level=logging.ERROR)
        assert "\x1b[32mInfo.\x1b[0m\n" == config.stdout.getvalue()
        assert (
            "\x1b[33mWarning.\x1b[0m\n\x1b[31mError.\x1b[0m\n"
            == config.stderr.getvalue()
        )
        assert (
            config.temp_directory / "udevbackup.log"
        ).read_text() == "Info.\nWarning.\nError.\n"
        assert config._log_content == "Info.\nWarning.\nError.\n"


def test_load_device_aliases(monkeypatch):
    """Test the load_device_aliases method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        actual_aliases = config.load_device_aliases()
        expected_aliases = {
            f"{config.devices_root}/sda1": UUID_RAW_PARTITION,
            f"{config.devices_root}/sdb1": UUID_LUKS_1_PARTITION,
            f"{config.devices_root}/sdc1": UUID_LUKS_2_PARTITION,
            f"{config.devices_root}/sdd1": UUID_LUKS_3_PARTITION,
            "PARTLABEL=data": UUID_LUKS_2_PARTITION,
            "PARTLABEL=other": UUID_LUKS_3_PARTITION,
            "PARTLABEL=primary": UUID_RAW_PARTITION,
            "PARTLABEL=secondary": UUID_LUKS_1_PARTITION,
            "PARTUUID=628ab6f7-b7b4-4702-9f7f-c264d7bfa6ca": UUID_LUKS_3_PARTITION,
            "PARTUUID=7a404841-844a-4a06-85dc-e7eea8e9aaf4": UUID_LUKS_2_PARTITION,
            "PARTUUID=f86f6365-65b2-4d3b-99b9-55e50e6a544a": UUID_LUKS_1_PARTITION,
            "PARTUUID=fd03e6cd-39b7-4a8d-8f1a-efb34c8238df": UUID_RAW_PARTITION,
            "UUID=07858aaf-d564-4123-b90c-19059ba47da8": UUID_LUKS_2_PARTITION,
            "UUID=3c50f3a4-82c6-4de6-875d-f8079b3f12ca": UUID_LUKS_3_PARTITION,
            "UUID=6162f76f-e228-4aed-8e86-b63840137255": UUID_LUKS_1_PARTITION,
            "UUID=8e00b174-2d2e-4190-8b81-0fc264ad3ff7": UUID_RAW_PARTITION,
        }

        assert actual_aliases == expected_aliases
        actual_parsed = config.parse_crypttab(CRYPTTAB_CONTENT_1)
        expected_parsed = {
            UUID_LUKS_1_PARTITION: "dm-0",
            UUID_LUKS_2_PARTITION: "dm-1",
        }
        assert actual_parsed == expected_parsed
        actual_parsed = config.parse_crypttab(CRYPTTAB_CONTENT_2)
        expected_parsed = {
            UUID_LUKS_1_PARTITION: "dm-0",
            UUID_LUKS_2_PARTITION: "dm-1",
            UUID_LUKS_3_PARTITION: "dm-2",
        }
        assert actual_parsed == expected_parsed
        assert config.rules[UUID_RAW_PARTITION].luks_uuid is None
        assert config.rules[UUID_LUKS_2_PARTITION].fs_uuid == UUID_LUKSED_PARTITION
        assert config.rules[UUID_LUKS_2_PARTITION].luks_name is None
        config.identify_cryptodevices()
        assert config.rules[UUID_LUKS_2_PARTITION].luks_name == "dm-1"


def test_show(monkeypatch):
    """Test the show method."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Config.udev_rule_path = pathlib.Path(tmpdir) / "udevbackup.rules"
        config = prepare_config(tmpdir, monkeypatch)
        config.show()
        assert "A udev rule must be added first." in config.stderr.getvalue()
        assert "udevadm control --reload-rules" in config.stdout.getvalue()
        config.rules.clear()
        config.stderr.seek(0)
        config.stdout.seek(0)
        Config.udev_rule_path.write_text(Config.udev_rule())
        config.show()
        assert "A udev rule must be added first." not in config.stderr.getvalue()
        assert (
            "Please create a 'rule.ini' file in the config dir."
            in config.stderr.getvalue()
        )


def test_run_raw(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        config.identify_cryptodevices()
        config.run(UUID_LUKS_3_PARTITION)
        assert config._log_content == ""
        assert UUID_RAW_PARTITION in config.rules
        assert UUID_LUKSED_PARTITION not in config.rules
        assert UUID_LUKS_2_PARTITION in config.rules
        config.run(UUID_RAW_PARTITION)
        assert f"Device {UUID_RAW_PARTITION} is connected." in config._log_content
        assert (
            f"Device {UUID_RAW_PARTITION} can be disconnected." in config._log_content
        )
        assert "Successful." in config._log_content
        assert config.popen_commands_short == ["mount", "bash", "umount"]


def test_run_luks(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        config.identify_cryptodevices()
        config.run(UUID_LUKS_2_PARTITION)
        assert f"Device {UUID_LUKS_2_PARTITION} is connected." in config._log_content
        assert (
            f"Device {UUID_LUKS_2_PARTITION} can be disconnected."
            in config._log_content
        )
        assert "Successful." in config._log_content
        assert config.popen_commands_short == [
            "cryptdisks_start",
            "mount",
            "sudo",
            "umount",
            "cryptsetup",
        ]


def test_send_email_no_email(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        config.identify_cryptodevices()
        config.use_smtp = True
        config.smtp_use_starttls = True
        config.smtp_use_tls = False
        config.run(UUID_RAW_PARTITION)
        assert config.popen_commands_short == ["mount", "bash", "umount"]
        assert (
            40,
            "Unable to send e-mail: SMTP from/to e-mail address is not configured.",
        ) in config.logger_content


def test_send_email(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        config.identify_cryptodevices()
        config.use_smtp = True
        config.smtp_use_starttls = True
        config.smtp_use_tls = False

        config.smtp_from_email = "from@example.com"
        config.smtp_to_email = "to@example.com"
        config.smtp_auth_password = "password123"
        config.smtp_auth_user = "user"
        config.smtp_use_tls = True

        config.run(UUID_RAW_PARTITION)
        assert (
            40,
            f"Unable to send mail to {config.smtp_to_email}: Authentication failed.",
        ) in config.logger_content
        assert config.popen_commands_short == ["mount", "bash", "umount"]


def test_send_email_valid(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        config = prepare_config(tmpdir, monkeypatch)
        config.identify_cryptodevices()
        config.use_smtp = True
        config.smtp_use_starttls = True
        config.smtp_use_tls = False
        config.smtp_from_email = "from@example.com"
        config.smtp_to_email = "to@example.com"
        config.smtp_auth_password = "pass"
        config.smtp_auth_user = "user"
        config.popen_commands_short.clear()
        config.run(UUID_RAW_PARTITION)
        assert config.popen_commands_short == ["mount", "bash", "umount"]
        assert (
            40,
            f"Unable to send mail to {config.smtp_to_email}: Authentication failed.",
        ) not in config.logger_content
