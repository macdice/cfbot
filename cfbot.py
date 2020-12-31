#!/usr/bin/env python

import cfbot_appveyor
import cfbot_cirrus
import cfbot_travis

import cfbot_commitfest
import cfbot_commitfest_rpc
import cfbot_config
import cfbot_patch
import cfbot_util
import cfbot_web
import fcntl

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
    if "appveyor" in cfbot_config.CI_MODULES:
      cfbot_appveyor.pull_build_results(conn)
    if "cirrus" in cfbot_config.CI_MODULES:
      cfbot_cirrus.pull_build_results(conn)
    if "travis" in cfbot_config.CI_MODULES:
      cfbot_travis.pull_build_results(conn)
      cfbot_travis.pull_build_results(conn)

    # exchange data with the Commitfest app
    cfbot_commitfest.pull_submissions(conn, commitfest_id)
    cfbot_commitfest.pull_submissions(conn, commitfest_id + 1)
    cfbot_commitfest.pull_modified_threads(conn)
    cfbot_commitfest.push_build_results(conn)

    # build one patch, if it is time for that
    cfbot_patch.maybe_process_one(conn)

    # rebuild a new set of web pages
    cfbot_web.rebuild(conn, commitfest_id)

    # garbage collect old build results
    cfbot_util.gc(conn)

if __name__ == "__main__":
  # don't run if we're already running
  if try_lock():
    run()

