
# settings used when polling Commitfest URLs
SLOW_FETCH_SLEEP = 1.0
USER_AGENT = "cfbot from http://commitfest.cputube.org"
TIMEOUT = 10

# database settings
DSN="dbname=cfbot"

# travis settings
TRAVIS_USER="macdice"
TRAVIS_REPO="postgres"
TRAVIS_API_BUILDS="https://api.travis-ci.org/repos/" + TRAVIS_USER + "/" + TRAVIS_REPO + "/builds"
TRAVIS_BUILD_URL="https://travis-ci.org/" + TRAVIS_USER + "/" + TRAVIS_REPO + "/builds/%s"

