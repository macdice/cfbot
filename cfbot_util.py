import cfbot_config
import errno
import fcntl
import pg8000
import requests
import time
import json

global_http_session = None


def get_http_session():
    """A session allowing for HTTP connection reuse."""
    global global_http_session
    if global_http_session is None:
        global_http_session = requests.Session()
    return global_http_session


def slow_fetch(url, none_for_404=False):
    """Fetch the body of a web URL, but sleep every time too to be kind to the
    commitfest server."""
    response = get_http_session().get(
        url,
        headers={"User-Agent": cfbot_config.USER_AGENT},
        timeout=cfbot_config.TIMEOUT,
    )
    if response.status_code == 404 and none_for_404:
        return None
    response.raise_for_status()
    time.sleep(cfbot_config.SLOW_FETCH_SLEEP)
    return response.text


def slow_fetch_binary(url, none_for_404=False):
    """Fetch the body of a web URL, but sleep every time too to be kind to the
    commitfest server."""
    response = get_http_session().get(
        url,
        headers={"User-Agent": cfbot_config.USER_AGENT},
        timeout=cfbot_config.TIMEOUT,
    )
    if response.status_code == 404 and none_for_404:
        return None
    response.raise_for_status()
    time.sleep(cfbot_config.SLOW_FETCH_SLEEP)
    return response.content


def slow_fetch_json(url, none_for_404=False):
    """Fetch the body of a web URL, but sleep every time too to be kind to the
    commitfest server."""
    response = get_http_session().get(
        url,
        headers={"User-Agent": cfbot_config.USER_AGENT},
        timeout=cfbot_config.TIMEOUT,
    )
    if response.status_code == 404 and none_for_404:
        return None
    response.raise_for_status()
    time.sleep(cfbot_config.SLOW_FETCH_SLEEP)
    return json.loads(response.content)


def post(url, d):
    response = get_http_session().post(
        url,
        headers={"User-Agent": cfbot_config.USER_AGENT},
        json=d,
        timeout=cfbot_config.TIMEOUT,
    )
    response.raise_for_status()


def lock():
    """Big lock to serialise operations that interact with containers,
    template source tree, web directory...

    """
    ### XXX We should really have finer grained locks... no need for
    ### the crusty web stuff to block source tree manipulations...
    fd = open(cfbot_config.LOCK_FILE, "w")
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def try_lock():
    fd = open(cfbot_config.LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except IOError as e:
        if e.errno != errno.EAGAIN:
            raise
        else:
            return None


def db():
    """Get a database connection."""
    return pg8000.connect(cfbot_config.DSN)
