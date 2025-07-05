#!/usr/bin/env python3
import cfbot_config
import cfbot_util
import cfbot_work_queue

import json
import logging
import re
import requests

# https://github.com/cirruslabs/cirrus-ci-web/blob/master/schema.gql
FINAL_TASK_STATUSES = ("FAILED", "ABORTED", "ERRORED", "COMPLETED")
FINAL_BUILD_STATUSES = ("FAILED", "ABORTED", "ERRORED", "COMPLETED")

# which statuses the cfapp wants to hear about
POST_TASK_STATUSES = (
    "CREATED",
    "PAUSED",
    "SCHEDULED",
    "TRIGGERED",
    "EXECUTING",
    "FAILED",
    "ABORTED",
    "ERRORED",
    "COMPLETED",
)


def query_cirrus(query, variables):
    request = requests.post(
        "https://api.cirrus-ci.com/graphql",
        json={"query": query, "variables": variables},
    )
    if request.status_code == 200:
        result = request.json()
        return result["data"]
    else:
        raise Exception(
            "Query failed to run by returning code of {}. {}".format(
                request.status_code, query
            )
        )


def get_artifacts_for_task(task_id):
    query = """
        query TaskById($id: ID!) { task(id: $id) { id, name, artifacts { name, files { path, size } } } }
        """
    variables = dict(id=task_id)
    result = query_cirrus(query, variables)
    # print(result)
    artifacts = result["task"]["artifacts"]
    paths = []
    # print(artifacts)
    for f in artifacts:
        for p in f["files"]:
            paths.append((f["name"], p["path"], p["size"]))
    return paths


def get_commands_for_task(task_id):
    query = """
        query TaskById($id: ID!) { task(id: $id) { commands { name, type, status, durationInSeconds } } }
        """
    variables = dict(id=task_id)
    result = query_cirrus(query, variables)
    # print(result)
    simple_result = []
    for command in result["task"]["commands"]:
        name = command["name"]
        xtype = command["type"]
        status = command["status"]
        duration = command["durationInSeconds"]
        simple_result.append((name, xtype, status, duration))
    return simple_result


def get_builds_for_commit(owner, repo, sha):
    query = """
        query buildBySha($owner: String!, $repo: String!, $sha: String!) {
          searchBuilds(repositoryOwner: $owner, repositoryName: $repo, SHA: $sha) {
            id
            status
          }
        }
    """
    variables = dict(owner=owner, repo=repo, sha=sha)
    result = query_cirrus(query, variables)
    if result and "searchBuilds" in result:  # and result["searchBuilds"]:
        return result["searchBuilds"]
    else:
        return []


def get_build(build_id):
    query = """
        query tasksByBuildID($build_id: ID!) {
          build(id: $build_id) {
            status
            branch
            changeIdInRepo
            tasks {
              id
              name
              status
              localGroupId
            }
          }
        }
    """
    variables = dict(build_id=build_id)
    result = query_cirrus(query, variables)
    return result["build"]


# Normally all updates are triggered by webhooks carrying a build ID.  This
# function is called periodically for branches that haven't moved in a while,
# to paper over any lost webhooks, and eventually reach the timeout state if
# Cirrus or a build machine has lost the plot.
def poll_stale_branch(conn, branch_id):
    # Lock branch to keep concurrency simple
    cursor = conn.cursor()
    cursor.execute(
        """SELECT commit_id,
                             status,
                             created < now() - interval '1 hour' AS timeout_reached
                        FROM branch
                       WHERE id = %s""",
        (branch_id,),
    )
    commit_id, branch_status, timeout_reached = cursor.fetchone()

    if branch_status != "testing":
        # Nothing to do.
        return
    elif timeout_reached:
        # Timeout reached, unless there has been a concurrent state change
        cursor.execute(
            """UPDATE branch
                             SET status = 'timeout'
                           WHERE id = %s
                             AND status = 'testing'""",
            (branch_id,),
        )
        if cursor.rowsaffected() == 1:
            logging.info("branch %s testing -> timeout", branch_id, branch_status)
            cfbot_work_queue.insert_work_queue_if_not_exists(
                cursor, "post-branch-status", branch_id
            )
    else:
        # Schedule a poll of every build associated with this commit ID that is
        # not already in a final state.  This should cover problems caused by
        # missed webhooks from Cirrus.
        builds = get_builds_for_commit(
            cfbot_config.CIRRUS_USER, cfbot_config.CIRRUS_REPO, commit_id
        )
        # logging.info("builds for commit ID %s: %s", commit_id, builds)
        for build in builds:
            build_id = build["id"]
            build_status = build["status"]
            # if build_status not in FINAL_BUILD_STATUSES:
            cfbot_work_queue.insert_work_queue_if_not_exists(
                cursor, "poll-stale-build", build_id
            )


