import argparse
import glob
import os
import subprocess  # nosec B404
import sys
from configparser import ConfigParser

from systemlogger import getLogger
from termcolor import cprint

from udevbackup.rule import Config, Rule

logger = getLogger(name="udevbackup", extra_tags={"application_fqdn": "system"})


def load_config(config_dir):
    """Load the configuration."""
    config_filenames = glob.glob(f"{config_dir}/*.ini")
    parser = ConfigParser()
    parser.read(config_filenames, encoding="utf-8")
    config_section = Config.section_name
    kwargs = Config.load(parser, config_section)
    config = Config(**kwargs)
    for section in parser.sections():
        if section == config_section:
            continue
        kwargs = Rule.load(parser, section)
        rule = Rule(config, section, **kwargs)
        config.register(rule)
    config.identify_cryptodevices()
    return config


def main():
    """Run the scripts, should be launched by a udev rule."""
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
        default=os.environ.get("ID_FS_UUID"),
        help="If not specified, use the ID_FS_UUID environment variable.",
    )
    args = parser.parse_args()
    return_code = 0  # 0 = success, != 0 = error
    try:
        config = load_config(args.config_dir)
    except ValueError as e:
        logger.error(f"Unable to load udevbackup configuration: {e}")
        config = None
    if not config:
        return_code = 1
    elif args.command == "show":
        config.show()
    elif args.command == "at":
        if not args.fs_uuid:
            logger.error(
                "No filesystem uuid provided: use --fs-uuid or set the ID_FS_UUID environment variable"
            )
            return_code = 1
        else:
            at_cmd = f"{sys.argv[0]} run --fs-uuid '{args.fs_uuid}'"
            logger.info(at_cmd)
            cmd = ["at", "now"]
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE)  # nosec B603 B607
            p.communicate(at_cmd.encode())
    elif args.command == "run":
        if not args.fs_uuid:
            logger.error(
                "No filesystem uuid provided: use --fs-uuid or set ID_FS_UUID environment variable"
            )
            return_code = 1
        else:
            logger.info(f"{args.fs_uuid} detected")
            config.run(args.fs_uuid)
    elif args.command == "example":
        Config.show_rule_file()
        cprint(f"Create one or more .ini files in {args.config_dir}.")
        cprint("Yellow lines are mandatory.")
        Config.print_help(Config.section_name)
        cprint("")
        Rule.print_help("example")
    return return_code
