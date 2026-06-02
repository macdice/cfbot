#!/usr/bin/env python3
#
# Cfbot uses terminology inherited from the defunct Cirrus CI API, and
# we have to do a bit of key swizzling to map the concepts to Github's
# model:
#
# * build ~= GH [workflow] run (eg run triggered by commit, usually
#   there is only one per commit unless it is re-run for some reason,
#   as was the case with Cirrus).  Unfortunately run_id is not
#   globally unique (as Cirrus's was), so build_id is a string of the
#   form repo:run_id.run_attempt.
#
#   XXX Surrogate key?
#
# * task ~= GH job (eg Windows, Linux, ... being the individual tasks
#   of in a build), but again job_id is not globally unique so task_id
#   is a string of the form repo:job_id.
#
#   XXX Surrogate key?
#
#   XXX Note that the cfapp doesn't really need the repo: prefix
#   because it only cares about tasks in postgresql-cfbot/postgresql,
#   but for now at least it seems simpler to have just one kind of
#   task ID across the two systems.  Cfbot has to deal with
#   postgres/postgres tasks too, and the job_id part might collide.
#
# * task_command ~= GH job step (eg configure, build, test, ...).
#
#   XXX Not implemented yet...
#
# Cfbot builds and tasks have a single "status" inherited from Cirrus,
# with upper case values.  GH has two fields "status" and "conclusion"
# with lower case values, which we combine into Cirrus-style values.
# Those values are also known to the CF app which Cfbot pushes
# notifications into, so changing them would require changes in
# several places.
#
# XXX We should really ingest workflow_run and workflow_job events
# directly without having to poll the API endpoints to get the data,
# so that it's faster and doesn't count against our API call limits.
# (We used to do that for Cirrus.)
#
# XXX That's a bit tricky because sometimes events arrive out of order
# or concurrently and they don't seem to have a sequence number or
# even change timestamp.  So for now, we just react to workflow_job
# events by polling the whole worflow_run and figuring out what
# changed.  That is serialized on our end, because we lock the "build"
# row while polling.
#
# XXX It might be sufficient to use a table of permitted status
# transitions, and figure out when to ignore updates or poll to
# resolve violations?

import cfbot_config
import cfbot_util
import cfbot_work_queue

import datetime
import json
import logging
import re
import requests

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


# Github runs (builds), jobs (tasks) and steps (task_commands) have
# "status" and "conclusion".  Convert to the unified task status
# values we inherited from Cirrus.
def convert_github_status_and_conclusion(status, conclusion):
    if status == "requested":
        return "CREATED"
    elif status == "queued":
        return "SCHEDULED"
    elif status == "waiting":
        return "PAUSED"
    elif status == "in_progress":
        return "EXECUTING"
    elif status == "pending":
        return "PAUSED"  # ??? waiting for some kind of approval
    elif status == "completed":
        if conclusion == "success":
            return "COMPLETED"
        elif conclusion == "failure":
            return "FAILED"
        elif conclusion == "cancelled":
            return "ABORTED"
        elif conclusion == "skipped":
            return "SKIPPED"
        elif conclusion == "neutral":
            return "COMPLETED"  # ???
        elif conclusion == "action_required":
            return "COMPLETED"  # ???
    # unrecognized!  use verbatim until we can add a missing case...
    return status + ":" + conclusion


def make_build_id(repo, run_id, run_attempt):
    return repo + ":" + str(run_id) + "." + str(run_attempt)


def split_build_id(build_id):
    repo, rest = build_id.split(":")
    run_id, run_attempt = rest.split(".")
    return repo, run_id, run_attempt


def make_task_id(repo, job_id):
    return repo + ":" + str(job_id)


def split_task_id(task_id):
    repo, job_id = task_id.split(":")
    return repo, job_id


