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

        # pull in any build results that we are waiting for
        # XXX would need to aggregate the 'keep_polling' flag if we went
        # back to supporting multiple providers, or do something smarter,
        # but considering the plan to double-down on cirrus and switch to
        # webhooks, not bothering for now
        cfbot_cirrus.pull_build_results(conn)

        # exchange data with the Commitfest app
        for name, cf in cfs.items():
            if cf is None:
                logging.info(f"skipping pulling submissions for {name} commitfest")
                continue

            logging.info(f"pulling submissions for {name} commitfest")
            cfbot_commitfest.pull_submissions(conn, cf["id"])

        cf_ids = [cf["id"] for cf in cfs.values() if cf is not None]

        cfbot_commitfest.pull_modified_threads(conn)

        # build one patch, if it is time for that
        cfbot_patch.maybe_process_one(conn, cf_ids)

        # rebuild a new set of web pages
        cfbot_web.rebuild(conn, cfs, cf_ids)

        # garbage collect old build results
        cfbot_util.gc(conn)


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