# Handler for "fetch-task-commands", a job enqueued once a task reaches a final
# state.
def fetch_task_commands(conn, task_id):
    cursor = conn.cursor()

    # if we reached a final state, then it is time to pull down the
    # artifacts (without bodies) and task commands (steps)

    # fetch the list of artifacts immediately
    for name, path, size in get_artifacts_for_task(task_id):
        cursor.execute(
            """INSERT INTO artifact (task_id, name, path, size)
               VALUES (%s, %s, %s, %s)
          ON CONFLICT DO NOTHING""",
            (task_id, name, path, size),
        )
    # artifact bodies will only be fetched after we figure out which tests
    # failed to avoid downloading too much

    # fetch the list of task commands (steps)
    for name, xtype, status, duration in get_commands_for_task(task_id):
        cursor.execute(
            """INSERT INTO task_command (task_id, name, type, status, duration)
               VALUES (%s, %s, %s, %s, %s * interval '1 second')""",
            (task_id, name, xtype, status, duration),
        )
    # the actual log bodies can be fetched later (and will trigger more jobs)
    cfbot_work_queue.insert_work_queue(cursor, "fetch-task-logs", task_id)


PRE_EXECUTING_STATUSUS = ("CREATED", "TRIGGERED", "SCHEDULED")


# We track the "current" build for each cfbot-managed branch.  The current
# branch is the one that is still in progress, or otherwise the latest one.
# Called by both poll_stale_branch() and ingest_webhook().
def update_branch(cursor, build_id, build_status, commit_id, build_branch):
    # if this is a cfbot managed branch, see if this build should now become
    # the current build for the branch
    if groups := re.match(r"cf/([0-9]+)", build_branch):
        # XXX it is a bit weird that we are parsing the branch name like this,
        # but matching by commit_id alone can lead to confusion in theory;
        # maybe add branch name to branch table?
        submission_id = groups.group(1)
        is_current_build_for_branch = False
        if build_status not in FINAL_BUILD_STATUSES:
            # if it's still in progress, then it is
            is_current_build_for_branch = True
        else:
            # if there is some other build still in progress, then it isn't
            cursor.execute(
                """SELECT 1
                     FROM build
                    WHERE branch_name = %s
                      AND commit_id = %s
                      AND build_id != %s
                      AND status NOT IN ('FAILED', 'ABORTED', 'ERRORED', 'COMPLETED')""",
                (build_branch, commit_id, build_id),
            )
            if not cursor.fetchone():
                # if this is the most recently created build on this branch, then it is
                # XXX should we be using Cirrus's creation times, not our own?
                cursor.execute(
                    """SELECT build_id
                         FROM build
                        WHERE branch_name = %s
                          AND commit_id = %s
                     ORDER BY created DESC
                        LIMIT 1""",
                    (build_branch, commit_id),
                )
                (most_recent_build_id,) = cursor.fetchone()
                if most_recent_build_id == build_id:
                    is_current_build_for_branch = True

        if is_current_build_for_branch:
            # Find the latest branch (push) record corresponding to the
            # subnmission (should probably be branch_name), and prepare to
            # merge details from this build into it.
            cursor.execute(
                """SELECT id, build_id, status
                     FROM branch
                    WHERE submission_id = %s
                      AND commit_id = %s
                 ORDER BY created
                      FOR UPDATE
                    LIMIT 1""",
                (submission_id, commit_id),
            )
            branch_id, old_build_id, old_branch_status = cursor.fetchone()
            branch_modified = False

            # If it wasn't tracking this build ID, update it.
            if old_build_id != build_id:
                branch_modified = True
                cursor.execute(
                    """UPDATE branch
                          SET build_id = %s,
                              modified = now()
                        WHERE id = %s""",
                    (build_id, branch_id),
                )
                logging.info(
                    "branch %s active build %s -> %s", branch_id, old_build_id, build_id
                )

            # The current build's status determines the branch's status.
            # The only status we won't overwrite is "timeout", which means
            # we've decided the branch is dead (we won't poll it, and it won't
            # be counted against the concurrent limit).  We can still receive
            # updates about it, though.
            #
            # XXX Perhaps we should be willing to un-time-out if the build
            # changed?
            #
            # XXX our internal branch status names could be tidier, but cfapp
            # knows about them so can't change them without coordinating the
            # rollout
            if old_branch_status != "timeout":
                if build_status in FINAL_BUILD_STATUSES:
                    if build_status == "COMPLETED":
                        branch_status = "finished"
                    else:
                        branch_status = "failed"
                else:
                    branch_status = "testing"
                if old_branch_status != branch_status:
                    branch_modified = True
                    cursor.execute(
                        """UPDATE branch
                              SET status = %s
                            WHERE id = %s""",
                        (branch_status, branch_id),
                    )
                    logging.info(
                        "branch %s %s -> %s",
                        branch_id,
                        old_branch_status,
                        branch_status,
                    )

            if branch_modified:
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "post-branch-status", branch_id
                )


