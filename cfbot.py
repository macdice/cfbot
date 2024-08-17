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
    commitfest_id = cfbot_commitfest_rpc.get_current_commitfest_id()

    # pull in any build results that we are waiting for
    # XXX would need to aggregate the 'keep_polling' flag if we went
    # back to supporting multiple providers, or do something smarter,
    # but considering the plan to double-down on cirrus and switch to
    # webhooks, not bothering for now
    cfbot_cirrus.pull_build_results(conn)

    # exchange data with the Commitfest app
    logging.info("pulling submissions for current commitfest")
    cfbot_commitfest.pull_submissions(conn, commitfest_id)
    logging.info("pulling submissions for next commitfest")
    cfbot_commitfest.pull_submissions(conn, commitfest_id + 1)
    logging.info("pulling modified threads")
    cfbot_commitfest.pull_modified_threads(conn)

    # build one patch, if it is time for that
    cfbot_patch.maybe_process_one(conn, commitfest_id)

    # rebuild a new set of web pages
    cfbot_web.rebuild(conn, commitfest_id)

    # garbage collect old build results
    cfbot_util.gc(conn)

if __name__ == "__main__":
  # don't run if we're already running
  lock_fd = try_lock()
  if lock_fd:
    run()
    lock_fd.close()
