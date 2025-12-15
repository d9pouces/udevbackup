import io
import os
import pathlib
import pwd
import subprocess
from logging import Logger

from udevbackup.rule import Config, Rule

UUID_LUKSED_PARTITION = "0e601e2a-3504-4890-bc21-3f4b46aef7cf"
UUID_RAW_PARTITION = "8e00b174-2d2e-4190-8b81-0fc264ad3ff7"
UUID_LUKS_1_PARTITION = "6162f76f-e228-4aed-8e86-b63840137255"
UUID_LUKS_2_PARTITION = "07858aaf-d564-4123-b90c-19059ba47da8"
UUID_LUKS_3_PARTITION = "3c50f3a4-82c6-4de6-875d-f8079b3f12ca"
PARTITIONS = {
    "raw": {
        "device": "sda1",
        "partlabel": "primary",
        "partuuid": "fd03e6cd-39b7-4a8d-8f1a-efb34c8238df",
        "uuid": UUID_RAW_PARTITION,
    },
    "luksed": {
        "device": "dm-0",
        "partlabel": "LUKS partition",
        "partuuid": "9850b6b3-28fa-46d3-8722-c7f4a5c9c330",
        "uuid": UUID_LUKSED_PARTITION,
    },
    "luks_1": {
        "device": "sdb1",
        "partlabel": "secondary",
        "partuuid": "f86f6365-65b2-4d3b-99b9-55e50e6a544a",
        "uuid": UUID_LUKS_1_PARTITION,
    },
    "luks_2": {
        "device": "sdc1",
        "partlabel": "data",
        "partuuid": "7a404841-844a-4a06-85dc-e7eea8e9aaf4",
        "uuid": UUID_LUKS_2_PARTITION,
    },
    "luks_3": {
        "device": "sdd1",
        "partlabel": "other",
        "partuuid": "628ab6f7-b7b4-4702-9f7f-c264d7bfa6ca",
        "uuid": UUID_LUKS_3_PARTITION,
    },
}
CRYPTTAB_CONTENT_1 = """# <target name>	<source device>		<key file>	<options>
dm-0 UUID=6162f76f-e228-4aed-8e86-b63840137255 /etc/luks-keys/6162f76f.key luks
dm-1 PARTUUID=7a404841-844a-4a06-85dc-e7eea8e9aaf4 /etc/luks-keys/7a404841.key luks,noauto
dm-2 UUID=3c50f3a4-82c6-4de6-875d-f8079b3f12ca none luks
"""
CRYPTTAB_CONTENT_2 = """# <target name>	<source device>		<key file>	<options>
dm-45 invalid
dm-0 UUID=6162f76f-e228-4aed-8e86-b63840137255 /etc/luks-keys/6162f76f.key luks
dm-1 PARTUUID=7a404841-844a-4a06-85dc-e7eea8e9aaf4 /etc/luks-keys/7a404841.key luks,noauto
dm-2 UUID=3c50f3a4-82c6-4de6-875d-f8079b3f12ca /etc/luks-keys/628ab6f7.key luks
"""


class TestConfig(Config):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.logger_content: list[tuple[int, str]] = []
        self.popen_commands_full: list[list[str]] = []
        self.popen_commands_short: list[str] = []
        self.popen_result: dict[str, int | Exception] = {}
        self.popen_inputs: list[bytes | None] = []
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()
        self.luks_open_timeout = 0.2

    def prepare_device(self, name: str):
        part_data = PARTITIONS[name]
        device_path = self.devices_root / part_data["device"]
        device_path.touch()
        for alias in ("partlabel", "partuuid", "uuid"):
            link_parent = self.devices_root / "disk" / f"by-{alias}"
            link_path = link_parent / part_data[alias]
            link_path.parent.mkdir(parents=True, exist_ok=True)
            link_path.symlink_to(os.path.relpath(device_path, link_parent))


def getpwnam(username: str):
    class FakePwNam:
        def __init__(self, uid: int, gid: int):
            self.pw_uid = uid
            self.pw_gid = gid

    if username == "backupuser":
        return FakePwNam(1001, 1001)
    raise KeyError(f"getpwnam(): name not found: '{username}'")


def chown(path: str, uid: int, gid: int):
    if uid == 1001 and gid == 1001:
        return
    raise PermissionError(f"[Errno 1] Operation not permitted: '{path}'")


def prepare_config(tmpdir: str, monkeypatch) -> TestConfig:
    config = TestConfig(
        use_log_file=True, use_stdout=True, lock_file=f"{tmpdir}/udevbackup.lock"
    )
    dev_root = pathlib.Path(tmpdir).resolve()
    monkeypatch.setattr(
        Logger,
        "log",
        lambda level, msg, *args, **kwargs: config.logger_content.append((level, msg)),
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakePopen(config, *args, **kwargs),
    )
    monkeypatch.setattr(pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(os, "chown", chown)
    config.temp_directory = dev_root / "tmp"
    config.temp_directory.mkdir(parents=True)
    config.crypttab = dev_root / "crypttab"
    config.crypttab.write_text(CRYPTTAB_CONTENT_1, encoding="utf-8")
    config.devices_root = dev_root
    config.prepare_device("raw")
    config.prepare_device("luks_1")
    config.prepare_device("luks_2")
    config.prepare_device("luks_3")
    rule_1 = Rule(config, "primary", UUID_RAW_PARTITION, "echo test1")
    rule_2 = Rule(
        config,
        "data",
        UUID_LUKSED_PARTITION,
        "echo test2",
        luks_uuid=UUID_LUKS_2_PARTITION,
        user="backupuser",
    )
    config.register(rule_1)
    config.register(rule_2)
    return config


class FakePopen:
    def __init__(
        self,
        config: TestConfig,
        command: list[str],
        cwd=None,
        stderr=None,
        stdout=None,
        stdin=None,
    ):
        self.command = command
        self.config = config
        self.cwd = cwd
        self.stderr = stderr
        self.stdout = stdout
        self.stdin = stdin
        self.returncode = 0

    def communicate(self, data: bytes | None = None):
        self.config.popen_commands_full.append(self.command)
        self.config.popen_commands_short.append(self.command[0])
        self.config.popen_inputs.append(data)
        if self.command[0] in self.config.popen_result:
            result = self.config.popen_result[self.command[0]]
            if isinstance(result, Exception):
                raise result
            self.returncode = result
        if self.command[0] == "cryptdisks_start":
            self.config.prepare_device("luksed")
        return None, None