# Called by cfbot_api.py's /api/cirrus-webhook endpoint with a message described at:
#
# https://cirrus-ci.org/api/#builds-and-tasks-webhooks
#
# Since webooks are unreliable, we check that the transition matches the
# existing database state.  If it doesn't, we enqueue a poll-stale-build job to
# resynchonise.
def ingest_webhook(conn, event_type, event):
    cursor = conn.cursor()

    # XXX validate to avoid key exceptions on malformed requests

    action = event["action"]
    build_id = event["build"]["id"]
    build_status = event["build"]["status"]
    build_branch = event["build"]["branch"]
    commit_id = event["build"]["changeIdInRepo"]

    if event_type == "build":
        cursor.execute(
            """INSERT INTO build (build_id, status, branch_name, commit_id, created, modified)
                          VALUES (%s, %s, %s, %s, now(), now())
                     ON CONFLICT DO NOTHING""",
            (build_id, build_status, build_branch, commit_id),
        )
        if cursor.rowcount == 1:
            if action != "created":
                logging.info(
                    "webhook out of sync, created build %s instead of updating",
                    build_id,
                )
            logging.info("new build %s %s", build_id, build_status)
        elif action == "created":
            logging.info(
                "webhook out of sync, build %s already exists, ignoring", build_id
            )
            return
        else:
            old_build_status = event["old_status"]
            cursor.execute(
                """SELECT status
                                FROM build
                               WHERE build_id = %s
                                 FOR UPDATE""",
                (build_id,),
            )
            (existing_build_status,) = cursor.fetchone()
            if existing_build_status == build_status:
                logging.info(
                    "webhook out of sync, build %s already has status %s, ignoring",
                    build_id,
                    build_status,
                )
                return
            elif existing_build_status == old_build_status or (
                build_status == "EXECUTING"
                and existing_build_status in PRE_EXECUTING_STATUSES
                and old_build_status in PRE_EXECUTING_STATUSES
            ):
                if existing_build_status != old_build_status:
                    logging.info(
                        "webhook out of sync, build %s expected to have %s but it has %s, assuming dropped webhooks and allowing transition to %s",
                        build_id,
                        old_build_status,
                        existing_build_status,
                        build_status,
                    )
                cursor.execute(
                    """UPDATE build
                          SET status = %s,
                              modified = now()
                        WHERE build_id = %s""",
                    (build_status, build_id),
                )
                logging.info(
                    "build %s %s -> %s", build_id, existing_build_status, build_status
                )
            else:
                logging.info(
                    "webhook out of sync, build %s has status %s but expected %s",
                    build_id,
                    existing_build_status,
                    old_build_status,
                )
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "poll-stale-build", build_id
                )
                return
        if build_status in FINAL_BUILD_STATUSES:
            cursor.execute(
                """SELECT COUNT(*)
                                FROM task
                               WHERE build_id = %s
                                 AND status NOT IN ('FAILED', 'ABORTED', 'ERRORED', 'COMPLETED', 'PAUSED')
                               LIMIT 1""",
                (build_id,),
            )
            (running_tasks,) = cursor.fetchone()
            if running_tasks > 0:
                logging.info(
                    "webhook out of sync: build %s has final status but has %d tasks with non-final, non-PAUSED status",
                    build_id,
                    running_tasks,
                )
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "poll-stale-build", build_id
                )
        update_branch(cursor, build_id, build_status, commit_id, build_branch)
    elif event_type == "task":
        task_id = event["task"]["id"]
        task_status = event["task"]["status"]
        task_name = event["task"]["name"]
        task_position = event["task"]["localGroupId"] + 1

        if action == "created":
            cursor.execute(
                """select 1
                     from build
                    where build_id = %s
                      for key share""",
                (build_id,),
            )
            if not cursor.fetchone():
                logging.info(
                    "webhook out of sync: referenced build %s does not exist", build_id
                )
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "poll-stale-build", build_id
                )
                return

            # XXX we have to guess what the commitfest/submission is, but why
            # do we even need these columns?
            cursor.execute(
                """select commitfest_id, submission_id
                     from branch
                    where commit_id = %s
                 order by created desc
                    limit 1""",
                (commit_id,),
            )
            if row := cursor.fetchone():
                commitfest_id, submission_id = row
            else:
                # needed for master/REL_XXX
                commitfest_id, submission_id = None, None
            cursor.execute(
                """INSERT INTO task (task_id, build_id, position, commitfest_id, submission_id, task_name, commit_id, status, created, modified)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
              ON CONFLICT DO NOTHING""",
                (
                    task_id,
                    build_id,
                    task_position,
                    commitfest_id,
                    submission_id,
                    task_name,
                    commit_id,
                    task_status,
                ),
            )
            if cursor.rowcount == 0:
                logging.info("webhook out of sync: task %s already exists", task_id)
                # XXX seems safe to skip creation without falling back to polling?
                # cfbot_work_queue.insert_work_queue_if_not_exists(
                #    cursor, "poll-stale-build", build_id
                # )
                return
            logging.info("new task %s %s", task_id, task_status)
        elif action == "updated":
            old_task_status = event["old_status"]
            cursor.execute(
                """select status
                                from task
                               where task_id = %s
                                 for update""",
                (task_id,),
            )
            if row := cursor.fetchone():
                (existing_task_status,) = row
            else:
                existing_task_status = None

            if existing_task_status == task_status:
                # already has that value, that's OK
                logging.info(
                    "webhook out of sync: task %s already has status %s",
                    task_id,
                    task_status,
                )
                return
            elif existing_task_status == old_task_status:
                # common case, only one that doesn't return
                cursor.execute(
                    """update task
                          set status = %s,
                              modified = now()
                        where task_id = %s""",
                    (task_status, task_id),
                )
            else:
                # unexpected value, fix by polling
                logging.info(
                    "webhook out of sync: task %s has status %s, expected %s",
                    task_id,
                    existing_task_status,
                    old_task_status,
                )
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "poll-stale-build", build_id
                )
                return
            logging.info("task %s %s -> %s", task_id, old_task_status, task_status)
        if task_status in POST_TASK_STATUSES:
            cfbot_work_queue.insert_work_queue_if_not_exists(
                cursor, "post-task-status", task_id
            )
        if task_status in FINAL_TASK_STATUSES:
            cfbot_work_queue.insert_work_queue(cursor, "fetch-task-commands", task_id)