# Sends a GET request to the Github API and returns the resulting JSON
# as a Python object.
#
# repo includes the user, like "postgres/postgres".
def get_github_api(repo, action, params=None, none_for_404=False):
    url = "https://api.github.com/repos/" + repo + "/actions/" + action
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2026-03-10",
    }

    # Use a token for more calls/hour if we have one for this repo...
    #
    # XXX Get this from a github_token table so they can be
    # periodically replaced?
    if repo in cfbot_config.GITHUB_TOKENS:
        headers["Authorization"] = "Bearer " + cfbot_config.GITHUB_TOKENS[repo]

    request = requests.get(url, params=params, headers=headers)
    if request.status_code == 200:
        return request.json()
    else:
        if none_for_404 and request.status_code == 404:
            return None
        raise Exception(
            "Query failed to GET {}, status {}".format(url, request.status_code)
        )


# Compute backoff.  Called when the current active build completes.
def compute_submission_backoff(cursor, commitfest_id, submission_id, build_status):
    if build_status == "COMPLETED":
        # An auto-rebuild triggered by Github will generate a failed build
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
                # XXX should we be using Github's creation times, not our own?
                # XXX should we use the run_attempt part of build_id?
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
        logging.info(
            "task %s %s -> %s [%s]", task_id, old_task_status, task_status, source
        )
        cursor.execute(
            """UPDATE task
                  SET status = %s,
                      modified = now()
                WHERE task_id = %s""",
            (task_status, task_id),
        )
    else:
        logging.info("new task %s %s [%s]", task_id, task_status, source)
        # caller inserted task

    # maintain the history of status changes
    cursor.execute(
        """INSERT INTO task_status_history(task_id, status, received, source, timestamp)
           VALUES (%s, %s, now(), %s, to_timestamp(%s::double precision / 1000))""",
        (task_id, task_status, source, timestamp),
    )

    # generate extra jobs depending on status
    if task_status in POST_TASK_STATUSES:
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "post-task-status", task_id
        )

    # XXX Here we used to kick off the workflow that pulls down and
    # scrapes logs, test results etc.  That isn't implemented for
    # Github Actions yet.  FIXME
    #
    # if task_status in FINAL_TASK_STATUSES:
    #    cfbot_work_queue.insert_work_queue(cursor, "fetch-task-commands", task_id)


# build row should be locked, must be new build or change in status.
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
        logging.info(
            "build %s %s -> %s [%s]", build_id, old_build_status, build_status, source
        )
    else:
        logging.info("new build %s %s [%s]", build_id, build_status, source)
    cursor.execute(
        """UPDATE build
              SET status = %s,
                  commit_id = coalesce(commit_id, %s),
                  branch_name = coalesce(branch_name, %s),
                  modified = now()
            WHERE build_id = %s""",
        (build_status, commit_id, branch_name, build_id),
    )

    # maintain the history of status changes
    cursor.execute(
        """INSERT INTO build_status_history(build_id, status, received, source)
           VALUES (%s, %s, now(), %s)""",
        (build_id, build_status, source),
    )


