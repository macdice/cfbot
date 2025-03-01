import logging
import sys
import platform
import shutil

# Fill in your GitHub username here. If this is None, we won't push to GitHub
# unless you change GIT_REMOTE_NAME.
GITHUB_USER = None
GITHUB_REPO = "postgres"

GITHUB_FULL_REPO = f"{GITHUB_USER}/{GITHUB_REPO}"

# which CI providers are enabled
CI_MODULES = ("cirrus",)

# Some settings are different for our production server
PRODUCTION = False

# http settings (be polite by identifying ourselves and limited rate)
# SLOW_FETCH_SLEEP = 1.0
# SLOW_FETCH_SLEEP = 0.1
SLOW_FETCH_SLEEP = 0.0
USER_AGENT = "cfbot from http://cfbot.cputube.org"
TIMEOUT = 20

LOCK_FILE = "/tmp/cfbot-lock"

# database settings
DSN = "cfbot"

# patch settings
if platform.system() == "Linux":
    if shutil.which("podman"):
        PATCHBURNER_CTL = "./cfbot_patchburner_podman_ctl.sh"
    elif shutil.which("docker"):
        PATCHBURNER_CTL = "./cfbot_patchburner_docker_ctl.sh"
    else:
        PATCHBURNER_CTL = "./cfbot_patchburner_chroot_ctl.sh"
else:
    PATCHBURNER_CTL = "sudo /usr/local/sbin/cfbot_patchburner_ctl.sh"

CYCLE_TIME = 48.0
CONCURRENT_BUILDS = 4
# work queue worker settings
CONCURRENT_QUEUE_WORKERS = 4

# cirrus settings
CIRRUS_USER = GITHUB_USER
CIRRUS_REPO = GITHUB_REPO

# git settings
if PRODUCTION:
    GIT_SSH_COMMAND = "ssh -i ~/.ssh/cfbot_github_rsa"
    GIT_REMOTE_NAME = "cfbot-repo"
else:
    GIT_SSH_COMMAND = "ssh"
    if GITHUB_USER:
        GIT_REMOTE_NAME = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    else:
        GIT_REMOTE_NAME = None

# http output
WEB_ROOT = "www"
CFBOT_APPLY_URL = "http://cfbot.cputube.org/%s"

# log settings
if PRODUCTION:
    logging.basicConfig(
        format="%(asctime)s %(message)s", filename="cfbot.log", level=logging.INFO
    )
else:
    logging.basicConfig(
        format="%(asctime)s %(message)s", stream=sys.stderr, level=logging.INFO
    )