# Handler for "poll-stale-build" jobs.
#
# These are created by the "poll-stale-branch" handler, used to advance branches
# that seem to be stuck. Note that it is careful to lock a build row so that it
# can safely run concurrently with ingest_webhook().
def poll_stale_build(conn, build_id):
    cursor = conn.cursor()

    # Serialise the API calls about this build by making sure we have a row to
    # lock first.  Otherwise the status might be able to go backwards in time
    # under concurrency.
    cursor.execute(
        """INSERT INTO build (build_id, created, modified)
                      VALUES (%s, now(), now())
                 ON CONFLICT DO NOTHING""",
        (build_id,),
    )
    inserted = cursor.rowcount > 0
    cursor.execute(
        """SELECT status
                        FROM build
                       WHERE build_id = %s
                         FOR UPDATE""",
        (build_id,),
    )
    (old_build_status,) = cursor.fetchone()

    # Network API call (regrettably while holding a lock...)
    build = get_build(build_id)
    # logging.info("Cirrus: %s", build)

    # If unknown to Cirrus (?!), then we're done, but we make sure we don't
    # leave our weird NULL record behind for other transactions to see.
    if not build:
        logging.info("Cirrus does not know build %s", build_id)
        cursor.execute(
            """DELETE FROM build
                           WHERE build_id = %s
                             AND status IS NULL""",
            (build_id,),
        )
        return

    commit_id = build["changeIdInRepo"]
    build_status = build["status"]
    build_branch = build["branch"]
    tasks = build["tasks"]

    # Update the modified time if the status changed.  This also sets
    # branch_name if we didn't have it before.
    if old_build_status != build_status:
        cursor.execute(
            """UPDATE build
                             SET status = %s,
                                 branch_name = %s,
                                 commit_id = %s,
                                 modified = now()
                           WHERE build_id = %s""",
            (build_status, build_branch, commit_id, build_id),
        )

    # Upsert the tasks.
    for task in tasks:
        task_id = task["id"]
        task_name = task["name"]
        task_status = task["status"]
        position = task["localGroupId"] + 1

        # check if we already have this task, and what its status is
        cursor.execute(
            """SELECT status
                 FROM task
                WHERE task_id = %s""",
            (task_id,),
        )
        if row := cursor.fetchone():
            # only update if status changes, so we can use the modified time
            (old_task_status,) = row
            if old_task_status != task_status:
                logging.info("task %s %s -> %s", task_id, old_task_status, task_status)
                cursor.execute(
                    """UPDATE task
                          SET status = %s,
                              build_id = %s,
                              modified = now()
                        WHERE task_id = %s""",
                    (task_status, build_id, task_id),
                )

                # if we reached a final state, then it is time to pull down the
                # artifacts (without bodies) and task commands (steps), which
                # will trigger further work
                if task_status in FINAL_TASK_STATUSES:
                    cfbot_work_queue.insert_work_queue(
                        cursor, "fetch-task-commands", task_id
                    )

                # tell the commitfest app
                if task_status in POST_TASK_STATUSES:
                    cfbot_work_queue.insert_work_queue_if_not_exists(
                        cursor, "post-task-status", task_id
                    )
        else:
            # a task we haven't heard about before

            # XXX we have to guess what the commitfest/submission is, but why do we even need these columns?  drop 'em
            # XXX make sure that post-XXX-status can handle that...
            cursor.execute(
                """select commitfest_id, submission_id from branch where commit_id = %s order by created desc limit 1""",
                (commit_id,),
            )
            if row := cursor.fetchone():
                commitfest_id, submission_id = row
            else:
                # needed for master/REL_XXX
                commitfest_id, submission_id = None, None

            logging.info("new task %s %s", task_id, task_status)
            cursor.execute(
                """INSERT INTO task (task_id, build_id, position, commitfest_id, submission_id, task_name, commit_id, status, created, modified)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())""",
                (
                    task_id,
                    build_id,
                    position,
                    commitfest_id,
                    submission_id,
                    task_name,
                    commit_id,
                    task_status,
                ),
            )

            # tell the commitfest app
            if task_status in POST_TASK_STATUSES:
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "post-task-status", task_id
                )

    if old_build_status != build_status:
        if inserted:
            logging.info("new build %s %s", build_id, build_status)
        else:
            logging.info("build %s %s -> %s", build_id, old_build_status, build_status)

    update_branch(cursor, build_id, build_status, commit_id, build_branch)