# Poll a run (build) and figure out what has changed.
#
# This is called when a build row in non-final status hasn't had an
# update for a while, and (for now) whenever a webhook arrives
# relating to this build (run) or a task (job) it contains.
def poll_run(conn, repo, run_id, run_attempt, source):

    build_id = make_build_id(repo, run_id, run_attempt)

    # What triggered this.  This is only used so that the
    # (build|task)_status_history table can show when webhooks were
    # missed and we had to poll based on timeouts.
    assert source in ("webhook", "poll")

    cursor = conn.cursor()

    # Serialise the API calls about this build by making sure we have a row to
    # lock first.
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
    build = get_github_api(
        repo, "runs/" + run_id + "/attempts/" + run_attempt, none_for_404=True
    )

    if not build:
        logging.info(
            "Github does not know workflow run %s, existing status %s",
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
        else:
            # Huh... it went away?  No point in polling again.
            process_new_build_status(
                cursor, build_id, old_build_status, "DELETED", None, None, source
            )
        return

    commit_id = build["head_sha"]
    build_status = convert_github_status_and_conclusion(
        build["status"], build["conclusion"]
    )
    build_branch = build["head_branch"]

    tasks = get_github_api(
        repo, "runs/" + run_id + "/attempts/" + run_attempt + "/jobs"
    )["jobs"]

    # Upsert the tasks.
    position = 0
    for task in tasks:
        task_id = make_task_id(repo, task["id"])
        task_name = task["name"]
        task_status = convert_github_status_and_conclusion(
            task["status"], task["conclusion"]
        )
        position += 1

        # Cirrus gave us "last status change" timestamps, but not
        # Github...
        #
        # XXX Drop this column?  Make it nullable and set it only for
        # created and completed where we receive a GH-generated
        # timestamp?
        task_timestamp = "0"  # task["statusTimestamp"]

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
                    source,
                    task_timestamp,
                )
        else:
            # a task we haven't heard about before
            cursor.execute(
                """INSERT INTO task (task_id, build_id, position, task_name, status, created, modified)
                   VALUES (%s, %s, %s, %s, %s, now(), now())""",
                (
                    task_id,
                    build_id,
                    position,
                    task_name,
                    task_status,
                ),
            )
            process_new_task_status(
                cursor, task_id, None, task_status, source, task_timestamp
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
            source,
        )

    # maybe update the branch too
    update_branch(cursor, build_id, build_status, commit_id, build_branch)


# Find out about all runs associated with a repo + commit ID.
#
# This is called when we don't have any runs associated with a branch
# after a period of time, to find out if we've missed something.
def poll_commit(conn, repo, commit_id):
    cursor = conn.cursor()
    runs = get_github_api(repo, "runs", params={"head_sha": commit_id})
    for run in runs["workflow_runs"]:
        build_id = make_build_id(repo, run["id"], run["run_attempt"])
        cfbot_work_queue.insert_work_queue_if_not_exists(
            cursor, "poll-github-run", build_id
        )


# ======================================================================
# Functions called by cfbot_periodic_(minutely|hourly).py, ie cron
# ======================================================================


