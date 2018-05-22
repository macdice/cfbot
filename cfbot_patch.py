#!/usr/bin/env python
#
# Figure out which submission most needs to be pushed into a new branch for
# building and testing.  Goals:
#
# 1.  Don't do anything if we're still waiting for build results from too
#     many branches from any given provider.  This limits our resource
#     consumption.
# 2.  The top priority is noticing newly posted patches.  So find the least
#     recent submission whose last message ID has changed since our last
#     branch.
# 3.  If we can't find any of those, then just rebuild every patch at a rate
#     that will get though them all every 48 hours, to check for bitrot.

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urlparse

def need_to_limit_rate(conn):
  """Have we pushed too many branches recently?"""
  # Don't let any provider finish up with more than the configured maximum
  # number of builds still running.
  cursor = conn.cursor()
  cursor.execute("""SELECT COUNT(*)
                      FROM build_result
                     WHERE result IS NULL
                  GROUP BY provider
	              ORDER BY 1 DESC""")
  row = cursor.fetchone()
  return row and row[0] >= cfbot_config.CONCURRENT_BUILDS

def choose_submission_with_new_patch(conn):
  """Return the ID pair for the submission most deserving, because it has been
     waiting the longest amonst submissions that have a new patch
     available."""
  # we'll use the last email time as an approximation of the time the patch
  # was sent, because it was most likely that message and it seems like a
  # waste of time to use a more accurate time for the message with the
  # attachment
  # -- wait a couple of minutes before probing because the archives are slow!
  cursor = conn.cursor()
  cursor.execute("""SELECT commitfest_id, submission_id
                      FROM submission
                     WHERE last_message_id IS NOT NULL
                       AND last_message_id IS DISTINCT FROM last_branch_message_id
                       AND status IN ('Ready for Committer', 'Needs review', 'Waiting on Author')
                  ORDER BY last_email_time
                     LIMIT 1""")
  row = cursor.fetchone()
  if row:
    return row
  else:
    return None, None

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
                       AND status IN ('Ready for Committer', 'Needs review', 'Waiting on Author')""")
  number, = cursor.fetchone()
  # how many will we need to do per hour to approximate our target rate?
  target_per_hour = number / cfbot_config.CYCLE_TIME
  # are we currently above or below our target rate?
  cursor.execute("""SELECT COUNT(*)
                      FROM submission
                     WHERE last_message_id IS NOT NULL
                       AND status IN ('Ready for Committer', 'Needs review', 'Waiting on Author')
                       AND last_branch_time > now() - INTERVAL '1 hour'""")
  current_rate_per_hour, = cursor.fetchone()
  # is it time yet?
  if current_rate_per_hour < target_per_hour:
    cursor.execute("""SELECT commitfest_id, submission_id
                        FROM submission
                       WHERE last_message_id IS NOT NULL
                         AND status IN ('Ready for Committer', 'Needs review', 'Waiting on Author')
                    ORDER BY last_branch_time NULLS FIRST
                       LIMIT 1""")
    row = cursor.fetchone()
    if row:
      return row
    else:
      return None, None
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
                        message_id, commit_id, ci_commit_id, result, url):
  cursor = conn.cursor()
  cursor.execute("""INSERT INTO build_result (commitfest_id, submission_id,
                                              provider, message_id,
                                              master_commit_id, ci_commit_id,
                                              result,
                                              url, modified, created)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())""",
                 (commitfest_id, submission_id, provider, message_id, commit_id,
                  ci_commit_id, result, url))

def make_branch(conn, burner_repo_path, commitfest_id, submission_id, message_id):
  branch = "commitfest/%s/%s" % (commitfest_id, submission_id)
  logging.info("creating branch %s" % branch)
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
the email thread, and also periodically to check for bitrot caused by changes
on the master branch.

Commitfest entry: https://commitfest.postgresql.org/%s/%s
Patch(es): https://www.postgresql.org/message-id/%s
Author(s): %s
""" % (commitfest_id, submission_id, name, commitfest_id, submission_id, message_id, ", ".join(authors))
  # commit!
  with tempfile.NamedTemporaryFile() as tmp:
    tmp.write(commit_message)
    tmp.flush()
    subprocess.check_call("""cd %s && git commit -q -F %s""" % (burner_repo_path, tmp.name), shell=True)
  return branch

