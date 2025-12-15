import os
import pathlib
import platform
import pwd
import shlex
import smtplib
import subprocess
import sys
import tempfile
import time
from configparser import ConfigParser
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from logging import ERROR, INFO, WARNING

import fasteners
from systemlogger import getLogger
from termcolor import cprint

logger = getLogger(name="udevbackup", extra_tags={"application_fqdn": "system"})


def get_command() -> list[str] | None:
    """Return the current method used to invoke udevbackup."""
    if sys.orig_argv[1] == "-m" and sys.orig_argv[2] == "udevbackup":
        return [sys.executable, "-m", "udevbackup"]
    return [sys.executable, sys.orig_argv[1]]


class ConfigSection:
    text_options = {}
    bool_options = {}
    int_options = {}
    float_options = {}
    required = set()

    @classmethod
    def print_help(cls, section: str):
        cprint(f"[{section}]", "yellow")
        all_values = {}
        all_values.update(cls.text_options)
        all_values.update(cls.bool_options)
        all_values.update(cls.int_options)
        all_values.update(cls.float_options)
        for k, v in sorted(all_values.items()):
            if k in cls.required:
                cprint(f"{k} = {v}", "yellow")
            else:
                cprint(f"{k} = {v}", "green")

    @classmethod
    def load(cls, parser: ConfigParser, section: str):
        kwargs = {}
        if parser.has_section(section):
            for option in parser.options(section):
                if option in cls.text_options:
                    kwargs[option] = parser.get(section, option)
                elif option in cls.bool_options:
                    kwargs[option] = parser.getboolean(section, option)
                elif option in cls.int_options:
                    kwargs[option] = parser.getint(section, option)
                elif option in cls.float_options:
                    kwargs[option] = parser.getfloat(section, option)
                else:
                    raise ValueError(f"Unrecognized option [{section}] {option}")
        for required_option in cls.required:
            if required_option in kwargs:
                continue
            raise ValueError(
                f"option {required_option} is required in section [{section}]"
            )
        return kwargs