# Handler for "poll-stale-branches".
#
# Queued periodically by cron, to look out for branches that we should poll
# because they seem to be stuck in 'testing' and not moving.
def poll_stale_branches(conn):
    cursor = conn.cursor()
    cursor.execute("""SELECT branch.id, MAX(task.modified)
                        FROM branch
                   LEFT JOIN task ON (branch.commit_id = task.commit_id)
                       WHERE branch.status = 'testing'
                       GROUP BY 1
                      HAVING MAX(task.modified) IS NULL OR MAX(task.modified) < now() - interval '5 minutes'""")
    for branch_id, last_modified in cursor.fetchall():
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "poll-stale-branch", branch_id
        )
    conn.commit()


def backfill_artifact(conn):
    cursor = conn.cursor()
    cursor.execute("""SELECT commitfest_id, submission_id, task_name, commit_id, task_id
                      FROM task t
                     WHERE status = 'FAILED'
                       AND NOT EXISTS (SELECT *
                                         FROM artifact a
                                        WHERE t.task_id = a.task_id)""")
    for commitfest_id, submission_id, name, commit_id, task_id in cursor.fetchall():
        for name, path, size in get_artifacts_for_task(task_id):
            cursor.execute(
                """INSERT INTO artifact (task_id, name, path, size)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT DO NOTHING""",
                (task_id, name, path, size),
            )
        conn.commit()


