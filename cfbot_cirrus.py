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
            branch
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
              statusTimestamp
            }
          }
        }
    """
    variables = dict(build_id=build_id)
    result = query_cirrus(query, variables)
    return result["build"]


# Normally all updates are triggered by webhooks carrying a build ID, or
# failing that, by poll_stale_builds().  But if a build machine is jammed and
# holding up the works, or we managed to miss so many webhooks that we don't
# even know about any build IDs that are associated with this branch, then this
# is the cleanup of last resort, that will poll all builds associated with this
# commit ID, and eventually trigger a timeout for the branch itself.
def poll_stale_branch(conn, branch_id):
    cursor = conn.cursor()
    cursor.execute(
        """SELECT submission_id,
                  commit_id,
                  status,
                  build_id IS NULL AS no_build,
                  created < now() - interval '1 hour' AS timeout_reached
             FROM branch
            WHERE id = %s""",
        (branch_id,),
    )
    submission_id, commit_id, branch_status, no_build, timeout_reached = (
        cursor.fetchone()
    )

    if branch_status == "testing" and timeout_reached:
        # Timeout reached, unless there has been a concurrent state change.
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
    elif no_build:
        # Schedule a poll of every build associated with this commit ID.  This
        # covers the case of missing a lot of webhooks (eg if cfbot is down for
        # a while), so that we don't know about any builds associated with this
        # branch.
        builds = get_builds_for_commit(
            cfbot_config.CIRRUS_USER, cfbot_config.CIRRUS_REPO, commit_id
        )
        # logging.info("builds for commit ID %s: %s", commit_id, builds)
        for build in builds:
            build_id = build["id"]
            build_branch = build["branch"]
            if build_branch == "cf/" + str(submission_id):
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


PRE_EXECUTING_STATUSES = ("CREATED", "TRIGGERED", "SCHEDULED")


# Compute backoff.  Called when the current active build completes.
def compute_submission_backoff(cursor, commitfest_id, submission_id, build_status):
    if build_status == "COMPLETED":
        # An auto-rebuilt triggered by Cirrus will generate a failed build
        # followed by a potentially successful build through no fault of the
        # patch's, so wipe all memory of backoffs on success.
        cursor.execute(
            """UPDATE submission
                  SET backoff_until = NULL,
                      last_backoff = NULL
                WHERE commitfest_id = %s
                  AND submission_id = %s""",
            (commitfest_id, submission_id),
        )
    elif build_status in FINAL_BUILD_STATUSES:
        # Double it every time
        cursor.execute(
            """UPDATE submission
                  SET backoff_until = now() + COALESCE(last_backoff * 2, interval '1 day'),
                      last_backoff = COALESCE(last_backoff * 2, interval '1 day')
                WHERE commitfest_id = %s
                  AND submission_id = %s
            RETURNING EXTRACT(days FROM last_backoff)""",
            (commitfest_id, submission_id),
        )
        (backoff,) = cursor.fetchone()
        logging.info(
            "submission %s/%s backoff time set to %s days",
            commitfest_id,
            submission_id,
            backoff,
        )


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
            if cursor.fetchone() == None:
                # if this is the most recently created build on this branch, then it is
                # XXX should we be using Cirrus's creation times, not our own?
                cursor.execute(
                    """SELECT build_id = %s
                         FROM build
                        WHERE branch_name = %s
                          AND commit_id = %s
                     ORDER BY created DESC
                        LIMIT 1""",
                    (build_id, build_branch, commit_id),
                )
                if row := cursor.fetchone():
                    if row[0]:
                        is_current_build_for_branch = True

        if is_current_build_for_branch:
            # Find the latest branch (push) record corresponding to the
            # subnmission (should probably be branch_name), and prepare to
            # merge details from this build into it.
            cursor.execute(
                """SELECT id, build_id, status, commitfest_id
                     FROM branch
                    WHERE submission_id = %s
                      AND commit_id = %s
                 ORDER BY created
                      FOR UPDATE
                    LIMIT 1""",
                (submission_id, commit_id),
            )
            branch_id, old_build_id, old_branch_status, commitfest_id = (
                cursor.fetchone()
            )
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

                    # XXX Should backoff apply when branch status is timeout?
                    # Current answer is no, because we can't tell the
                    # difference between timeout caused by Florida Mac (not
                    # patch's fault) and timeout caused by the user's patch
                    # hanging.  But in the case of a patch hanging, hopefully
                    # Cirrus times out and gives us FAILED or ABORTED?  Need to
                    # look into that...
                    if build_status in FINAL_BUILD_STATUSES:
                        compute_submission_backoff(
                            cursor, commitfest_id, submission_id, build_status
                        )

            if branch_modified:
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "post-branch-status", branch_id
                )


# task row should be locked, must be a new task or change in status
def process_new_task_status(
    cursor, task_id, old_task_status, task_status, source, timestamp
):
    # log new/changed status, update if changed
    if old_task_status:
        assert old_task_status != task_status
        logging.info("task %s %s -> %s", task_id, old_task_status, task_status)
        cursor.execute(
            """update task
                             set status = %s,
                                 modified = now()
                           where task_id = %s""",
            (task_status, task_id),
        )
    else:
        logging.info("new task %s %s", task_id, task_status)
        # caller inserted task

    # maintain the history of status changes
    cursor.execute(
        """insert into task_status_history(task_id, status, received, source, timestamp)
                      values (%s, %s, now(), %s, to_timestamp(%s::double precision / 1000))""",
        (task_id, task_status, source, timestamp),
    )

    # generate extra jobs depending on status
    if task_status in POST_TASK_STATUSES:
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "post-task-status", task_id
        )
    if task_status in FINAL_TASK_STATUSES:
        cfbot_work_queue.insert_work_queue(cursor, "fetch-task-commands", task_id)


# build row should be locked, must be new build or change in status
def process_new_build_status(
    cursor,
    build_id,
    old_build_status,
    build_status,
    commit_id,
    branch_name,
    source,
):
    # log new/changed status, update if changed
    if old_build_status:
        assert old_build_status != build_status
        logging.info("build %s %s -> %s", build_id, old_build_status, build_status)
        cursor.execute(
            """update build
                             set status = %s,
                                 commit_id = coalesce(commit_id, %s),
                                 branch_name = coalesce(branch_name, %s),
                                 modified = now()
                           where build_id = %s""",
            (build_status, commit_id, branch_name, build_id),
        )
    else:
        logging.info("new build %s %s", build_id, build_status)
        # caller inserted build

    # maintain the history of status changes
    cursor.execute(
        """insert into build_status_history(build_id, status, received, source)
                      values (%s, %s, now(), %s)""",
        (build_id, build_status, source),
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
            process_new_build_status(
                cursor, build_id, None, build_status, commit_id, build_branch, "webhook"
            )
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
                process_new_build_status(
                    cursor,
                    build_id,
                    existing_build_status,
                    build_status,
                    commit_id,
                    build_branch,
                    "webhook",
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

        # if we got here, we inserted or updated build above, so also
        # synchronise the branch
        update_branch(cursor, build_id, build_status, commit_id, build_branch)

    elif event_type == "task":
        task_id = event["task"]["id"]
        task_status = event["task"]["status"]
        task_name = event["task"]["name"]
        task_position = event["task"]["localGroupId"] + 1
        task_timestamp = event["task"]["statusTimestamp"]

        if action == "created":
            cursor.execute(
                """select 1
                     from build
                    where build_id = %s
                      for update""",
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

            cursor.execute(
                """INSERT INTO task (task_id, build_id, position, task_name, commit_id, status, created, modified)
                   VALUES (%s, %s, %s, %s, %s, %s, now(), now())
              ON CONFLICT DO NOTHING""",
                (
                    task_id,
                    build_id,
                    task_position,
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
            else:
                process_new_task_status(
                    cursor, task_id, None, task_status, "webhook", task_timestamp
                )
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
            elif existing_task_status == old_task_status:
                # we have the expected old value, common case
                process_new_task_status(
                    cursor,
                    task_id,
                    old_task_status,
                    task_status,
                    "webhook",
                    task_timestamp,
                )
            else:
                # unexpected or missing old value, fix by polling
                logging.info(
                    "webhook out of sync: task %s has status %s, expected %s",
                    task_id,
                    existing_task_status,
                    old_task_status,
                )
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "poll-stale-build", build_id
                )


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
    logging.info("Cirrus: %s", build)

    if not build:
        logging.info(
            "Cirrus does not know build %s, existing status %s",
            build_id,
            old_build_status,
        )
        if old_build_status == None:
            # Make sure we don't leave our weird NULL record behind for other
            # transactions to see.
            cursor.execute(
                """DELETE FROM build
                    WHERE build_id = %s
                      AND status IS NULL""",
                (build_id,),
            )
        elif old_build_status in ("CREATED", "SCHEDULED", "TRIGGERED"):
            # XXX It seems that Cirrus brings builds up to TRIGGERED status and
            # then deletes them (?), if it finds there is no .cirrus.yml file
            # in a branch.  We'll set the status to our own made-up status, to
            # prevent futher polling...
            process_new_build_status(
                cursor, build_id, old_build_status, "DELETED", None, None, "poll"
            )
        return

    commit_id = build["changeIdInRepo"]
    build_status = build["status"]
    build_branch = build["branch"]
    tasks = build["tasks"]

    # Upsert the tasks.
    for task in tasks:
        task_id = task["id"]
        task_name = task["name"]
        task_status = task["status"]
        position = task["localGroupId"] + 1
        task_timestamp = task["statusTimestamp"]

        # check if we already have this task, and what its status is
        cursor.execute(
            """SELECT status, status != %s
                 FROM task
                WHERE task_id = %s
                  FOR UPDATE""",
            (task_status, task_id),
        )
        if row := cursor.fetchone():
            # process change, if it is different
            (old_task_status, change) = row
            if change:
                process_new_task_status(
                    cursor,
                    task_id,
                    old_task_status,
                    task_status,
                    "poll",
                    task_timestamp,
                )
        else:
            # a task we haven't heard about before
            cursor.execute(
                """INSERT INTO task (task_id, build_id, position, task_name, commit_id, status, created, modified)
                   VALUES (%s, %s, %s, %s, %s, %s, now(), now())""",
                (
                    task_id,
                    build_id,
                    position,
                    task_name,
                    commit_id,
                    task_status,
                ),
            )
            process_new_task_status(
                cursor, task_id, None, task_status, "poll", task_timestamp
            )

            # tell the commitfest app
            if task_status in POST_TASK_STATUSES:
                cfbot_work_queue.insert_work_queue_if_not_exists(
                    cursor, "post-task-status", task_id
                )

    # Process branch changes.  This also sets the commit_id and branch_name due
    # to our strange protocol above...
    if old_build_status != build_status:
        process_new_build_status(
            cursor,
            build_id,
            old_build_status,
            build_status,
            commit_id,
            build_branch,
            "poll",
        )

    # maybe update the branch too
    update_branch(cursor, build_id, build_status, commit_id, build_branch)


# Called periodically to check if any branches appear to be stuck.  That is, we
# haven't yet heard about a build assocated with this branch, so we might have
# missed a webhook.  If so, queue up a job to poll for new builds associated
# with the commit ID.
#
# There is a hard-coded grace period of 1 minute before we resort to that.
# Usually we hear about a build within seconds, and find a branch to link it
# to.
def check_stale_branches(conn):
    cursor = conn.cursor()
    cursor.execute("""SELECT id
                        FROM branch
                       WHERE status = 'testing'
                         AND build_id IS NULL
                         AND created < now() - interval '1 minute'""")
    for (branch_id,) in cursor.fetchall():
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "poll-stale-branch", branch_id
        )
    conn.commit()


# Called periodically to check if any builds have exceeded the statistically
# expected time in a running state, and if so, queue up a job to poll them.
def check_stale_builds(conn):
    cursor = conn.cursor()

    # Compute the elapsed time of 99% of all completed master/release
    # branch builds in recent time, and use that as a reference to decide
    # when it's time to start polling a build because it looks like it's
    # taking too long and we might have missed some updates.
    #
    # This policy is quite conservative, but we don't want to poll too
    # often, or Cirrus might not like us, and 2 sigma should in theory only
    # have to poll for 1% of branches spuriously...
    cursor.execute("""with ref as (select branch_name,
                                          status,
                                          avg_elapsed + stddev_elapsed * 3 as elapsed_p99
                                     from build_status_statistics
                                    where branch_name = 'master' or branch_name like 'REL_%%'),
                           run as (select build_id,
                                          status,
                                          branch_name,
                                          case
                                            when branch_name = 'master' or branch_name like 'REL_%%'
                                            then branch_name
                                            else 'master'
                                          end as reference_branch,
                                          now() - created as elapsed
                                     from build
                                    where build_status_running(status))
                      select run.build_id,
                             run.reference_branch,
                             run.branch_name,
                             run.status,
                             extract(epoch from ref.elapsed_p99),
                             extract(epoch from run.elapsed)
                        from run
                   left join ref on ((run.reference_branch, run.status) = (ref.branch_name, ref.status))
                       where run.elapsed > COALESCE(elapsed_p99, interval '30 minutes')""")
    for (
        build_id,
        reference_branch,
        branch_name,
        build_status,
        elapsed_p99,
        elapsed,
    ) in cursor.fetchall():
        if elapsed_p99 == None:
            # no reference data available, it's just "a really long time"
            logging.info(
                "build %s still has status %s after %.2fs",
                build_id,
                build_status,
                elapsed,
            )
        else:
            logging.info(
                "build %s still has status %s after %.2fs, longer than %.2fs which was enough for 99.7%% of recent COMPLETED builds on reference branch %s",
                build_id,
                build_status,
                float(elapsed),
                float(elapsed_p99),
                reference_branch,
            )
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "poll-stale-build", build_id
        )


# Called periodically to check if any tasks have exceeded the statistically
# expected time in a running state, and if so, queue up a job to poll the
# relevant build.
def check_stale_tasks(conn):
    cursor = conn.cursor()

    # Same thing for tasks.
    cursor.execute("""with ref as (select branch_name,
                                          task_name,
                                          status,
                                          avg_elapsed + stddev_elapsed * 3 as elapsed_p99
                                     from task_status_statistics
                                    where branch_name = 'master' or branch_name like 'REL_%%'),
                           run as (select task.task_id,
                                          task.build_id,
                                          task.task_name,
                                          task.status,
                                          build.branch_name,
                                          case
                                            when build.branch_name = 'master' or build.branch_name like 'REL_%%'
                                            then build.branch_name
                                            else 'master'
                                          end as reference_branch,
                                          now() - task.modified as elapsed
                                     from task join build using (build_id)
                                    where task_status_running(task.status))
                      select run.task_id,
                             run.build_id,
                             run.reference_branch,
                             run.branch_name,
                             run.task_name,
                             run.status,
                             extract(epoch from ref.elapsed_p99),
                             extract(epoch from run.elapsed)
                        from run
                   left join ref on ((run.reference_branch, run.task_name, run.status) = (ref.branch_name, ref.task_name, ref.status))
                       where run.elapsed > COALESCE(elapsed_p99, interval '30 minutes')""")
    for (
        task_id,
        build_id,
        reference_branch,
        branch_name,
        task_name,
        task_status,
        elapsed_p99,
        elapsed,
    ) in cursor.fetchall():
        if elapsed_p99 == None:
            # no reference data available, it's just "a really long time"
            logging.info(
                "task %s still has status %s after %.2fs",
                task_id,
                task_status,
                elapsed,
            )
        else:
            logging.info(
                "task %s still has status %s after %.2fs, longer than %.2fs which was enough for 99.7%% of recent COMPLETED tasks named '%s' on reference branch %s",
                task_id,
                task_status,
                float(elapsed),
                float(elapsed_p99),
                task_name,
                reference_branch,
            )
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "poll-stale-build", build_id
        )


def refresh_task_status_statistics(conn):
    cursor = conn.cursor()
    cursor.execute("""delete from task_status_statistics""")
    cursor.execute("""insert into task_status_statistics
                             (branch_name, task_name, status, avg_elapsed, stddev_elapsed, n)
                      with elapsed as (select build.branch_name as branch_name,
                                              task.task_name,
                                              h.status,
                                              lead(h.timestamp) over(partition by h.task_id order by h.timestamp) - h.timestamp as elapsed
                                         from build
                                         join task using (build_id)
                                         join task_status_history h using (task_id)
                                        where task.status = 'COMPLETED'
                                          and (build.branch_name = 'master' or build.branch_name like 'REL_%%')
                                        )
                      select branch_name,
                             task_name,
                             status,
                             avg(elapsed),
                             coalesce(interval '1 second' * stddev(extract(epoch from elapsed)), interval '0 seconds') as stddev,
                             count(elapsed) as n
                        from elapsed
                       where elapsed is not null
                       group by 1, 2, 3""")


def refresh_build_status_statistics(conn):
    cursor = conn.cursor()
    cursor.execute("""delete from build_status_statistics""")
    cursor.execute("""insert into build_status_statistics
                             (branch_name, status, avg_elapsed, stddev_elapsed, n)
                      with elapsed as (select build.branch_name as branch_name,
                                              h.status,
                                              lead(received) over (partition by h.build_id order by received) - received as elapsed
                                         from build_status_history h
                                         join build using (build_id)
                                        where build.status = 'COMPLETED'
                                          and (build.branch_name = 'master' or build.branch_name like 'REL_%%')
                                        )
                      select branch_name,
                             status,
                             avg(elapsed),
                             coalesce(interval '1 second' * stddev(extract(epoch from elapsed)), interval '0 seconds') as stddev,
                             count(elapsed) as n
                        from elapsed
                       where elapsed is not null
                       group by 1, 2""")


if __name__ == "__main__":
    with cfbot_util.db() as conn:
        cursor = conn.cursor()
        compute_submission_backoff(cursor, 52, 3478, "COMPLETED")
        conn.commit()
