UdevBackup
==========

Launch a backup script when an external storage device is connected.

Create a configuration file to define which devices to monitor and which scripts to run.
When this device is connected, the volume is mounted, the script is launched automatically and then the volume is
unmounted.
An email can be sent with the output of the script at the end.

If the device is LUKS encrypted, it can be automatically unlocked if its UUID is defined in the config file and if a key
is provided in the /etc/crypttab file.

I wrote this script for a simple offline backup of my server: I just have to turn
the external USB drive on and wait for the email before turning it off again.

Only works on Linux, detecting the connection with udev rules and with the "at" utility to run long jobs (more than 30
seconds).

installation
------------

```bash
sudo pipx install udevbackup
```

you need to install an udev rule to launch udevbackup when a new device (with a file system) is connected:

```bash
    sudo udevbackup install
```

configuration
-------------

Create a .ini config file with a "main" section for global options, and another section for each
target partition. The filename is not important: all .ini files in /etc/udevbackup are read.
These files must use the UTF-8 encoding.

You can display all available options through an example of config file with the "example" command.

```bash
udevbackup example
```

```ini
[main]
lock_file = Name of a global lock file to avoid parallel runs.
log_file = Name of the global log file.
smtp_auth_password = SMTP password. Default to "".
smtp_auth_user = SMTP user. Default to "".
smtp_from_email = E-mail address for the FROM: value. Default to "".
smtp_server = SMTP server. Default to "localhost".
smtp_smtp_port = The SMTP port. Default to 25.
smtp_to_email = Recipient of the e-mail. Required to send e-mails.
smtp_use_starttls = Use STARTTLS for emails. Default to 0.
smtp_use_tls = Use TLS (smtps) for emails. Default to 0.
use_log_file = Write all errors to the log file. Default to 1.
use_smtp = Send messages by email (with the whole content of stdout/stderr of your scripts). Default to 0.
use_stdout = Display messages on stdout. Default to 0.

[example]
command = Command running the script (whose name is passed as first argument). Default to "bash".
fs_uuid = UUID of the target partition.
luks_uuid = UUID of the LUKS partition (a key must be provided in the /etc/crypttab file).
mount_options = Extra mount options. Default to "".
post_script = Script to run after the disk umount. Only run if the disk was mounted. Default to "".
pre_script = Script to run before mounting the disk. The disk will not be mounted if this script does not returns 0. Default to "".
script = Content of the script to execute when the disk is mounted. Working dir is the mounted directory.This script will be copied in a temporary file, whose name is passed to the command.
stderr = Write stderr to this filename.
stdout = Write stdout to this filename.
user = User used for running the script and mounting the disk.
```

Here is a complete example:

```bash
cat /etc/udevbackup/example.ini
```

```ini
[main]
smtp_auth_user = user
smtp_auth_password = s3cr3tP@ssw0rd
smtp_server = localhost
use_stdout = 0
use_smtp = 1

[my_config]
fs_uuid = b5094075-9f23-4881-9315-86fe4e97f029
script = mkdir -p ./data
         rsync -av /data/to_backup/ ./data/
```

You can display the current config:

```bash
udevbackup show
```
