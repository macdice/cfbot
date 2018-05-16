import logging
import sys

# which CI providers are enabled
CI_PROVIDERS = ("appveyor", "travis")

# http settings (be polite by identifying ourselves and limited rate)
SLOW_FETCH_SLEEP = 1.0
USER_AGENT = "cfbot from http://commitfest.cputube.org"
TIMEOUT = 10

LOCK_FILE="/tmp/cfbot-lock"

# database settings
DSN="dbname=cfbot"

# patch settings
PATCHBURNER_CTL="sudo ./cfbot_patchburner_ctl.sh"
CYCLE_TIME = 48.0
CONCURRENT_BUILDS = 300

# travis settings
TRAVIS_USER="macdice"
TRAVIS_REPO="postgres"
TRAVIS_API_BUILDS="https://api.travis-ci.org/repos/" + TRAVIS_USER + "/" + TRAVIS_REPO + "/builds"
TRAVIS_BUILD_URL="https://travis-ci.org/" + TRAVIS_USER + "/" + TRAVIS_REPO + "/builds/%s"

# appveyor settings
APPVEYOR_USER="macdice"
APPVEYOR_REPO="postgres"
APPVEYOR_API_BUILDS="https://ci.appveyor.com/api/projects/"+ APPVEYOR_USER + "/" + APPVEYOR_REPO + "/history?recordsNumber=10"
APPVEYOR_BUILD_URL="https://ci.appveyor.com/project/"+ APPVEYOR_USER + "/" + APPVEYOR_REPO + "/build/%s"

# git settings
#GIT_SSH_COMMAND="ssh -i ~/.ssh/cfbot_github_rsa"
GIT_SSH_COMMAND="ssh"
#GIT_REMOTE_NAME="cfbot-repo"
GIT_REMOTE_NAME="macdice"

# http output
WEB_ROOT="www"
CFBOT_APPLY_URL="https://cfbot.cputube.org/log/%s"

# log settings
logging.basicConfig(format='%(asctime)s %(message)s', filename="cfbot.log", level=logging.INFO)

