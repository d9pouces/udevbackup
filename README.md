UdevBackup
==========

On Linux, detects when specified storage devices are connected, then mounts them,
executes a script, unmounts them and tells when it is done (using mail or text to speech).

A config file defines storage devices and the scripts to run.

I wrote this script for a simple offline backup of my server: I just have to turn
the external USB drive on and wait for the message (using text to speech) before
turning it off again. UdevBackup double forks before running the script, so
there is no timeout problem with udev and slow scripts.

Require the "at" utility for running long jobs (more than 30 seconds).

installation
------------

    sudo pip3 install udevbackup --upgrade

you need to create a udev rule to launch udevbackup when a new device (with a file system) is connected:

    echo 'ACTION=="add", ENV{DEVTYPE}=="partition", RUN+="/usr/local/bin/udevbackup at"' | sudo tee /etc/udev/rules.d/udevbackup.rules
    udevadm control --reload-rules

If you only have short jobs, you can use

    echo 'ACTION=="add", ENV{DEVTYPE}=="partition", RUN+="/usr/local/bin/udevbackup run"' | sudo tee /etc/udev/rules.d/udevbackup.rules
    udevadm control --reload-rules

configuration
-------------

Create a .ini config file with a "main" section for global options, and another section for each
target partition. The name is not important. All .ini files in /etc/udevbackup are read.
These files must use the UTF-8 encoding.

You can display all available options with the "help" command, but .

    udevbackup help

    Create one or more .ini files in /etc/udevbackup.
    Yellow lines are mandatory.
    [main]
    smtp_auth_user = SMTP user. Default to "".
    smtp_auth_password = SMTP password. Default to "".
    smtp_server = SMTP server. Default to "localhost".
    smtp_from_email = Recipient of the e-mail.  Default to "".
    smtp_to_email = E-mail address for the FROM: value. Default to "".
    use_speech = Use google speech for announcing successes and failures. Default to 0.
    use_stdout = Display messages on stdout. Default to 0.
    use_smtp = Send messages by email (with the whole content of stdout/stderr of your scripts). Default to 0.
    smtp_use_tls = Use TLS (smtps) for emails. Default to 0.
    smtp_use_starttls = Use STARTTLS for emails. Default to 0.
    smtp_smtp_port = The SMTP port. Default to 25.

    [example]
    fs_uuid = UUID of the used file system. Check /dev/disk/by-uuid/ before and after having connected your disk to get it.
    command = Command to call for running the script (whose name is passed as first argument). Default to "bash".
    script = Content of the script to execute when the disk is mounted. Current working dir is the mounted directory. This script will be copied in a temporary file, whose name is passed to the command.
    stdout = Write stdout to this filename.
    stderr = Write stderr to this filename.
    mount_options = Extra mount options. Default to "".
    user = User used for running the script and mounting the disk.Default to "current user".
    pre_script = Script to run before mounting the disk. The disk will not be mounted if this script does not returns 0. Default to "".
    post_script = Script to run after the disk umount. Only run if the disk was mounted. Default to "".

Here is a complete example:

    cat /etc/udevbackup/example.ini
    [main]
    smtp_auth_user = user
    smtp_auth_password = s3cr3tP@ssw0rd
    smtp_server = localhost
    use_speech = 1
    use_stdout = 0
    use_smtp = 1

    [example]
    fs_uuid = 58EE-7CAE
    script = mkdir -p ./data
        rsync -av /data/to_backup/ ./data/

You can display the current config:

    udevbackup show
