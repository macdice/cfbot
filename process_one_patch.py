#!/usr/bin/env python
#
# Figure out which submission most needs to be pushed into a new branch for
# building and testing.  Goals:
#
# 1.  Don't do anything if we've already pushed 3 branches in the past 15
#     minutes.  This limits our resource consumption on the CI providers.
# 2.  The top priority is noticing newly posted patches.  So find the least
#     recent submission whose last message ID has changed since our last
#     branch.
# 3.  If we can't find any of those, then just rebuild every patch at a rate
#     that will get though them all every 48 hours, to check for bitrot.

import commitfest_rpc
import psycopg2
import os
import subprocess
import tempfile
import urlparse

CONCURRENT_BUILDS = 3
CYCLE_TIME = 48.0

DSN="dbname=cfbot"
PATCHBURNER_CTL="./cfbot_patchburner_ctl.sh"

def need_to_limit_rate(conn):
  """Have we pushed too many branches recently?"""
  # It takes about 15 minutes to run our full build on Travis CI, so we can
  # use that fact to make a stupid approximation of how many we can build
  # at once.  They hard limit us to 5, let's soft limit ourselves to 3.
  cursor = conn.cursor()
  cursor.execute("""SELECT COUNT(*)
                      FROM submission
                     WHERE last_branch_time > now() - INTERVAL '15 minutes'""")
  number, = cursor.fetchone()
  return number >= CONCURRENT_BUILDS

def choose_submission_with_new_patch(conn):
  """Return the ID pair for the submission most deserving, because it has been
     waiting the longest amonst submissions that have a new patch
     available."""
  # we'll use the last email time as an approximation of the time the patch
  # was sent, because it was most likely that message and it seems like a
  # waste of time to use a more accurate time for the message with the
  # attachment
  cursor = conn.cursor()
  cursor.execute("""SELECT commitfest_id, submission_id
                      FROM submission
                     WHERE last_message_id IS NOT NULL
                       AND last_message_id IS DISTINCT FROM last_branch_message_id
                       AND status IN ('Ready for Committer', 'Needs review')
                  ORDER BY last_email_time
                     LIMIT 1""")
  return cursor.fetchone()

def choose_submission_without_new_patch(conn):
  """Return the ID pair for the submission that has been waiting longest for
     a periodic bitrot check, but only if we're under the configured rate per
     hour (which is expressed as the cycle time to get through all
     submissions)."""
  # how many submissions are there?
  cursor = conn.cursor()
  cursor.execute("""SELECT COUNT(*)
                      FROM submission
                     WHERE last_message_id IS NOT NULL
                       AND status IN ('Ready for Committer', 'Needs review')""")
  number, = cursor.fetchone()
  # how many will we need to do per hour to approximate our target rate?
  target_per_hour = number / CYCLE_TIME
  # are we currently above or below our target rate?
  cursor.execute("""SELECT COUNT(*)
                      FROM submission
                     WHERE last_message_id IS NOT NULL
                       AND status IN ('Ready for Committer', 'Needs review')
                       AND last_branch_time > now() - INTERVAL '1 hour'""")
  current_rate_per_hour, = cursor.fetchone()
  # is it time yet?
  if current_rate_per_hour < target_per_hour:
    cursor.execute("""SELECT commitfest_id, submission_id
                        FROM submission
                       WHERE last_message_id IS NOT NULL
                         AND status IN ('Ready for Committer', 'Needs review')
                    ORDER BY last_branch_time NULLS FIRST
                       LIMIT 1""")
    return cursor.fetchone()
  else:
    return None, None

def choose_submission(conn):
  """Choose the best submission to process, giving preference to new
     patches."""
  commitfest_id, submission_id = choose_submission_with_new_patch(conn)
  if submission_id:
    return commitfest_id, submission_id
  commitfest_id, submission_id = choose_submission_without_new_patch(conn)
  return commitfest_id, submission_id

def update_patchbase_tree(repo_dir):
  """Pull changes from PostgreSQL master and return the HEAD commit ID."""
  subprocess.call("cd %s && git checkout . > /dev/null && git clean -fd > /dev/null && git checkout -q master && git pull -q" % repo_dir, shell=True)

def get_commit_id(repo_dir):
  return subprocess.check_output("cd %s && git show | head -1 | cut -d' ' -f2" % repo_dir, shell=True).strip()

def insert_build_result(conn, commitfest_id, submission_id, provider,
                        message_id, commit_id, ci_commit_id, result, output):
  cursor = conn.cursor()
  cursor.execute("""INSERT INTO build_result (commitfest_id, submission_id,
                                              provider, message_id,
                                              master_commit_id, ci_commit_id,
                                              result,
                                              message, modified, created)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())""",
                 (commitfest_id, submission_id, provider, message_id, commit_id,
                  ci_commit_id, result, output))

