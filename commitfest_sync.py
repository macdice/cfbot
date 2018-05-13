#!/usr/bin/env python
#
# Poll the Commitfest app to synchronise our local database.  This doesn't
# do any other work, it just updates our "submission" table with information
# about the lastest message for each entry, creating rows as required.

import commitfest_rpc
import psycopg2

DSN="dbname=cfbot"

def poll_commitfest(conn, commitfest_id):
  """Fetch the list of submissions and make sure we have a row for each one.
     Update the last email time according to the Commitfest main page,
     as well as name, status, authors in case they changed."""
  cursor = conn.cursor()
  for submission in commitfest_rpc.get_submissions_for_commitfest(commitfest_id):
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

def poll_modified_threads(conn):
  """Check all threads we've never checked before, or whose last_email_time
     has moved.  We want to find the lastest message ID that has attachments
     that we understand, and remember that."""
  cursor = conn.cursor()
  cursor2 = conn.cursor()
  cursor.execute("""SELECT commitfest_id, submission_id, last_email_time
                      FROM submission
                     WHERE last_email_time_checked IS NULL
                        OR last_email_time_checked != last_email_time""")
  for commitfest_id, submission_id, last_email_time in cursor:
    url = commitfest_rpc.get_thread_url_for_submission(commitfest_id, submission_id)
    message_id, attachments = commitfest_rpc.get_latest_patches_from_thread_url(url)
    cursor2.execute("""UPDATE submission
                          SET last_email_time_checked = %s,
                              last_message_id = %s
                        WHERE commitfest_id = %s
                          AND submission_id = %s""",
                    (last_email_time, message_id, commitfest_id, submission_id))
    conn.commit()

if __name__ == "__main__":
  conn = psycopg2.connect(DSN)
  commitfest_id = commitfest_rpc.get_current_commitfest_id()
  poll_commitfest(conn, commitfest_id)
  poll_commitfest(conn, commitfest_id + 1)
  poll_modified_threads(conn)
  conn.close()