def patchburner_ctl(command, want_rcode=False):
  """Invoke the patchburner control script."""
  if want_rcode:
    p = subprocess.Popen("""%s %s""" % (cfbot_config.PATCHBURNER_CTL, command), shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = p.stdout.read()
    rcode = p.wait()
    return output, rcode
  else:
    return subprocess.check_output("%s %s" % (cfbot_config.PATCHBURNER_CTL, command), shell=True)
    
def process_submission(conn, commitfest_id, submission_id):
  cursor = conn.cursor()
  template_repo_path = patchburner_ctl("template-repo-path").strip()
  burner_repo_path = patchburner_ctl("burner-repo-path").strip()
  patch_dir = patchburner_ctl("burner-patch-path").strip()
  #print "got %s" % update_patchbase_tree()
  commit_id = get_commit_id(template_repo_path)
  logging.info("processing submission %d, %d" % (commitfest_id, submission_id))
  # create a fresh patchburner jail
  patchburner_ctl("destroy")
  patchburner_ctl("create")
  # find out where to put the patches so the jail can see them
  # fetch the patches from the thread and put them in the patchburner's
  # filesystem
  time.sleep(10) # argh, try to close race against slow archives
  thread_url = cfbot_commitfest_rpc.get_thread_url_for_submission(commitfest_id, submission_id)
  message_id, patch_urls = cfbot_commitfest_rpc.get_latest_patches_from_thread_url(thread_url)
  for patch_url in patch_urls:
    parsed = urlparse.urlparse(patch_url)
    filename = os.path.basename(parsed.path)
    dest = os.path.join(patch_dir, filename)
    with open(dest, "w+") as f:
      f.write(cfbot_util.slow_fetch(patch_url))
  # apply the patches inside the jail
  output, rcode = patchburner_ctl("apply", want_rcode=True)
  # write the patch output to a public log file
  log_file = "patch_%d_%d.log" % (commitfest_id, submission_id)
  with open(os.path.join(cfbot_config.WEB_ROOT, log_file), "w+") as f:
    f.write(output)
  log_url = cfbot_config.CFBOT_APPLY_URL % log_file
  # did "patch" actually succeed?
  if rcode != 0:
    # we failed to apply the patches
    insert_build_result(conn, commitfest_id, submission_id, 'apply',
                        message_id, commit_id, log_url, 'failure', log_url)
  else:
    # we applied the patch; now make it into a branch with a commit on it
    # including the CI control files for all enabled providers
    for d in cfbot_config.CI_PROVIDERS:
      for f in os.listdir(d):
        s = os.path.join(d, f)
        if os.path.isfile(s):
          shutil.copy(s, os.path.join(burner_repo_path, f))
    branch = make_branch(conn, burner_repo_path, commitfest_id, submission_id, message_id)
    # push it to the remote monitored repo, if configured
    if cfbot_config.GIT_REMOTE_NAME:
      logging.info("pushing branch %s" % branch)
      my_env = os.environ.copy()
      my_env["GIT_SSH_COMMAND"] = cfbot_config.GIT_SSH_COMMAND
      subprocess.check_call("cd %s && git push -q -f %s %s" % (burner_repo_path, cfbot_config.GIT_REMOTE_NAME, branch), env=my_env, shell=True)
    # record the build status
    ci_commit_id = get_commit_id(burner_repo_path)
    insert_build_result(conn, commitfest_id, submission_id, 'apply',
                        message_id, commit_id, ci_commit_id, 'success', log_url)
    # create placeholder results for the CI providers (we'll start polling them)
    for provider in cfbot_config.CI_PROVIDERS:
      insert_build_result(conn, commitfest_id, submission_id, provider,
                          message_id, commit_id, ci_commit_id, None, None)
  # record that we have processed this commit ID and message ID
  #
  # Unfortunately we also have to clobber last_message_id to avoid getting
  # stuck in a loop, because sometimes the commitfest app reports a change
  # in last email date before the new email is visible in the flat thread (!),
  # which means that we can miss a new patch.  Doh.  Need something better
  # here (don't really want to go back to polling threads aggressively...)
  cursor.execute("""UPDATE submission
                       SET last_message_id = %s,
                           last_branch_message_id = %s,
                           last_branch_commit_id = %s,
                           last_branch_time = now()
                     WHERE commitfest_id = %s AND submission_id = %s""",
                 (message_id, message_id, commit_id, commitfest_id, submission_id))
  conn.commit()
  patchburner_ctl("destroy")

def maybe_process_one(conn):
  if not need_to_limit_rate(conn):
    commitfest_id, submission_id = choose_submission(conn)
    if submission_id:
      process_submission(conn, commitfest_id, submission_id)
 
if __name__ == "__main__":
  with cfbot_util.db() as conn:
    maybe_process_one(conn)