# Called periodically to check if any branches appear to be stuck.
# That is, we haven't yet heard about a build assocated with this
# branch, so we might have missed a webhook.  If so, queue up a job to
# poll for new builds associated with the commit ID.
#
# There is a hard-coded grace period of 1 minute before we resort to that.
# Usually we hear about a build within seconds, and find a branch to link it
# to.
def check_stale_branches(conn):
    cursor = conn.cursor()
    cursor.execute("""SELECT id,
                             commit_id,
                             created < now() - interval '1 hour' AS timeout_reached
                        FROM branch
                       WHERE status = 'testing'
                         AND build_id IS NULL
                         AND created < now() - interval '1 minute'
                         FOR UPDATE""")
    for branch_id, commit_id, time_out in cursor.fetchall():
        if time_out:
            # Time to give up on this branch?
            cursor.execute(
                """UPDATE branch
                      SET status = 'timeout'
                    WHERE id = %s
                      AND status = 'testing'""",
                (branch_id,),
            )
            logging.info("branch %s testing -> timeout", branch_id)
            cfbot_work_queue.insert_work_queue_if_not_exists(
                cursor, "post-branch-status", branch_id
            )
        else:
            # We might have missed a webhook by being down?
            cfbot_work_queue.insert_work_queue_if_not_exists(
                cursor,
                "poll-github-commit",
                cfbot_config.GITHUB_FULL_REPO + ":" + commit_id,
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
    # often, and 2 sigma should in theory only have to poll for 0.3%
    # of branches spuriously...
    cursor.execute("""with ref as (select branch_name,
                                          status,
                                          avg_elapsed + stddev_elapsed * 3 as elapsed_p99
                                     from build_status_statistics),
                           run as (select build_id,
                                          status,
                                          branch_name,
                                          case
                                            when build.branch_name like 'cf/%%' then 'cf/*'
                                            else build.branch_name
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
            cursor, "poll-github-run", build_id
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
                                     from task_status_statistics),
                           run as (select task.task_id,
                                          task.build_id,
                                          task.task_name,
                                          task.status,
                                          build.branch_name,
                                          case
                                            when build.branch_name like 'cf/%%' then 'cf/*'
                                            else build.branch_name
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
            cursor, "poll-github-run", build_id
        )


def refresh_task_status_statistics(conn):
    cursor = conn.cursor()
    cursor.execute("""delete from task_status_statistics""")
    cursor.execute("""insert into task_status_statistics
                             (branch_name, task_name, status, avg_elapsed, stddev_elapsed, n)
                      with elapsed as (select case
                                                when build.branch_name like 'cf/%%' then 'cf/*'
                                                else branch_name
                                              end as branch_name,
                                              task.task_name,
                                              h.status,
                                              lead(h.timestamp) over(partition by h.task_id order by h.timestamp) - h.timestamp as elapsed
                                         from build
                                         join task using (build_id)
                                         join task_status_history h using (task_id)
                                        where task.status = 'COMPLETED')
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
                      with elapsed as (select case
                                                when build.branch_name like 'cf/%%' then 'cf/*'
                                                else branch_name
                                              end as branch_name,
                                              h.status,
                                              lead(received) over (partition by h.build_id order by received) - received as elapsed
                                         from build_status_history h
                                         join build using (build_id)
                                        where build.status = 'COMPLETED')
                      select branch_name,
                             status,
                             avg(elapsed),
                             coalesce(interval '1 second' * stddev(extract(epoch from elapsed)), interval '0 seconds') as stddev,
                             count(elapsed) as n
                        from elapsed
                       where elapsed is not null
                       group by 1, 2""")


# ======================================================================
# Functions called by flask when our webhook is called by Github.
# ======================================================================


def ingest_workflow_run(conn, event):
    # XXX not used yet...
    pass


def ingest_workflow_job(conn, event):
    cursor = conn.cursor()
    # XXX ignore the status information in the webhook for now (due to
    # lack of ability to sequence correctly), and just enqueue a job
    # to poll the whole run with the API (while holding a lock)
    repo = event["repository"]["full_name"]
    run_id = event["workflow_job"]["run_id"]
    run_attempt = event["workflow_job"]["run_attempt"]
    build_id = make_build_id(repo, run_id, run_attempt)
    cfbot_work_queue.insert_work_queue_if_not_exists(
        cursor, "poll-github-run-on-webhook", build_id
    )


# ======================================================================
# Functions called by cfbot workers servicing work_queue items.
# ======================================================================


# poll-github-commit
def poll_github_commit(conn, key):
    # Enqueued by check_stale_branches() when we haven't heard any
    # news about a commit ID for a while.
    repo, commit_id = key.split(":")
    poll_commit(conn, repo, commit_id)


# poll-github-run
def poll_github_run(conn, key):
    # Enqueued by check_stale_builds() when we haven't heard any news
    # about a build for a statistically unlikely period of time.
    repo, run_id, run_attempt = split_build_id(key)
    poll_run(conn, repo, run_id, run_attempt, "poll")


# poll-github-run-on-webhook
def poll_github_run_on_webhook(conn, key):
    # Enqueued by ingest_workflow_job() when we receive a webhook
    # poke from Github.
    repo, run_id, run_attempt = split_build_id(key)
    poll_run(conn, repo, run_id, run_attempt, "webhook")


# ======================================================================

if __name__ == "__main__":
    # print(json.dumps(get_builds_for_commit("3c970e3e544bb17a894854c027d3d3bc285fb072"), indent=4))
    # print(json.dumps(get_tasks_for_build("26492296536"), indent=4))
    # print(json.dumps(get_github_api("runs/" + "264922965360", none_for_404=True), indent=4))
    # exit(0)
    with cfbot_util.db() as conn:
        # cursor = conn.cursor()
        # compute_submission_backoff(cursor, 52, 3478, "COMPLETED")
        # poll_stale_branch(conn, 2)
        check_stale_branches(conn)
        check_stale_builds(conn)
        check_stale_tasks(conn)
        conn.commit()
