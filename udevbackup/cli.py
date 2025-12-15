import argparse
import glob
import os
import shlex
import subprocess  # nosec B404
import sys
from configparser import ConfigParser
from logging import ERROR, INFO

from systemlogger import getLogger
from termcolor import cprint

from udevbackup.rule import Config, Rule

logger = getLogger(name="udevbackup", extra_tags={"application_fqdn": "system"})


def load_config(config_dir):
    """Load the configuration."""
    config_filenames = glob.glob(f"{config_dir}/*.ini")
    parser = ConfigParser(interpolation=None)
    parser.read(config_filenames, encoding="utf-8")
    ini_config_section = Config.ini_section_name
    kwargs = Config.load(parser, ini_config_section)
    config = Config(**kwargs)
    for section in parser.sections():
        if section == ini_config_section:
            continue
        kwargs = Rule.load(parser, section)
        rule = Rule(config, section, **kwargs)
        config.register(rule)
    config.identify_cryptodevices()
    return config


def main(args: list[str] | None = None):
    """Run the scripts, should be launched by an udev rule."""
    parser = argparse.ArgumentParser(
        description="Run script when targetted external devices are connected"
    )
    parser.add_argument(
        "command",
        choices=("show", "run", "example", "at"),
        help="""command to run.
                        show: show the loaded configuration.
                        run: run the script for the given filesystem uuid (/dev/disk/by-uuid/XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX).
                        example: show a example of config file.
                        at: launch this script through `at` and immediately exits.
                        """,
    )
    parser.add_argument(
        "--config-dir",
        "-C",
        default="/etc/udevbackup",
        help="Configuration directory (default: /etc/udevbackup)",
    )
    parser.add_argument(
        "--fs-uuid",
        "-U",
        default=os.environ.get("ID_FS_UUID"),
        help="If not specified, use the ID_FS_UUID environment variable.",
    )
    args = parser.parse_args(args=args)
    return_code = 0  # 0 = success, != 0 = error
    try:
        config = load_config(args.config_dir)
    except ValueError as e:
        logger.log(
            ERROR,
            f"Unable to load udevbackup configuration: {e}",
        )
        config = None
    if not config:
        return_code = 1
    elif args.command == "show":
        config.show()
    elif args.command == "at":
        if not args.fs_uuid:
            logger.log(
                ERROR,
                "No filesystem uuid provided: use --fs-uuid or set the ID_FS_UUID environment variable",
            )
            return_code = 1
        else:
            cmd = [sys.argv[0], "run", "--fs-uuid", args.fs_uuid, "-C", args.config_dir]
            at_cmd = shlex.join(cmd)
            logger.log(INFO, at_cmd)
            cmd = ["at", "now"]
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # nosec B603 B607
                p.communicate(at_cmd.encode())
                if p.returncode != 0:
                    logger.log(ERROR, f"Failed to run `{' '.join(cmd)}` command)")
                    return_code = 2
            except FileNotFoundError:
                logger.log(ERROR, "Command not found: 'at'")
                return_code = 3
    elif args.command == "run":
        if not args.fs_uuid:
            logger.log(
                ERROR,
                "No filesystem uuid provided: use --fs-uuid or set ID_FS_UUID environment variable",
            )
            return_code = 1
        else:
            logger.log(INFO, f"{args.fs_uuid} detected")
            return_code = 0 if config.run(args.fs_uuid) else 4
    elif args.command == "example":
        Config.show_rule_file()
        cprint(f"Create one or more .ini files in {args.config_dir}.")
        cprint("Yellow lines are mandatory.")
        Config.print_help(Config.ini_section_name)
        cprint("")
        Rule.print_help("example")
    return return_code
