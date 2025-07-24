#!/usr/bin/env python3

import cfbot_cirrus

import cfbot_commitfest
import cfbot_commitfest_rpc
import cfbot_config
import cfbot_patch
import cfbot_util
import cfbot_web

import errno
import fcntl
import logging
import requests


def try_lock():
    """Make sure that only one copy runs."""
    fd = open(cfbot_config.LOCK_FILE, "w")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except IOError as e:
        if e.errno != errno.EAGAIN:
            raise
        else:
            return None


def run():
    with cfbot_util.db() as conn:
        # get the current Commitfest ID
        cfs = cfbot_commitfest_rpc.get_current_commitfests()

        # Look for stuck builds, in case have missed a webhook or it is time to
        # time out.
        cfbot_cirrus.check_stale_branches(conn)
        cfbot_cirrus.check_stale_builds(conn)
        cfbot_cirrus.check_stale_tasks(conn)
        conn.commit()

        # XXX We should get this information by receiving a POST from the
        # cfapp on a new endpoint.  We'd probably want to poll for missed
        # updates occasionally, but using proper JSON endpoints instead of
        # scraping, and with low frequency since it'd only be a last resort way
        # to stay in sync.
        for name, cf in cfs.items():
            if cf is None:
                # logging.info(f"skipping pulling submissions for {name} commitfest")
                continue

            # logging.info(f"pulling submissions for {name} commitfest")
            cfbot_commitfest.pull_submissions(conn, cf["id"])

        cf_ids = [cf["id"] for cf in cfs.values() if cf is not None]

        cfbot_commitfest.pull_modified_threads(conn)

        # XXX This should probably become a work_queue job so that it can also
        # be queued when a build finishes (instead of waiting for this cron job
        # to run again), but first we need more sophisticated rate limiting
        # with the requisite interlocking to make it reliable.
        cfbot_patch.maybe_process_one(conn, cf_ids)

        # XXX We should probably stop building web pages, or if we're going to
        # keep doing it, build them with work_queue jobs when relevant data
        # changes, not every minute, or just make real dynamic pages with
        # Flask?
        cfbot_web.rebuild(conn, cfs, cf_ids)


if __name__ == "__main__":
    # don't run if we're already running
    lock_fd = try_lock()
    if lock_fd:
        try:
            run()
        except requests.exceptions.ReadTimeout:
            logging.error("Failed to process due to a timeout")
        except requests.exceptions.ConnectionError:
            logging.error("Failed to process due to a connection error")
        except requests.exceptions.HTTPError as e:
            logging.error("Failed to process due to an HTTP error: %s", e)
        except Exception as e:
            logging.error("Some unexpected error occured: %s", e)
            raise
        lock_fd.close()