class Rule(ConfigSection):
    text_options = {
        "fs_uuid": "UUID of the target partition.",
        "luks_uuid": "UUID of the LUKS partition (a key must be provided in the /etc/crypttab file).",
        "command": 'Command running the script (whose name is passed as first argument). Default to "bash".',
        "script": "Content of the script to execute when the disk is mounted. "
        "Working dir is the mounted directory."
        "This script will be copied in a temporary file, whose name is passed to the command.",
        "stdout": "Write stdout to this filename.",
        "stderr": "Write stderr to this filename.",
        "mount_options": 'Extra mount options. Default to "".',
        "user": "User used for running the script and mounting the disk.",
        "pre_script": "Script to run before mounting the disk. The disk will not be mounted if this script "
        'does not returns 0. Default to "".',
        "post_script": "Script to run after the disk umount. Only run if the disk was mounted. "
        'Default to "".',
    }
    required = {"fs_uuid", "script"}

    def __init__(
        self,
        config,
        name: str,
        fs_uuid: str,
        script: str,
        luks_uuid: str | None = None,
        command: str = "bash",
        user: str | None = None,
        stdout: str = "%(tmp)s/%(name)s.out.txt",
        stderr: str = "%(tmp)s/%(name)s.err.txt",
        mount_options: str = "",
        pre_script: str | None = None,
        post_script: str | None = None,
    ):
        self.config: Config = config
        self.name: str = name
        self.errors: list[str] = []
        self.fs_uuid: str = fs_uuid
        self.luks_uuid: str | None = luks_uuid
        self.luks_name: str | None = None
        self.script: str = script
        self.pre_script: str | None = pre_script
        self.post_script: str | None = post_script
        self.command: list[str] = shlex.split(command)
        self.user: str | None = user
        self.mount_options: list[str] = shlex.split(mount_options)
        self.stdout_path: str = stdout % {
            "name": self.name,
            "tmp": config.temp_directory,
        }
        self.stderr_path: str = stderr % {
            "name": self.name,
            "tmp": config.temp_directory,
        }
        self._is_mounted: bool = False
        self._is_luks_opened: bool = False
        self._mount_dir: str | None = None
        self._stdout_fd = None
        self._stderr_fd = None

    def execute(self):
        self.set_up()
        if not self.errors:
            self.execute_script("script", cwd=self._mount_dir)
        self.tear_down()

    def set_up(self):
        try:
            self._stdout_fd = open(self.stdout_path, "wb")
        except Exception as e:
            self.errors.append(f"Unable to open {self.stdout_path} ({e}).")
            return False
        try:
            self._stderr_fd = open(self.stderr_path, "wb")
        except Exception as e:
            self.errors.append(f"Unable to open {self.stderr_path} ({e}).")
            return False
        if not self.execute_script("pre_script", cwd=None):
            return False

        if self.luks_uuid and self.luks_name:
            cmd = ["cryptdisks_start", self.luks_name]
            if not self.execute_command(cmd):
                self.errors.append(f"Unable to open LUKS device {self.luks_uuid}")
                return False
            self._is_luks_opened = True
            timeout = time.time() + self.config.luks_open_timeout
            while not (
                self.config.devices_root / "disk" / "by-uuid" / self.fs_uuid
            ).exists():
                if time.time() > timeout:
                    self.errors.append(
                        f"Timeout waiting for device {self.fs_uuid} after opening LUKS"
                    )
                    return False
                time.sleep(0.5)

        self._mount_dir = tempfile.mkdtemp(
            prefix=f"{self.config.temp_prefix}_{self.fs_uuid}-"
        )
        if self.user:
            try:
                uid: int = pwd.getpwnam(self.user).pw_uid
                gid: int = pwd.getpwnam(self.user).pw_gid
                os.chown(self._mount_dir, uid=uid, gid=gid)
            except KeyError:
                self.errors.append(f"Unable to get info for user '{self.user}'")
                return False
            except PermissionError:
                self.errors.append(f"Unable to chown mount directory to '{self.user}'")
                return False

        if self.execute_command(
            ["mount"] + self.mount_options + [f"UUID={self.fs_uuid}", self._mount_dir]
        ):
            self._is_mounted = True
        return self._is_mounted

    def tear_down(self):
        was_mounted = self._is_mounted
        if was_mounted:
            if self.execute_command(["umount", self._mount_dir]):
                self._is_mounted = False
        if self._is_luks_opened and not self._is_mounted:
            if self.execute_command(["cryptsetup", "close", self.luks_name]):
                self._is_luks_opened = False
        if self._mount_dir and not self._is_mounted:
            os.rmdir(self._mount_dir)
            self._mount_dir = None
        if was_mounted:
            self.execute_script("post_script", cwd=None)
        if self._stderr_fd:
            self._stderr_fd.close()
        if self._stdout_fd:
            self._stdout_fd.close()

    def execute_script(self, script_attr_name: str, cwd: str = None) -> bool:
        script_content = getattr(self, script_attr_name)
        if not script_content:
            return True
        with tempfile.NamedTemporaryFile(
            prefix=f"{self.config.temp_prefix}_{self.fs_uuid}-{script_attr_name}"
        ) as fd:
            fd.write(script_content.encode())
            fd.flush()
            if self.user:
                command = ["sudo", "-Hu", self.user] + self.command + [fd.name]
            else:
                command = self.command + [fd.name]
            return self.execute_command(command, cwd=cwd, attr_name=script_attr_name)

    def execute_command(
        self, command: list[str], cwd: str | None = None, attr_name: str | None = None
    ) -> bool:
        title = attr_name or " ".join(command)
        ret_code = -1
        self.config.log_text(f"Executing command {title}", INFO)
        try:
            p = subprocess.Popen(
                command,
                cwd=cwd,
                stderr=self._stderr_fd,
                stdout=self._stdout_fd,
                stdin=subprocess.PIPE,
            )
            p.communicate(b"")
            ret_code = p.returncode
            if ret_code != 0:
                self.errors.append(f"Unable to execute command {title}.")
        except Exception as e:
            self.errors.append(f"Unable to execute command {title} ({e}).")
        return ret_code == 0