def backfill_task_command(conn):
    cursor = conn.cursor()
    cursor.execute("""SELECT commitfest_id, submission_id, task_name, commit_id, task_id
                      FROM task t
                     WHERE status IN ('FAILED', 'COMPLETED')
                       AND NOT EXISTS (SELECT *
                                         FROM task_command c
                                        WHERE t.task_id = c.task_id)""")
    for commitfest_id, submission_id, name, commit_id, task_id in cursor.fetchall():
        for name, xtype, status, duration, log in get_commands_for_task(task_id):
            cursor.execute(
                """INSERT INTO task_command (task_id, name, type, status, duration, log)
                        VALUES (%s, %s, %s, %s, %s * interval '1 second', %s)""",
                (task_id, name, xtype, status, duration, log),
            )
        conn.commit()


if __name__ == "__main__":
    #  print(get_commands_for_task('5646021133336576'))
    #   print(get_artifacts_for_task('5636792221696000'))
    with cfbot_util.db() as conn:
        # poll_stale_branches(conn)
        # poll_stale_branch(conn, 201003)
        # poll_build(conn, 6247778155757568)
        poll_stale_build(conn, "5471750814695424")
        # ingest_webhook(
        #    conn,
        #    "task",
        #    {
        #        "old_status": "CREATED",
        #        "action": "updated",
        #        "repository": {
        #            "id": 5309429912436736,
        #            "owner": "postgresql-cfbot",
        #            "name": "postgresql",
        #            "isPrivate": False,
        #        },
        #        "build": {
        #            "id": 5275791488974848,
        #            "branch": "cf/4966",
        #            "changeIdInRepo": "1393f0d2323b1c6c92961b66e7228ab649d8cae7",
        #            "changeTimestamp": 1751414782000,
        #            "changeMessageTitle": "[CF 4966] v1 - Parallel CREATE INDEX for GIN indexes",
        #            "status": "EXECUTING",
        #            "user": {"id": 5349278619009024, "username": "postgresql-cfbot"},
        #        },
        #        "task": {
        #            "id": 5585849976356864,
        #            "name": "SanityCheck",
        #            "status": "TRIGGERED",
        #            "statusTimestamp": 1751414793117,
        #            "creationTimestamp": 1751414792338,
        #            "durationInSeconds": 0,
        #            "uniqueLabels": [],
        #            "localGroupId": 0,
        #            "automaticReRun": False,
        #            "manualRerunCount": 0,
        #            "automaticallyReRunnable": False,
        #            "instanceType": "GCEInstance",
        #            "notifications": [],
        #        },
        #    },
        # )
        conn.commit()
        # poll_branch_for_commit_id(conn, "78526a6b703ed7a8efed9762692ef48ef32ccd8e")
#    backfill_task_command(conn)
#    backfill_task_command(conn)
#    pull_build_results(conn)
