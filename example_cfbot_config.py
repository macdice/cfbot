import logging
import sys
import platform
import shutil

# Fill in your GitHub username here. If this is None, we won't push to GitHub
# unless you change GIT_REMOTE_NAME.
GITHUB_USER = None
GITHUB_REPO = "postgres"

GITHUB_FULL_REPO = f"{GITHUB_USER}/{GITHUB_REPO}"

# Some settings are different for our production server
PRODUCTION = False

# commitfest integration settiongs
COMMITFEST_HOST = "https://commitfest.postgresql.org"
COMMITFEST_SHARED_SECRET = "INSECURE"
COMMITFEST_POST_URL = "http://localhost:8007/cfbot_notify/"

# If we receive "push" notifications matching these settings, we'll
# automatically mirror them to branches of the same name in our output repo (if
# configured above).
GITHUB_MIRROR_USER = "postgres"
GITHUB_MIRROR_REPO = "postgres"
GITHUB_MIRROR_FULL_REPO = f"{GITHUB_MIRROR_USER}/{GITHUB_MIRROR_REPO}"
GITHUB_MIRROR_BRANCH_PATTERN = r"^(master|REL_[0-9]+_STABLE)$"

GITHUB_TOKENS = {
    # "postgres/postgres" : "token_goes_here",
    # ...
}

# Paths that we don't allow patches to modify, to prevent privilege escalation
# of Github Actions.
PUSH_BLOCKED_PATTERN = r"^\.github/workflows/.*$"

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
    else:
        PATCHBURNER_CTL = "./cfbot_patchburner_docker_ctl.sh"
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

# data retention, in days
RETENTION_LARGE_OBJECTS = 2
RETENTION_ALL = 90