class Config(ConfigSection):
    udev_rule_path = pathlib.Path("/etc/udev/rules.d/99-udevbackup.rules")

    ini_section_name = "main"
    text_options = {
        "smtp_auth_user": 'SMTP user. Default to "".',
        "smtp_auth_password": 'SMTP password. Default to "".',
        "smtp_server": 'SMTP server. Default to "localhost".',
        "smtp_from_email": 'E-mail address for the FROM: value. Default to "".',
        "smtp_to_email": "Recipient of the e-mail. Required to send e-mails.",
        "log_file": "Name of the global log file.",
        "lock_file": "Name of a global lock file to avoid parallel runs.",
    }
    bool_options = {
        "use_stdout": "Display messages on stdout. Default to 0.",
        "use_smtp": "Send messages by email (with the whole content of stdout/stderr of your scripts). "
        "Default to 0.",
        "use_log_file": "Write all errors to the log file. Default to 1.",
        "smtp_use_tls": "Use TLS (smtps) for emails. Default to 0.",
        "smtp_use_starttls": "Use STARTTLS for emails. Default to 0.",
    }
    int_options = {"smtp_smtp_port": "The SMTP port. Default to 25."}

    def __init__(
        self,
        smtp_auth_password: str | None = None,
        smtp_auth_user: str | None = None,
        smtp_from_email: str | None = None,
        smtp_server: str = "localhost",
        smtp_smtp_port: int = 25,
        smtp_to_email: str | None = None,
        smtp_use_starttls: bool = False,
        smtp_use_tls: bool = False,
        use_stdout: bool = False,
        use_smtp: bool = False,
        use_log_file: bool = True,
        log_file: str | None = None,
        lock_file: str | None = None,
    ):

        self.use_smtp = use_smtp
        self.smtp_auth_password: str | None = smtp_auth_password
        self.smtp_auth_user: str | None = smtp_auth_user
        self.smtp_from_email: str = smtp_from_email or f"root@{platform.node()}"
        self.smtp_server: str | None = smtp_server
        self.smtp_smtp_port: int = smtp_smtp_port
        self.smtp_to_email: str | None = smtp_to_email
        self.smtp_use_starttls: bool = smtp_use_starttls
        self.smtp_use_tls: bool = smtp_use_tls

        self.use_stdout: bool = use_stdout

        self.use_log_file: bool = use_log_file
        self.log_file: str | None = log_file

        self.lock_file: str | None = lock_file

        self.rules: dict[str, Rule] = {}  # rules[fs_uuid] = Rule()

        self._log_content: str = ""

        self.temp_prefix: str = "udevbackup_"
        # these constants simplify tests
        self.devices_root: pathlib.Path = pathlib.Path("/dev/disk")
        self.crypttab: pathlib.Path = pathlib.Path("/etc/crypttab")
        self.temp_directory: pathlib.Path = pathlib.Path(tempfile.gettempdir())
        self.luks_open_timeout: float = 300.0  # seconds
        self.stdout = sys.stdout
        self.stderr = sys.stderr

    def register(self, rule: Rule):
        self.rules[rule.luks_uuid or rule.fs_uuid] = rule

    def identify_cryptodevices(self):
        """Parse /etc/crypttab to get the mapping between LUKS UUID and name."""
        luks_names = self.get_luks_names()
        for rule in self.rules.values():
            rule.luks_name = luks_names.get(rule.luks_uuid)

    def get_luks_names(self) -> dict[str, str]:
        content = ""
        if not self.crypttab.is_file():
            return {}
        try:
            with self.crypttab.open("r") as fd:
                content = fd.read()
        except PermissionError:
            self.log_text(
                "Unable to read /etc/crypttab (permission denied).", level=WARNING
            )
        return self.parse_crypttab(content)

    def parse_crypttab(self, content: str) -> dict[str, str]:
        aliases = self.load_device_aliases()
        luks_uuid_to_luks_name: dict[str, str] = {}
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 4:
                continue
            name = parts[0]
            device = parts[1]
            key = parts[2]
            if device in aliases and key != "none":
                luks_uuid_to_luks_name[aliases[device]] = name
        return luks_uuid_to_luks_name

    def load_device_aliases(self) -> dict[str, str]:
        synonyms: dict[str, str] = {}
        dev_to_uuid: dict[str, str] = {}
        if not (self.devices_root / "disk" / "by-uuid").is_dir():
            return synonyms
        for dev_uuid in pathlib.Path(self.devices_root / "disk" / "by-uuid").iterdir():
            if dev_uuid.is_symlink():
                device = str(dev_uuid.resolve())
                synonyms[f"UUID={dev_uuid.name}"] = dev_uuid.name
                synonyms[device] = dev_uuid.name
                dev_to_uuid[device] = dev_uuid.name
        for method in ("PARTUUID", "PARTLABEL", "LABEL"):
            root = pathlib.Path(self.devices_root / "disk" / f"by-{method.lower()}")
            if not root.is_dir():
                continue
            for dev_part in root.iterdir():
                if dev_part.is_symlink():
                    device = str(dev_part.resolve())
                    if device in dev_to_uuid:
                        uuid = dev_to_uuid[device]
                        synonyms[f"{method}={dev_part.name}"] = uuid
        return synonyms

    def run(self, fs_uuid: str) -> bool:
        if fs_uuid not in self.rules:
            # no message: we don't want a message everytime a device is connected
            return False
        os.chdir(self.temp_directory)
        rule: Rule = self.rules[fs_uuid]

        self.log_text(f"Device {fs_uuid} is connected.", level=INFO)
        try:
            if self.lock_file:
                self.log_text(f"Waiting for {self.lock_file}.", level=INFO)
                with fasteners.InterProcessLock(self.lock_file):
                    self.log_text(f"{self.lock_file} acquired.", level=INFO)
                    rule.execute()
                    self.log_text(f"{self.lock_file} release.", level=INFO)
            else:
                rule.execute()
        except Exception as e:
            self.log_text(f"An error happened: {e}.")
        if rule.errors:
            self.log_text("An error happened.", level=ERROR)
            for error in rule.errors:
                self.log_text(error, level=ERROR)
        else:
            self.log_text("Successful.", level=INFO)
        if self.use_smtp:
            subject = str(rule.name)
            if rule.errors:
                subject += " [KO]"
            else:
                subject += " [OK]"
            self.send_email(
                self._log_content,
                subject=subject,
                attachments=[rule.stdout_path, rule.stderr_path],
            )
        self.log_text(f"Device {fs_uuid} can be disconnected.", level=INFO)
        return len(rule.errors) == 0

    def log_text(self, text, level=INFO):
        if self.use_log_file:
            log_filepath = self.log_file or self.temp_directory / "udevbackup.log"
            try:
                with open(log_filepath, "a") as fd:
                    fd.write(f"{text}\n")
            except Exception as e:
                text += f"\nERROR: Unable to use append text to {log_filepath} ({e})\n"
        logger.log(level, text)
        if self.use_stdout:
            if level >= ERROR:
                cprint(text, "red", file=self.stderr, force_color=True)
            elif level >= WARNING:
                cprint(text, "yellow", file=self.stderr, force_color=True)
            else:
                cprint(text, "green", file=self.stdout, force_color=True)
        self._log_content += text
        self._log_content += "\n"

    def show(self):
        self.show_rule_file(stdout=self.stdout, stderr=self.stderr)
        for rule in self.rules.values():
            options = " ".join(rule.mount_options)
            cmd = " ".join(shlex.quote(x) for x in rule.command)
            cprint(f"[{rule.name}]", "yellow", force_color=True, file=self.stdout)
            cprint(
                f"file system uuid: {rule.fs_uuid}",
                "green",
                force_color=True,
                file=self.stdout,
            )
            cprint(
                f"extra mount options: {options}",
                "green",
                force_color=True,
                file=self.stdout,
            )
            if rule.user:
                cprint(
                    f"mounted file system will be chowned to: {rule.user}",
                    "green",
                    force_color=True,
                    file=self.stdout,
                )
            cprint(
                f"stdout will be written to: {rule.stdout_path}",
                "green",
                force_color=True,
                file=self.stdout,
            )
            cprint(
                f"stderr will be written to: {rule.stderr_path}",
                "green",
                force_color=True,
                file=self.stdout,
            )
            cprint("command to execute: ", "green", force_color=True, file=self.stdout)
            cprint("MOUNT_POINT=[mount point]", force_color=True, file=self.stdout)
            if rule.user:
                cprint(
                    f"cat << EOF > [tmpfile] ; sudo -Hu {rule.user} {cmd} [tmpfile]\n{rule.script}\nEOF",
                    force_color=True,
                    file=self.stdout,
                )
            else:
                cprint(
                    f"cat << EOF > [tmpfile] ; {cmd} [tmpfile]\n{rule.script}\nEOF",
                    force_color=True,
                    file=self.stdout,
                )
        if not self.rules:
            cprint(
                "Please create a 'rule.ini' file in the config dir.",
                "red",
                force_color=True,
                file=self.stderr,
            )

    @classmethod
    def show_rule_file(cls, stdout=sys.stdout, stderr=sys.stderr):
        if not cls.udev_rule_path.is_file():
            cprint(
                "A udev rule must be added first.", "red", file=stderr, force_color=True
            )
            cprint(
                f"echo '{cls.udev_rule()}' | sudo tee {cls.udev_rule_path}",
                "green",
                file=stdout,
                force_color=True,
            )
            cprint(
                "udevadm control --reload-rules", "green", file=stdout, force_color=True
            )

    @classmethod
    def udev_rule(cls) -> str:
        cmd = get_command() + ["at"]
        at_cmd = shlex.join(cmd)
        return f'ACTION=="add", ENV{{DEVTYPE}}=="partition", RUN+="{at_cmd}"'

    def send_email(self, content, subject=None, attachments=None):
        try:
            if self.smtp_use_tls:
                smtp = smtplib.SMTP_SSL(self.smtp_server, self.smtp_smtp_port)
                smtp.set_debuglevel(0)
            else:
                smtp = smtplib.SMTP(self.smtp_server, self.smtp_smtp_port)
                smtp.set_debuglevel(0)
                if self.smtp_use_starttls:
                    smtp.starttls()
            if self.smtp_auth_user and self.smtp_auth_password:
                smtp.login(self.smtp_auth_user, self.smtp_auth_password)
            if not self.smtp_from_email or not self.smtp_to_email:
                self.log_text(
                    "Unable to send e-mail: SMTP from/to e-mail address is not configured.",
                    level=ERROR,
                )
                return
            msg = MIMEMultipart()
            msg["From"] = self.smtp_from_email
            msg["To"] = self.smtp_to_email
            if subject:
                msg["Subject"] = subject
            msg.attach(MIMEText(content, "plain"))
            if attachments:
                for attachment in attachments:
                    if os.path.isfile(attachment):
                        part = MIMEBase("application", "octet-stream")
                        with open(attachment, "rb") as fd:
                            attachment_content = fd.read()
                        part.set_payload(attachment_content)
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f"attachment; filename= {os.path.basename(attachment)}",
                        )
                        msg.attach(part)
            smtp.sendmail(self.smtp_from_email, [self.smtp_to_email], msg.as_string())
        except Exception as e:
            self.log_text(
                f"Unable to send mail to {self.smtp_to_email}: {e}.", level=ERROR
            )
