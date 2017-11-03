#!/usr/bin/env python
import datetime
from cfbot import *

def run(num_branches_to_push):
  lock = try_lock()
  if not lock:
    # another copy is already running in this directory, so exit quietly (for
    # example if a cronjob starts before the last one has finished)
    return
  prepare_repo()
  commit_id = update_tree()
  
  # fetch the current and next commitfests
  commitfest = get_current_commitfest()
  next_commitfest = CommitFest(commitfest.id + 1)

  # prep both filesystems
  prepare_filesystem(commitfest.id)
  prepare_filesystem(next_commitfest.id)

  # now consider all the submissions for both as a single array
  submissions = commitfest.get_submissions() + next_commitfest.get_submissions()

  # reduce these to just "interesting" statuses
  submissions = filter(lambda s: s.status in ("Ready for Committer", "Needs review", "Waiting on Author"), submissions)
  
  with open("logs/cfbot.%s.log" % datetime.date.today().isoformat(), "a") as log:
    log.write("== starting at %s\n" % str(datetime.datetime.now()))
    log.write("commitfest = %s\n" % commitfest.id)
    log.write("commit = %s\n" % commit_id)
    log.flush()
    activity_message = check_n_submissions(log, commit_id, submissions, num_branches_to_push)
    log.write("== finishing at %s\n" % str(datetime.datetime.now()))
    log.flush()
  build_web_page(commit_id, commitfest.id, submissions, None, activity_message, "www/index.html")
  for author in unique_authors(submissions):
    build_web_page(commit_id, None, submissions, author, activity_message, "www/" + make_author_url(author))
  build_web_page(commit_id, next_commitfest.id, submissions, None, activity_message, "www/next.html")
  lock.close()

if __name__ == "__main__":
  num_branches_to_push = 0
  if len(sys.argv) > 1:
    num_branches_to_push = int(sys.argv[1])
  run(num_branches_to_push)
