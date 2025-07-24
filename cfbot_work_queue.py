#!/usr/bin/env python3

import cfbot_cirrus
import cfbot_commitfest
import cfbot_config
import cfbot_highlights
import cfbot_util
import re
import select
import setproctitle
import requests
import time
import logging


def retry_limit(type):
    if (
        type.startswith("fetch-")
        or type.startswith("poll-")
        or type.startswith("post-")
    ):
        # Things that hit network APIs get multiple retries
        return 3

    # Everything else is just assumed to be a bug/data problem and requires
    # user intervention
    return 0


def insert_work_queue(cursor, type, key=None):
    cursor.execute(
        """insert into work_queue (type, key, status) values (%s, %s, 'NEW') returning id""",
        (type, key),
    )
    (id,) = cursor.fetchone()
    # logging.info("work_queue insert: id = %d, type = %s, key = %s", id, type, key)
    cursor.execute("notify work_queue")


def insert_work_queue_if_not_exists(cursor, type, key=None):
    # skip if there is already an identical item queued and we can lock it
    # without waiting, to deduplicate jobs
    cursor.execute(
        """select 1
                        from work_queue
                       where type = %s
                         and key is not distinct from %s
                         and status = 'NEW'
                         for update skip locked
                       limit 1""",
        (type, key),
    )
    if not cursor.fetchone():
        insert_work_queue(cursor, type, key)


def process_one_job(conn, fetch_only):
    cursor = conn.cursor()
    if fetch_only:
        cursor.execute("""select id, type, key, retries
                         from work_queue
                        where type like 'fetch-%'
                          and (status = 'NEW' or (status = 'WORK' and lease < now()))
                          for update skip locked
                        limit 1""")
    else:
        cursor.execute("""select id, type, key, retries
                          from work_queue
                         where status = 'NEW'
                            or (status = 'WORK' and lease < now())
                           for update skip locked
                         limit 1""")
    row = cursor.fetchone()
    if not row:
        return False
    id, type, key, retries = row
    # print("XXX " + type + " " + key);
    if retries and retries >= retry_limit(type):
        cursor.execute(
            """update work_queue
                           set status = 'FAIL'
                         where id = %s""",
            (id,),
        )
        id = None
    else:
        cursor.execute(
            """update work_queue
                           set lease = now() + interval '15 minutes',
                               status = 'WORK',
                               retries = coalesce(retries + 1, 0)
                         where id = %s""",
            (id,),
        )
    conn.commit()
    if not id:
        return True  # done, go around again

    setproctitle.setproctitle("cfbot worker: %s %s" % (type, key))

    # logging.info("work_queue begin:  id = %d, type = %s, key = %s", id, type, key)
    start_time = time.time()

    # dispatch to the right work handler
    try:
        if type == "fetch-task-logs":
            cfbot_highlights.fetch_task_logs(conn, key)
        elif type == "ingest-task-logs":
            cfbot_highlights.ingest_task_logs(conn, key)
        elif type == "fetch-task-commands":
            cfbot_cirrus.fetch_task_commands(conn, key)
        elif type == "fetch-task-artifacts":
            cfbot_highlights.fetch_task_artifacts(conn, key)
        elif type == "ingest-task-artifacts":
            cfbot_highlights.ingest_task_artifacts(conn, key)
        elif type == "analyze-task-tests":
            cfbot_highlights.analyze_task_tests(conn, key)
        elif type == "refresh-highlight-pages":
            cfbot_highlights.refresh_highlight_pages(conn, key)
        elif type == "poll-stale-branch":
            cfbot_cirrus.poll_stale_branch(conn, key)
        elif type == "poll-stale-build":
            cfbot_cirrus.poll_stale_build(conn, key)
        elif type == "post-task-status":
            cfbot_commitfest.post_task_status(conn, key)
        elif type == "post-branch-status":
            cfbot_commitfest.post_branch_status(conn, key)
        else:
            pass
    except (
        requests.exceptions.ReadTimeout,
        requests.exceptions.ConnectionError,
        requests.exceptions.HTTPError,
    ) as e:
        # these are all exceptions that happy often and randomly due to flaky
        # web services, and we're brave enough to continue and retry a couple
        # of times after the lease expires
        logging.error(
            "work_queue retryable error: id = %d, type = %s, key = %s, error = %s",
            id,
            type,
            key,
            e,
        )
        conn.rollback()
        return False
    except:
        # for anything else, things are not good: log with exception stack
        # trace and rethrow so we blow up and attract more attention
        logging.exception(
            "work_queue fatal error: id = %d, type = %s, key = %s", id, type, key
        )
        raise

    # if we made it this far without an error, this work item is done
    # logging.info(
    #    "work_queue finish: id = %d, type = %s, key = %s, elapsed = %0.03fs",
    #    id,
    #    type,
    #    key,
    #    time.time() - start_time,
    # )
    cursor.execute(
        """delete from work_queue
                       where id = %s""",
        (id,),
    )
    conn.commit()
    return True  # go around again


if __name__ == "__main__":
    with cfbot_util.db() as conn:
        cursor = conn.cursor()
        cursor.execute("set application_name = 'cfbot_worker'")
        cursor.execute("set synchronous_commit = off")
        cursor.execute("listen work_queue")

        # run until interrupted by a signal
        #
        # XXX need to handle supervisord's first signal and exit nicely after
        # finishing the job we're working on, for clean shutdown
        while True:
            # process as many jobs as we can without waiting
            while process_one_job(conn, False):
                conn.notifications.clear()

            # wait for NOTIFY to wake us up
            #
            # XXX correct way to get socket fd?
            # XXX transactions block  delivery
            setproctitle.setproctitle("cfbot worker: idle")
            conn.autocommit = True
            conn.commit()
            select.select([conn._usock], [], [])
            conn.notifications.clear()
            conn.autocommit = False
