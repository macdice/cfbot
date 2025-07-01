#!/usr/bin/env python3
import cfbot_config
import cfbot_util
import cfbot_work_queue

import requests


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
            buildCreatedTimestamp
          }
        }
    """
    variables = dict(owner=owner, repo=repo, sha=sha)
    result = query_cirrus(query, variables)
    if result and "searchBuilds" in result and len(result["searchBuilds"]):
        return result["searchBuilds"]
    else:
        return []


def get_tasks_for_build(build_id):
    query = """
        query tasksByBuildID($build_id: ID!) {
          build(id: $build_id) {
            tasks {
              id
              name
              status
            }
          }
        }
    """
    variables = dict(build_id=build_id)
    result = query_cirrus(query, variables)
    return result["build"]["tasks"]


def get_task_results(commit):
    builds = get_builds_for_commit(
        cfbot_config.CIRRUS_USER, cfbot_config.CIRRUS_REPO, commit
    )
    if len(builds) > 0:
        build = builds[0]["id"]
        return get_tasks_for_build(build)
    return []


# This is used in two code paths that might run concurrently:
#
# 1. While processing a cirrus-task-update job because we got a POST from
# Cirrus to tell us that a task status changed, but for now we just query all
# tasks and merge, rather than processing them one at a time.
#
# 2. While polling if we haven't heard from Cirrus for a while, to cope with
# missed notifications.
def poll_branch(conn, branch_id):
    cursor = conn.cursor()

    # Lock branch so that we can compute the new branch status in a serialised
    # transaction.
    #
    # XXX It's not ideal to hold this row lock while we make network
    # requests...  perhaps we should get_task_results() first, and move
    # get_artifacts_for_task(), get_commands_for_task() into a new work_queue
    # job?  But could that make states go backwards?
    cursor.execute(
        """SELECT commitfest_id,
                             submission_id,
                             commit_id,
                             status,
                             created < now()  - interval '1 hour' AS timeout_reached
                        FROM branch
                       WHERE id = %s
                         FOR UPDATE""",
        (branch_id,),
    )

    commitfest_id, submission_id, commit_id, branch_status, timeout_reached = (
        cursor.fetchone()
    )

    if branch_status == "testing":
        keep_polling_branch = False
        submission_needs_backoff = False
        tasks = get_task_results(commit_id)
        if len(tasks) == 0:
            # if no tasks at all, we're still waiting for them to be created...
            keep_polling_branch = True

        position = 0
        posted_at_least_one_task_status = False
        for task in tasks:
            task_still_running = False
            position += 1
            task_id = task["id"]
            name = task["name"]
            status = task["status"]
            if status == "PAUSED":
                continue  # ignore for now
            if status not in ("FAILED", "ABORTED", "ERRORED", "COMPLETED"):
                keep_polling_branch = True
                task_still_running = True
            if status in ("FAILED", "ABORTED", "ERRORED"):
                submission_needs_backoff = True

            # check if we already have this task, and what its status is
            cursor.execute(
                """SELECT status
                     FROM task
                    WHERE task_id = %s""",
                (task_id,),
            )
            row = cursor.fetchone()

            post_task_status = False
            if row:
                # only update if status changes, so we can use the modified time
                if row[0] != status:
                    cursor.execute(
                        """UPDATE task
                                 SET status = %s,
                                     modified = now()
                               WHERE task_id = %s""",
                        (status, task_id),
                    )
                    post_task_status = True

                    # if we reached a final state then it is time to pull down the
                    # artifacts (without bodies) and task commands (steps)
                    if not task_still_running:
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
                        for name, xtype, status, duration in get_commands_for_task(
                            task_id
                        ):
                            cursor.execute(
                                """INSERT INTO task_command (task_id, name, type, status, duration)
                                  VALUES (%s, %s, %s, %s, %s * interval '1 second')""",
                                (task_id, name, xtype, status, duration),
                            )
                        # the actual log bodies can be fetched later (and will trigger more jobs)
                        cfbot_work_queue.insert_work_queue(
                            cursor, "fetch-task-logs", task_id
                        )
            else:
                # a task we haven't heard about before
                cursor.execute(
                    """INSERT INTO task (task_id, position, commitfest_id, submission_id, task_name, commit_id, status, created, modified)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())""",
                    (
                        task_id,
                        position,
                        commitfest_id,
                        submission_id,
                        name,
                        commit_id,
                        status,
                    ),
                )
                # hmm we don't want to tell the cf app about CREATED but not triggered stuff...
                # XXX can we find out the triggered status, and skip that way?
                if status != "CREATED":
                    post_task_status = True

            if post_task_status:
                # tell the commitfest app
                cfbot_work_queue.insert_work_queue(cursor, "post-task-status", task_id)
                posted_at_least_one_task_status = True

        if timeout_reached:
            new_branch_status = "timeout"
        elif not keep_polling_branch:
            new_branch_status = "finished"
        else:
            new_branch_status = None  # no change

        if new_branch_status:
            cursor.execute(
                """UPDATE branch
                      SET status = %s,
                          modified = now()
                    WHERE id = %s""",
                (
                    new_branch_status,
                    branch_id,
                ),
            )
            if not posted_at_least_one_task_status:
                # task status messages include the branch status so no point in
                # posting another update unless we haven't queued one already
                # in this transaction
                cfbot_work_queue.insert_work_queue(
                    cursor, "post-branch-status", branch_id
                )

        if submission_needs_backoff:
            # If we're not already backing off due to a previous failure, then
            # we should start backing off. We also double the backoff time.
            cursor.execute(
                """UPDATE submission
                      SET backoff_until = now() + COALESCE(last_backoff * 2, interval '1 day'),
                          last_backoff = COALESCE(last_backoff * 2, interval '1 day')
                    WHERE commitfest_id = %s
                      AND submission_id = %s
                      AND (backoff_until < now() OR backoff_until IS NULL)""",
                (commitfest_id, submission_id),
            )
        elif new_branch_status == "finished":
            # Reset last_backoff and backoff_until if we finished successfully
            # without triggering a new backoff.
            cursor.execute(
                """UPDATE submission
                      SET backoff_until = NULL,
                          last_backoff = NULL
                    WHERE commitfest_id = %s
                      AND submission_id = %s
                      AND backoff_until < now()""",
                (commitfest_id, submission_id),
            )

    conn.commit()


# Handler for "poll-cirrus-branch" work_queue jobs.
def poll_branch_for_commit_id(conn, commit_id):
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id
             FROM branch
            WHERE commit_id = %s
            ORDER BY created
            LIMIT 1""",
        (commit_id,),
    )
    for (branch_id,) in cursor.fetchall():
        poll_branch(conn, branch_id)


# Normally, poll_branch() will be called based on webhook callbacks from Cirrus
# to tell us about changes, so this should often do nothing.  Since that's
# unreliable, we'll also look out for branches that haven't seen any change in
# a while so that we can advance the state machine, and queue up poll jobs
# directly.
#
# Handler for "poll-stale-cirrus-branches".
def poll_stale_branches(conn):
    cursor = conn.cursor()
    cursor.execute("""SELECT branch.commit_id, MAX(task.modified)
                        FROM branch
                        JOIN task ON (branch.commit_id = task.commit_id)
                       WHERE branch.status = 'testing'
                       GROUP BY 1
                      HAVING MAX(task.modified) < now() - interval '0 minutes'""")
    for commit_id, last_modified in cursor.fetchall():
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "poll-cirrus-branch", commit_id
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
        poll_stale_branches(conn)
        # poll_branch(conn, 200924)
        # poll_branch_for_commit_id(conn, "78526a6b703ed7a8efed9762692ef48ef32ccd8e")
#    backfill_task_command(conn)
#    backfill_task_command(conn)
#    pull_build_results(conn)