def make_branch(conn, burner_repo_path, commitfest_id, submission_id, message_id):
  branch = "commitfest/%s/%s" % (commitfest_id, submission_id)
  # blow away the branch if it exists already
  subprocess.call("""cd %s && git branch -q -D %s > /dev/null 2> /dev/null""" % (burner_repo_path, branch), shell=True) # ignore failure
  # create a new one
  subprocess.check_call("""cd %s && git checkout -q -b %s""" % (burner_repo_path, branch), shell=True)
  # add all changes
  subprocess.check_call("""cd %s && git add -A""" % (burner_repo_path,), shell=True)
  # look up the data we need to make a friendly commit message
  cursor = conn.cursor()
  cursor.execute("""SELECT name, authors FROM submission WHERE commitfest_id = %s AND submission_id = %s""",
                 (commitfest_id, submission_id))
  name, authors = cursor.fetchone()
  # compose the commit message
  commit_message = """[CF %s/%s] %s

This commit was automatically generated by cfbot at commitfest.cputube.org.
It is based on patches submitted to the PostgreSQL mailing lists and
registered in the PostgreSQL Commitfest application.

This branch will be overwritten each time a new patch version is posted to
the email thread or the master branch changes.

Commitfest entry: https://commitfest.postgresql.org/%s/%s
Patch(es): https://www.postgresql.org/message-id/%s
Author(s): %s
""" % (commitfest_id, submission_id, name, commitfest_id, submission_id, message_id, ", ".join(authors))
  # commit!
  with tempfile.NamedTemporaryFile() as tmp:
    tmp.write(commit_message)
    tmp.flush()
    subprocess.check_call("""cd %s && git commit -q -F %s""" % (burner_repo_path, tmp.name), shell=True)

def process_submission(conn, commitfest_id, submission_id):
  cursor = conn.cursor()
  template_repo_path = subprocess.check_output("%s template-repo-path" % PATCHBURNER_CTL, shell=True).strip()
  burner_repo_path = subprocess.check_output("%s burner-repo-path" % PATCHBURNER_CTL, shell=True).strip()
  patch_dir = subprocess.check_output("%s burner-patch-path" % PATCHBURNER_CTL, shell=True).strip()
  #print "got %s" % update_patchbase_tree()
  commit_id = get_commit_id(template_repo_path)
  print "processing %d, %d" % (commitfest_id, submission_id)
  # create a fresh patchburner jail
  subprocess.call("""sudo %s destroy""" % (PATCHBURNER_CTL,), shell=True)
  subprocess.call("""sudo %s create""" % (PATCHBURNER_CTL,), shell=True)
  # find out where to put the patches so the jail can see them
  # fetch the patches from the thread and put them in the patchburner's
  # filesystem
  thread_url = commitfest_rpc.get_thread_url_for_submission(commitfest_id, submission_id)
  message_id, patch_urls = commitfest_rpc.get_latest_patches_from_thread_url(thread_url)
  for patch_url in patch_urls:
    parsed = urlparse.urlparse(patch_url)
    filename = os.path.basename(parsed.path)
    dest = os.path.join(patch_dir, filename)
    with open(dest, "w+") as f:
      f.write(commitfest_rpc.slow_fetch(patch_url))
  # apply the patches inside the jail
  p = subprocess.Popen("""sudo %s apply""" % (PATCHBURNER_CTL,), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
  output = p.stdout.read()
  rcode = p.wait()
  if rcode != 0:
    # we failed to apply the patches
    insert_build_result(conn, commitfest_id, submission_id, 'apply',
                        message_id, commit_id, None, 'failure', output)
  else:
    # we applied the patch; now make it into a branch with a commit on it
    # TODO: add the CI control files
    make_branch(conn, burner_repo_path, commitfest_id, submission_id, message_id)
    ci_commit_id = get_commit_id(burner_repo_path)
    insert_build_result(conn, commitfest_id, submission_id, 'apply',
                        message_id, commit_id, ci_commit_id, 'success', output)
    # create placeholder results for the CI providers (we'll start polling them)
    insert_build_result(conn, commitfest_id, submission_id, 'travis',
                        message_id, commit_id, ci_commit_id, None, None)
    insert_build_result(conn, commitfest_id, submission_id, 'appveyor',
                        message_id, commit_id, ci_commit_id, None, None)
  # record that we have processed this commit ID and message ID
  cursor.execute("""UPDATE submission
                       SET last_branch_message_id = %s,
                           last_branch_commit_id = %s,
                           last_branch_time = now()
                     WHERE commitfest_id = %s AND submission_id = %s""",
                 (message_id, commit_id, commitfest_id, submission_id))
  conn.commit()
  #subprocess.call("""sudo %s destroy""" % (PATCHBURNER_CTL,), shell=True)
  
if __name__ == "__main__":
  conn = psycopg2.connect(DSN)
  if not need_to_limit_rate(conn):
    commitfest_id, submission_id = choose_submission(conn)
    if submission_id:
      process_submission(conn, commitfest_id, submission_id)
  conn.close()
