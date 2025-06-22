import cfbot_config
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
    get_http_session().post(
        url,
        headers={"User-Agent": cfbot_config.USER_AGENT},
        json=d,
        timeout=cfbot_config.TIMEOUT,
    )


def gc(conn):
    cursor = conn.cursor()
    cursor.execute("""DELETE FROM task WHERE created < now() - interval '6 months'""")
    cursor.execute(
        """DELETE FROM task WHERE created < now() - interval '4 hours' AND status = 'EXECUTING'"""
    )  # ?
    cursor.execute("""DELETE FROM branch WHERE created < now() - interval '6 months'""")

    # timing out branches is now done in cfbot_cirrus.py pull_build_results()
    # cursor.execute(
    #    """UPDATE branch SET status = 'timeout' WHERE created < now() - interval '2 hours' AND status = 'testing'"""
    # )
    # TODO: GC the git tree too!
    conn.commit()


def db():
    """Get a database connection."""
    return pg8000.connect(cfbot_config.DSN)
