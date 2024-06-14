#!/usr/bin/env python
#
# Poll the Commitfest app to synchronise our local database.  This doesn't
# do any other work, it just updates our "submission" table with information
# about the lastest message for each entry, creating rows as required.

import cfbot_commitfest_rpc
import cfbot_util

import logging

def pull_submissions(conn, commitfest_id):
  """Fetch the list of submissions and make sure we have a row for each one.
     Update the last email time according to the Commitfest main page,
     as well as name, status, authors in case they changed."""
  cursor = conn.cursor()
  for submission in cfbot_commitfest_rpc.get_submissions_for_commitfest(commitfest_id):
    # avoid writing for nothing by doing a read query first
    cursor.execute("""SELECT *
                        FROM submission
                       WHERE commitfest_id = %s
                         AND submission_id = %s
                         AND name = %s
                         AND status = %s
                         AND authors = %s
                         AND last_email_time = %s AT TIME ZONE 'UTC'""",
                   (commitfest_id, submission.id,
                    submission.name, submission.status, submission.authors, submission.last_email_time))
    if cursor.fetchone():
      # no change required
      continue
    cursor.execute("""INSERT INTO submission (commitfest_id, submission_id,
                                              name, status, authors,
                                              last_email_time)
                      VALUES (%s, %s, %s, %s, %s, %s AT TIME ZONE 'UTC')
                 ON CONFLICT (commitfest_id, submission_id) DO
                      UPDATE
                      SET name = EXCLUDED.name,
                          status = EXCLUDED.status,
                          authors = EXCLUDED.authors,
                          last_email_time = EXCLUDED.last_email_time""",
                   (commitfest_id, submission.id,
                    submission.name, submission.status, submission.authors, submission.last_email_time))
    conn.commit()

def pull_modified_threads(conn):
  """Check all threads we've never checked before, or whose last_email_time
     has moved.  We want to find the lastest message ID that has attachments
     that we understand, and remember that."""
  cursor = conn.cursor()
  cursor2 = conn.cursor()
  # don't look at threads that have changed in the last minute, because the
  # archives website seems to be a bit "eventually consistent" and it might not
  # yet show a recent message on the "flat" page
  cursor.execute("""SELECT commitfest_id, submission_id, last_email_time
                      FROM submission
                     WHERE last_email_time_checked IS NULL
                        OR (last_email_time_checked != last_email_time AND
                            last_email_time < now() - interval '1 minutes')""")
  for commitfest_id, submission_id, last_email_time in cursor:
    logging.info("checking commitfest %s submission %s" % (commitfest_id, submission_id))
    url = cfbot_commitfest_rpc.get_thread_url_for_submission(commitfest_id, submission_id)
    if url == None:
      message_id = None
    else:
      message_id, attachments = cfbot_commitfest_rpc.get_latest_patches_from_thread_url(url)
    cursor2.execute("""UPDATE submission
                          SET last_email_time_checked = %s,
                              last_message_id = %s
                              --last_branch_message_id = NULL
                        WHERE commitfest_id = %s
                          AND submission_id = %s""",
                    (last_email_time, message_id, commitfest_id, submission_id))
    conn.commit()

def push_build_results(conn):
  pass

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    commitfest_id = cfbot_commitfest_rpc.get_current_commitfest_id()
    pull_submissions(conn, commitfest_id)
    pull_submissions(conn, commitfest_id + 1)
    pull_modified_threads(conn)
    push_build_results(conn)
