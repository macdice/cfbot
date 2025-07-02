#!/usr/bin/env python
#
# Poll the Commitfest app to synchronise our local database.  This doesn't
# do any other work, it just updates our "submission" table with information
# about the lastest message for each entry, creating rows as required.

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import json

import logging


def pull_submissions(conn, commitfest_id):
    """Fetch the list of submissions and make sure we have a row for each one.
    Update the last email time according to the Commitfest main page,
    as well as name, status, authors in case they changed."""
    cursor = conn.cursor()
    for submission in cfbot_commitfest_rpc.get_submissions_for_commitfest(
        commitfest_id
    ):
        # avoid writing for nothing by doing a read query first
        cursor.execute(
            """SELECT *
                        FROM submission
                       WHERE commitfest_id = %s
                         AND submission_id = %s
                         AND name = %s
                         AND status = %s
                         AND authors = %s
                         AND last_email_time = %s AT TIME ZONE 'UTC'""",
            (
                commitfest_id,
                submission.id,
                submission.name,
                submission.status,
                submission.authors,
                submission.last_email_time,
            ),
        )
        if cursor.fetchone():
            # no change required
            continue
        # Sending an email to a thread will clear the backoff
        # caused by earlier failures.  That's not quite what we want, we'd
        # rather clear it only when a new patch version is posted!
        cursor.execute(
            """INSERT INTO submission (commitfest_id, submission_id,
                                              name, status, authors,
                                              last_email_time)
                      VALUES (%s, %s, %s, %s, %s, %s AT TIME ZONE 'UTC')
                 ON CONFLICT (commitfest_id, submission_id) DO
                      UPDATE
                      SET name = EXCLUDED.name,
                          status = EXCLUDED.status,
                          authors = EXCLUDED.authors,
                          last_email_time = EXCLUDED.last_email_time,
                          backoff_until = NULL,
                          last_backoff = NULL""",
            (
                commitfest_id,
                submission.id,
                submission.name,
                submission.status,
                submission.authors,
                submission.last_email_time,
            ),
        )
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
        logging.info(
            "checking commitfest %s submission %s" % (commitfest_id, submission_id)
        )
        url = cfbot_commitfest_rpc.get_thread_url_for_submission(
            commitfest_id, submission_id
        )
        if url is None:
            message_id = None
        else:
            message_id, attachments = (
                cfbot_commitfest_rpc.get_latest_patches_from_thread_url(url)
            )
        cursor2.execute(
            """UPDATE submission
                          SET last_email_time_checked = %s,
                              last_message_id = %s
                              --last_branch_message_id = NULL
                        WHERE commitfest_id = %s
                          AND submission_id = %s""",
            (last_email_time, message_id, commitfest_id, submission_id),
        )
        conn.commit()


def make_branch_status_message(conn, branch_id=None, commit_id=None):
    assert branch_id or commit_id

    filter_column = "id" if branch_id else "commit_id"

    cursor = conn.cursor()
    cursor.execute(
        f"""SELECT id, commit_id, submission_id, url, status, created, modified,
                            version, patch_count,
                            first_additions, first_deletions,
                            all_additions, all_deletions
                      FROM branch
                     WHERE {filter_column} = %s""",
        (branch_id or commit_id,),
    )
    (
        branch_id,
        commit_id,
        submission_id,
        url,
        status,
        created,
        modified,
        version,
        patch_count,
        first_additions,
        first_deletions,
        all_additions,
        all_deletions,
    ) = cursor.fetchone()
    message = {
        "submission_id": submission_id,
        "branch_name": "cf/%d" % submission_id,
        "branch_id": branch_id,
        "commit_id": commit_id,
        "apply_url": url,
        "status": status,
        "created": created.isoformat(),
        "modified": modified.isoformat(),
        "version": version,
        "patch_count": patch_count,
        "first_additions": first_additions,
        "first_deletions": first_deletions,
        "all_additions": all_additions,
        "all_deletions": all_deletions,
    }
    return message


def make_task_status_message(conn, task_id):
    cursor = conn.cursor()
    cursor.execute(
        """SELECT commit_id, task_name, position, status, created, modified
                      FROM task
                     WHERE task_id = %s""",
        (task_id,),
    )
    commit_id, task_name, position, status, created, modified = cursor.fetchone()
    message = {
        "task_id": task_id,
        "commit_id": commit_id,
        "task_name": task_name,
        "position": position,
        "status": status,
        "created": created.isoformat(),
        "modified": modified.isoformat(),
    }
    return message


def make_task_update_message(conn, task_id):
    task_status = make_task_status_message(conn, task_id)
    if task_status["status"] in ("CREATED", "PAUSED"):
        # don't post tasks in these states for now, commitfest app does not
        # handle these (yet)
        return None

    branch_status = make_branch_status_message(conn, commit_id=task_status["commit_id"])
    message = {
        "shared_secret": cfbot_config.COMMITFEST_SHARED_SECRET,
        "task_status": task_status,
        "branch_status": branch_status,
    }
    return message


def make_branch_update_message(conn, branch_id):
    branch_status = make_branch_status_message(conn, branch_id=branch_id)
    message = {
        "shared_secret": cfbot_config.COMMITFEST_SHARED_SECRET,
        "branch_status": branch_status,
    }
    return message


# Handler for "post-branch-status" work_queue jobs.
def post_branch_status(conn, branch_id):
    message = make_branch_update_message(conn, int(branch_id))
    if cfbot_config.COMMITFEST_POST_URL:
        cfbot_util.post(cfbot_config.COMMITFEST_POST_URL, message)
    else:
        logging.info("would post to cf app: " + json.dumps(message))


# Handler for "post-task-status" work_queue jobs.
def post_task_status(conn, task_id):
    message = make_task_update_message(conn, task_id)
    if not message:
        return

    if cfbot_config.COMMITFEST_POST_URL:
        cfbot_util.post(cfbot_config.COMMITFEST_POST_URL, message)
    else:
        logging.info("would post to cf app: " + json.dumps(message))


if __name__ == "__main__":
    with cfbot_util.db() as conn:
        post_task_status(conn, "5798872931368960")
