import logging
import os
import platform
import pwd
import shlex
import smtplib
import subprocess
import sys
import time
from collections import OrderedDict
from configparser import ConfigParser
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from getpass import getuser
from logging import ERROR, INFO, WARNING
from tempfile import NamedTemporaryFile, mkdtemp, gettempdir
from typing import Union

from google_speech import Speech
from termcolor import cprint

logger = logging.getLogger("udevbackup")


class Section:
    text_options = {}
    bool_options = {}
    int_options = {}
    float_options = {}
    required = set()

    @classmethod
    def print_help(cls, section):
        cprint("[%s]" % section, "yellow")
        all_values = {}
        all_values.update(cls.text_options)
        all_values.update(cls.bool_options)
        all_values.update(cls.int_options)
        all_values.update(cls.float_options)
        for k, v in all_values.items():
            color = "yellow" if k in cls.required else "green"
            cprint("%s = %s" % (k, v), color)

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
                    raise ValueError("Unrecognized option [%s] %s" % (section, option))
        for required_option in cls.required:
            if required_option in kwargs:
                continue
            raise ValueError("option %s is required in section [%s]" % (required_option, section))
        return kwargs


class Rule(Section):
    text_options = {
        "fs_uuid": "UUID of the used file system. "
        "Check /dev/disk/by-uuid/ before and after having connected your disk to get it.",
        "command": "Command to call for running the script (whose name is passed as first argument). "
        'Default to "bash".',
        "script": "Content of the script to execute when the disk is mounted. "
        "Current working dir is always the mounted directory."
        "This script will be copied in a temporary file, whose name is passed to the command.",
        "stdout": "Write stdout to this filename.",
        "stderr": "Write stderr to this filename.",
        "mount_options": 'Extra mount options. Default to "".',
        "user": "User used for running the script and mounting the disk." 'Default to "%s".' % getuser(),
        "pre_script": "Script to run before mounting the disk. The disk will not be mounted if this script "
        'does not returns 0. Default to "".',
        "post_script": "Script to run after the disk umount. Only run if the disk was mounted. " 'Default to "".',
    }
    required = {"fs_uuid", "script"}

    def __init__(
        self,
        config,
        name,
        fs_uuid: str,
        script: str,
        command: str = "bash",
        user: str = getuser(),
        stdout: str = "%(tmp)s/%(name)s.out",
        stderr: str = "%(tmp)s/%(name)s.err",
        mount_options: str = "",
        pre_script: Union[str, None] = None,
        post_script: Union[str, None] = None,
    ):
        self.config = config
        self.name = name
        self.errors = []
        self.fs_uuid = fs_uuid
        self.script = script
        self.pre_script = pre_script
        self.post_script = post_script
        self.command = shlex.split(command)
        self.user = user
        self.mount_options = shlex.split(mount_options)
        self.stdout = stdout % {"name": self.name, "tmp": gettempdir()}
        self.stderr = stderr % {"name": self.name, "tmp": gettempdir()}
        self._is_mounted = False
        self._mount_dir = None
        self._stdout_fd = None
        self._stderr_fd = None

    def execute(self):
        self.set_up()
        if self._is_mounted:
            self.backup()
        self.tear_down()

    def set_up(self):
        try:
            self._stdout_fd = open(self.stdout, "wb")
        except Exception as e:
            self.errors.append("Unable to open %s (%s)." % (self.stdout, e))
            return False
        try:
            self._stderr_fd = open(self.stderr, "wb")
        except Exception as e:
            self.errors.append("Unable to open %s (%s)." % (self.stderr, e))
            return False
        if not self.execute_script("pre_script", cwd=None):
            return False
        self._mount_dir = mkdtemp(prefix="%s-" % self.fs_uuid)
        uid = pwd.getpwnam(self.user).pw_uid
        gid = pwd.getpwnam(self.user).pw_gid
        try:
            os.chown(self._mount_dir, uid=uid, gid=gid)
        except PermissionError:
            self.errors.append("Unable to chown mount folder to %(user)s" % self.__dict__)
            return False
        cmd = ["mount"] + self.mount_options + ["UUID=%s" % self.fs_uuid, self._mount_dir]
        p = subprocess.Popen(cmd, stderr=self._stderr_fd, stdout=self._stdout_fd, stdin=subprocess.PIPE)
        try:
            p.communicate(b"")
        except Exception as e:
            self.errors.append("Unable to mount the device using '%s': %s" % (" ".join(cmd), e))
            return False
        if p.returncode != 0:
            self.errors.append("Unable to mount the device %(fs_uuid)s" % self.__dict__)
            return False
        self._is_mounted = True
        return True

    def backup(self) -> bool:
        return self.execute_script("script", cwd=self._mount_dir)

    def execute_script(self, script_attr_name, cwd=None):
        script_content = getattr(self, script_attr_name)
        if not script_content:
            return True
        with NamedTemporaryFile() as fd:
            fd.write(script_content.encode())
            fd.flush()
            command = ["sudo", "-Hu", self.user] + self.command + [fd.name]
            try:
                p = subprocess.Popen(
                    command, cwd=cwd, stderr=self._stderr_fd, stdout=self._stdout_fd, stdin=subprocess.PIPE
                )
            except Exception as e:
                self.errors.append("Unable to execute script %(cmd)s (%(e)s)." % {"cmd": script_attr_name, "e": e})
                return False
            p.communicate(b"")
            if p.returncode != 0:
                self.errors.append("Unable to execute script %(cmd)s." % {"cmd": script_attr_name})
                return False
            return True

    def tear_down(self):
        was_mounted = self._is_mounted
        if was_mounted:
            p = subprocess.Popen(
                ["umount", self._mount_dir], stderr=self._stderr_fd, stdout=self._stdout_fd, stdin=subprocess.PIPE
            )
            p.communicate(b"")
            if p.returncode != 0:
                self.errors.append("Unable to umount %(fs_uuid)s" % self.__dict__)
            else:
                self._is_mounted = False
        if self._mount_dir:
            os.rmdir(self._mount_dir)
            self._mount_dir = None
        if was_mounted:
            self.execute_script("post_script", cwd=None)
        if self._stderr_fd:
            self._stderr_fd.close()
        if self._stdout_fd:
            self._stdout_fd.close()


class Config(Section):
    udev_rule = 'ACTION=="add", ENV{DEVTYPE}=="partition", RUN+="%s at"' % sys.argv[0]
    section_name = "main"
    lang = "en"
    text_options = {
        "smtp_auth_user": 'SMTP user. Default to "".',
        "smtp_auth_password": 'SMTP password. Default to "".',
        "smtp_server": 'SMTP server. Default to "localhost".',
        "smtp_from_email": 'E-mail address for the FROM: value. Default to "".',
        "smtp_to_email": "Recipient of the e-mail. Required to send e-mails.",
        "log_file": "Name of the global log file. Default to %s/udevbackup.log" % gettempdir(),
    }
    bool_options = {
        "use_speech": "Use google speech for announcing successes and failures. Default to 0.",
        "use_stdout": "Display messages on stdout. Default to 0.",
        "use_smtp": "Send messages by email (with the whole content of stdout/stderr of your scripts). "
        "Default to 0.",
        "use_log_file": "Write all errors to this file. Default to 1.",
        "smtp_use_tls": "Use TLS (smtps) for emails. Default to 0.",
        "smtp_use_starttls": "Use STARTTLS for emails. Default to 0.",
    }
    int_options = {"smtp_smtp_port": "The SMTP port. Default to 25."}
    udev_rule_filename = "/etc/udev/rules.d/udevbackup.rules"

    def __init__(
        self,
        smtp_auth_user: Union[str, None] = None,
        smtp_auth_password: Union[str, None] = None,
        smtp_server: str = "localhost",
        smtp_smtp_port: int = 25,
        smtp_use_tls: bool = False,
        smtp_use_starttls: bool = False,
        smtp_to_email: Union[str, None] = None,
        smtp_from_email: Union[str, None] = None,
        use_speech: bool = False,
        use_stdout: bool = False,
        use_smtp: bool = False,
        use_log_file: bool = True,
        log_file: str = "%s/udevbackup.log" % gettempdir(),
    ):
        self.smtp_auth_user = smtp_auth_user
        self.smtp_auth_password = smtp_auth_password
        self.smtp_server = smtp_server
        self.smtp_smtp_port = smtp_smtp_port
        self.smtp_use_tls = smtp_use_tls
        self.smtp_use_starttls = smtp_use_starttls
        self.smtp_from_email = smtp_from_email or "root@%s" % (platform.node())
        self.smtp_to_email = smtp_to_email
        self.use_speech = use_speech
        self.use_stdout = use_stdout
        self.use_smtp = use_smtp
        self.log_file = log_file
        self.use_log_file = use_log_file
        self.rules = OrderedDict()  # rules[fs_uuid] = Rule()
        self._log_content = ""

    def register(self, rule: Rule):
        self.rules[rule.fs_uuid] = rule

    def run(self, fs_uuid: str, fork=True):
        if fs_uuid not in self.rules:
            return
        rule = self.rules[fs_uuid]
        assert isinstance(rule, Rule)
        if fork and not self.fork():
            return
        self.log_text("Device %s is connected." % fs_uuid, level=INFO)
        try:
            rule.set_up()
            if not rule.errors:
                rule.backup()
            rule.tear_down()
        except Exception as e:
            self.log_text("An error happened: %s." % e)
        if rule.errors:
            self.log_text("An error happened.", level=ERROR)
            for error in rule.errors:
                self.log_text(error, level=ERROR)
        else:
            self.log_text("Successful.", level=INFO)
        if self.use_smtp:
            subject = "%s" % rule.name
            if rule.errors:
                subject += " [KO]"
            else:
                subject += " [OK]"
            self.send_email(
                self._log_content,
                subject=subject,
                attachments=[rule.stdout, rule.stderr],
            )

        c = 0
        while os.path.exists("/dev/disk/by-uuid/%s" % rule.fs_uuid):
            if c % 600 == 0:
                self.log_text("Please disconnect the device.", level=INFO)
            c += 1
            time.sleep(0.1)
        if self.use_stdout:
            cprint(self._log_content)

    def log_text(self, text, level=INFO):
        if self.use_speech:
            try:
                Speech(text, self.lang).play([])
            except KeyboardInterrupt:
                pass
            except Exception as e:
                text += "\nERROR: Unable to use text to speech (%s)\n" % e
        if self.use_log_file:
            try:
                with open(self.log_file, "a") as fd:
                    fd.write("%s\n" % text)
            except Exception as e:
                text += "\nERROR: Unable to use append text to %s (%s)\n" % (
                    self.log_file,
                    e,
                )
        logger.log(level, text)
        if self.use_stdout:
            color = "green"
            if level >= WARNING:
                color = "yellow"
            if level >= ERROR:
                color = "red"
            cprint(text, color)
        self._log_content += text
        self._log_content += "\n"

    @staticmethod
    def fork():
        os.closerange(0, 65535)  # just in case
        wpid = os.fork()
        if wpid != 0:
            time.sleep(0.1)  # get forked process chance to change cgroup
            return False
        wpid = os.fork()
        if wpid != 0:
            time.sleep(0.1)
            return False
        with open("/sys/fs/cgroup/cpu/tasks", "a+") as fd:
            fd.write(str(os.getpid()))
        time.sleep(3)  # defer execution by XX seconds
        # YOUR CODE GOES HERE
        sys.stdout.flush()
        sys.stderr.flush()
        sys.stdout = open("/dev/null", "w")
        sys.stderr = open("/dev/null", "w")
        sys.stdin = open("/dev/null", "r")
        return True

    def show(self):
        self.show_rule_file()
        for rule in self.rules.values():
            cprint("[%s]" % rule.name, "yellow")
            cprint("file system uuid: %s" % rule.fs_uuid, "green")
            cprint("extra mount options: %s" % " ".join(rule.mount_options), "green")
            cprint("mounted file system will be chowned to: %s" % rule.user, "green")
            cprint("stdout will be written to: %s" % rule.stdout, "green")
            cprint("stderr will be written to: %s" % rule.stderr, "green")
            cprint("command to execute: ", "green")
            cmd = " ".join(shlex.quote(x) for x in rule.command)
            cprint("MOUNT_POINT=[mount point]")
            cprint("cat << EOF > [tmpfile] ; sudo -Hu %s %s [tmpfile]\n%s\nEOF" % (rule.user, cmd, rule.script))
        if not self.rules:
            cprint("Please create a .ini file in the config dir", "red")

    @classmethod
    def show_rule_file(cls):
        if not os.path.isfile(cls.udev_rule_filename):
            cprint("Please run the following commands: ")
            cprint(
                "echo '%s' | sudo tee %s" % (cls.udev_rule, cls.udev_rule_filename),
                "green",
            )
            cprint("udevadm control --reload-rules", "green")

    def send_email(self, content, subject=None, attachments=None):
        try:
            if self.smtp_use_tls:
                smtp = smtplib.SMTP_SSL(self.smtp_server, self.smtp_smtp_port)
                smtp.set_debuglevel(0)
            else:
                smtp = smtplib.SMTP(self.smtp_server, self.smtp_smtp_port)
                smtp.set_debuglevel(0)
                smtp.starttls()
            if self.smtp_auth_user and self.smtp_auth_password:
                smtp.login(self.smtp_auth_user, self.smtp_auth_password)
            if not self.smtp_from_email or not self.smtp_to_email:
                return
            msg = MIMEMultipart()
            msg["From"] = self.smtp_from_email
            msg["To"] = self.smtp_to_email
            if subject:
                msg["Subject"] = subject
            msg.attach(MIMEText(content, "plain"))
            if attachments:
                for attachment in attachments:
                    if not os.path.isfile(attachment):
                        continue
                    part = MIMEBase("application", "octet-stream")
                    with open(attachment, "rb") as fd:
                        attachment_content = fd.read()
                    part.set_payload(attachment_content)
                    encoders.encode_base64(part)
                    part.add_header(
                        "Content-Disposition",
                        "attachment; filename= %s" % os.path.basename(attachment),
                    )
                    msg.attach(part)

            smtp.sendmail(self.smtp_from_email, [self.smtp_to_email], msg.as_string())
        except Exception as e:
            self.log_text("Unable to send mail to %s: %s." % (self.smtp_to_email, e), level=INFO)
