#!/usr/bin/env python3

import cfbot_cirrus
import cfbot_commitfest
import cfbot_config
import cfbot_util
import cfbot_web_highlights
import re
import scipy.stats
import requests
import logging

# Patterns to look out for in artifact files.
ARTIFACT_PATTERNS = (
    (re.compile(r"SUMMARY: .*Sanitizer.*"), "sanitizer"),
    (re.compile(r".*TRAP: failed Assert.*"), "assertion"),
    (re.compile(r".*PANIC: .*"), "panic"),
)

# Patterns to look out for in "build" step.  This patterns detects MSVC
# warnings, which notably don't cause a build failure so this might be our only
# chance to notice them early.
BUILD_PATTERNS = ((re.compile(r".*: (warning|error) [^:]+: .*"), "compiler"),)  # msvc

# Patterns to look out for in the "*_warnings" steps.  These detect GCC and
# Clang warnings.
WARNING_PATTERNS = (
    (re.compile(r".*:[0-9]+: (error|warning): .*"), "compiler"),
    (re.compile(r".*: undefined reference to .*"), "linker"),
)


def highlight_patterns(cursor, task_id, source, patterns, line, types):
    for pattern, highlight_type in patterns:
        if pattern.match(line):
            insert_highlight(cursor, task_id, highlight_type, source, line, types)
            break


def retry_limit(type):
    if type.startswith("fetch-") or type.startswith("post-"):
        # Things that hit network APIs get multiple retries
        return 3

    # Everything else is just assumed to be a bug/data problem and requires
    # user intervention
    return 0


def binary_to_safe_utf8(bytes):
    text = bytes.decode("utf-8", errors="ignore")  # strip illegal UTF8 sequences
    text = text.replace("\x00", "")  # postgres doesn't like nul codepoint
    text = text.replace("\r", "")  # strip windows noise
    return text


def insert_highlight(cursor, task_id, type, source, excerpt, types):
    types.add(type)
    cursor.execute(
        """insert into highlight (task_id, type, source, excerpt)
                      values (%s, %s, %s, %s)""",
        (task_id, type, source, excerpt),
    )


def insert_work_queue(cursor, type, key):
    cursor.execute(
        """insert into work_queue (type, key, status) values (%s, %s, 'NEW')""",
        (type, key),
    )


def insert_work_queue_if_not_exists(cursor, type, key):
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


def lock_task(cursor, task_id):
    cursor.execute("""select from task where task_id = %s for update""", (task_id,))


def ingest_task_artifacts(conn, task_id):
    highlight_types = set()

    cursor = conn.cursor()
    lock_task(cursor, task_id)
    cursor.execute(
        """delete from highlight
                       where task_id = %s
                         and (type in ('sanitizer', 'assertion', 'panic', 'regress', 'tap') or
                              (type = 'core' and not exists (select *
                                                               from task_command
                                                              where task_id = %s
                                                                and name = 'cores')))""",
        (task_id, task_id),
    )

    # scan all artifact files for patterns we recognise
    cursor.execute(
        """select name, path, body
                        from artifact
                       where task_id = %s
                         and body is not null""",
        (task_id,),
    )
    for name, path, body in cursor.fetchall():
        source = "artifact:" + name + "/" + path

        # Crashlogs require some multi-line processing
        if name == "crashlog":
            # Windows crash logs show up as artifacts (see also
            # ingest_task_logs for Unix)
            collected = []
            in_backtrace = False

            def dump(source):
                insert_highlight(
                    cursor,
                    task_id,
                    "core",
                    source,
                    "\n".join(collected),
                    highlight_types,
                )
                collected.clear()

            for line in body.splitlines():
                # backtraces start like this:
                if re.match(r"Child-SP.*", line):
                    if in_backtrace:
                        # if multiple core files, dump previous one
                        dump(source)
                    in_backtrace = True
                    continue
                if in_backtrace:
                    # stack frames start like this:
                    if re.match(r"[0-9a-fA-F]{8}`.*", line):
                        if len(collected) < 10:
                            collected.append(line)
                        else:
                            # that's enough lines for a highlight
                            dump(source)
                            in_backtrace = False
            if in_backtrace:
                dump(source)
            continue

        if path.endswith("/regression.diffs"):
            if body.strip() == "":
                continue
            lines = body.splitlines()
            excerpt = "\n".join(lines[:20])
            if len(lines) > 20:
                excerpt += "\n...\n"
            insert_highlight(
                cursor, task_id, "regress", source, excerpt, highlight_types
            )
            continue

        if re.match("^.*/regress_log_.*$", path):
            collected = []
            for line in body.splitlines():
                if re.match(".*(Bail out!|timed out).*", line):
                    collected.append(line)
                elif re.match(".* not ok .*", line) and not re.match(
                    ".* (TODO|SKIP).*", line
                ):
                    collected.append(line)
            if len(collected) > 0:
                insert_highlight(
                    cursor,
                    task_id,
                    "tap",
                    source,
                    "\n".join(collected),
                    highlight_types,
                )

        # Process the simple patterns
        for line in body.splitlines():
            highlight_patterns(
                cursor, task_id, source, ARTIFACT_PATTERNS, line, highlight_types
            )

    # if we inserted any highlights, rebuild the appropriate pages
    if highlight_types:
        insert_work_queue(cursor, "refresh-highlight-pages", "all")
        for t in highlight_types:
            insert_work_queue(cursor, "refresh-highlight-pages", t)


def ingest_task_logs(conn, task_id):
    highlight_types = set()
    cursor = conn.cursor()
    lock_task(cursor, task_id)
    cursor.execute(
        """delete from highlight
                       where task_id = %s
                         and (type in ('compiler', 'linker', 'regress', 'isolation', 'test') or
                              (type = 'core' and exists (select *
                                                           from task_command
                                                          where task_id = %s
                                                            and name = 'cores')))""",
        (task_id, task_id),
    )
    cursor.execute(
        """delete from test
                       where task_id = %s
                         and type = 'tap'""",
        (task_id,),
    )
    cursor.execute(
        """select name, log
                        from task_command
                       where task_id = %s
                         and (name in ('build', 'build_32', 'test_world', 'test_world_32', 'test_running', 'check_world', 'cores') or name like '%%_warning')
                         and log is not null""",
        (task_id,),
    )
    for name, log in cursor.fetchall():
        source = "command:" + name
        if name == "build":
            for line in log.splitlines():
                highlight_patterns(
                    cursor, task_id, source, BUILD_PATTERNS, line, highlight_types
                )
        elif name.endswith("_warning"):
            for line in log.splitlines():
                highlight_patterns(
                    cursor, task_id, source, WARNING_PATTERNS, line, highlight_types
                )
        elif name in ("test_world", "test_world_32", "test_running", "check_world"):
            in_tap_summary = False
            collected_tap = []

            def dump_tap(source):
                if len(collected_tap) > 0:
                    insert_highlight(
                        cursor,
                        task_id,
                        "test",
                        source,
                        "\n".join(collected_tap),
                        highlight_types,
                    )
                    collected_tap.clear()

            for line in log.splitlines():
                # "structured" test result capture: we want all the results
                # including success (later this might come from meson's .json file
                # so we don't need hairy regexes)
                #
                # note: failures captured here will affect the fetch-task-artifacts
                # job
                groups = re.match(
                    r".* postgresql:[^ ]+ / ([^ /]+)/([^ ]+) *([A-Z]+) *([0-9.]+s).*",
                    line,
                )
                if groups:
                    suite = groups.group(1)
                    test = groups.group(2)
                    result = groups.group(3)
                    duration = groups.group(4)
                    cursor.execute(
                        """insert into test (task_id, command, type, suite, name, result, duration)
                                      values (%s, %s, 'tap', %s, %s, %s, %s)
                                      on conflict do nothing""",
                        (task_id, name, suite, test, result, duration),
                    )

                # "unstructured" highlight, raw log excerpt
                if re.match(r".*Summary of Failures:", line):
                    dump_tap(source)
                    in_tap_summary = True
                    continue
                if in_tap_summary and re.match(r".* postgresql:[^ ]+ / [^ ]+ .*", line):
                    if not re.match(r".* SKIP .*", line):
                        collected_tap.append(line)
                elif re.match(r".*Expected Fail:.*", line):
                    dump_tap(source)
                    in_tap_summary = False
            dump_tap(source)

        elif name == "cores":
            # Linux/FreeBSD/macOS have backtraces in the "cores" task command,
            # but see also ingest_task_artifact which processes Windows'
            # backtraces.
            collected = []
            in_backtrace = False

            def dump(source):
                insert_highlight(
                    cursor,
                    task_id,
                    "core",
                    source,
                    "\n".join(collected),
                    highlight_types,
                )
                collected.clear()

            for line in log.splitlines():
                # GDB (Linux, FreeBSD) backtraces start with "Thread N", LLDB (macOS) with "thread #N"
                if re.match(r".* [Tt]hread #?[0-9]+ ?.*", line):
                    if in_backtrace:
                        # if multiple core files, dump previous one
                        dump(source)
                    in_backtrace = True
                    continue
                if in_backtrace:
                    # GDB stack frames start like " #N ", LLDB like "frame #N:"
                    if re.match(r".* #[0-9]+[: ].*", line):
                        if len(collected) < 10:
                            collected.append(line)
                        else:
                            # that's enough lines for a highlight
                            dump(source)
                            in_backtrace = False
            if in_backtrace:
                dump(source)

    # now that we have the list of failed tests, we can pull down the artifact
    # bodies more efficiently (excluded successful tests)
    insert_work_queue(cursor, "fetch-task-artifacts", task_id)
    # insert_work_queue(cursor, "analyze-task-tests", task_id)

    # if we inserted any highlights, rebuild the appropriate pages
    if highlight_types:
        insert_work_queue(cursor, "refresh-highlight-pages", "all")
        for t in highlight_types:
            insert_work_queue(cursor, "refresh-highlight-pages", t)


def fetch_task_logs(conn, task_id):
    cursor = conn.cursor()

    # find all the commands for this task, and pull down the logs
    cursor.execute(
        """select name from task_command where task_id = %s and status not in ('SKIPPED', 'UNDEFINED', 'ABORTED')""",
        (task_id,),
    )
    for (command,) in cursor.fetchall():
        log_bin = cfbot_util.slow_fetch_binary(
            "https://api.cirrus-ci.com/v1/task/%s/logs/%s.log" % (task_id, command),
            True,
        )
        if log_bin is None:
            continue
        log = binary_to_safe_utf8(log_bin)
        cursor.execute(
            """update task_command
                             set log = %s
                           where task_id = %s
                             and name = %s""",
            (log, task_id, command),
        )

    # defer ingestion until a later step
    insert_work_queue(cursor, "ingest-task-logs", task_id)


def fetch_task_artifacts(conn, task_id):
    cursor = conn.cursor()

    # download the artifacts for this task.   we want the Windows crashlog ones
    # always, and the testrun ones, but we exclude subdirectories corresponding to
    # tests that passed, to save on disk space
    cursor.execute(
        """select name, path
                        from artifact
                       where task_id = %s
                         and body is null
                         and (name = 'crashlog' or
                              (name = 'testrun' and
                               (task_id, coalesce(substring(path from '^[^/]+/testrun/[^/]+/[^/]+'), '')) not in
                                (select task_id,
					case command
                                          when 'test_world_32' then 'build-32/testrun/'
					  else 'build/testrun/'
					end || suite || '/' || name
                                   from test
                                  where task_id = %s
                                    and result in ('OK', 'SKIP'))))""",
        (task_id, task_id),
    )
    artifacts_to_fetch = cursor.fetchall()
    if len(artifacts_to_fetch) == 0:
        # if that didn't find any, then perhaps we don't have any "test" rows because
        # this is an autoconf build with unparseable logs.  just download everything (note that artifacts
        # only exist at all if *something* failed, we just don't know what it was)
        cursor.execute(
            """select name, path from artifact where task_id = %s and body is null and name = 'log'""",
            (task_id,),
        )
        artifacts_to_fetch = cursor.fetchall()

    for name, path in artifacts_to_fetch:
        url = "https://api.cirrus-ci.com/v1/artifact/task/%s/%s/%s" % (
            task_id,
            name,
            path,
        )
        # print(url)
        log = binary_to_safe_utf8(cfbot_util.slow_fetch_binary(url))
        cursor.execute(
            """update artifact set body = %s where task_id = %s and name = %s and path = %s""",
            (log, task_id, name, path),
        )

    # defer ingestion to a later step
    insert_work_queue(cursor, "ingest-task-artifacts", task_id)


def analyze_task_tests(conn, task_id):
    cursor = conn.cursor()
    cursor.execute("""select submission_id from task where task_id = %s""", (task_id,))
    (submission_id,) = cursor.fetchone()
    cursor.execute(
        """delete from test_statistics where submission_id = %s""", (submission_id,)
    )
    cursor.execute(
        """
select task.task_name,
       test.command,
       test.suite,
       test.name,
       array_agg(extract (epoch from test.duration))
         filter (where task.submission_id = %s),
       array_agg(extract (epoch from test.duration))
         filter (where task.submission_id != %s)
  from test
  join task using (task_id)
 where task.created > now() - interval '7 days'
   and task.status = 'COMPLETED'
 group by 1, 2, 3, 4""",
        (submission_id, submission_id),
    )
    for task_name, command, suite, test, sample1, sample2 in cursor.fetchall():
        if not sample1 or not sample2 or len(sample1) <= 2 or len(sample2) <= 2:
            continue
        patched_avg = sum(sample1) / len(sample1)
        other_avg = sum(sample2) / len(sample2)
        t, p = scipy.stats.ttest_ind(sample1, sample2, equal_var=False)
        cursor.execute(
            """insert into test_statistics (submission_id, task_name, command, suite, test, other_avg, patched_avg, t, p)
                          values (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                submission_id,
                task_name,
                command,
                suite,
                test,
                other_avg,
                patched_avg,
                t,
                p,
            ),
        )


def refresh_highlight_pages(conn, type):
    # rebuild pages of the requested type/mode
    cfbot_web_highlights.rebuild_type(conn, type)


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

    # dispatch to the right work handler
    try:
        if type == "fetch-task-logs":
            fetch_task_logs(conn, key)
        elif type == "ingest-task-logs":
            ingest_task_logs(conn, key)
        elif type == "fetch-task-artifacts":
            fetch_task_artifacts(conn, key)
        elif type == "ingest-task-artifacts":
            ingest_task_artifacts(conn, key)
        elif type == "analyze-task-tests":
            analyze_task_tests(conn, key)
        elif type == "refresh-highlight-pages":
            refresh_highlight_pages(conn, key)
        elif type == "poll-cirrus-branch":
            cfbot_cirrus.poll_branch_for_commit_id(conn, key)
        elif type == "post-task-status":
            cfbot_commitfest.post_task_status(conn, key)
        elif type == "post-branch-status":
            cfbot_commitfest.post_branch_status(conn, key)
        else:
            pass
    except requests.exceptions.ReadTimeout:
        logging.error("Failed to process work queue due to a timeout")
        return False
    except requests.exceptions.ConnectionError:
        logging.error("Failed to process work queue due to a connection error")
        return False
    except requests.exceptions.HTTPError as e:
        logging.error("Failed to process work queue due to an HTTP error: %s", e)
        return False

    # if we made it this far without an error, this work item is done
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
        cursor.execute("set application_name = 'cfbot_queue_worker'")
        cursor.execute(
            "select count(*) from pg_stat_activity where application_name = 'cfbot_queue_worker'"
        )
        (nworkers,) = cursor.fetchone()
        # normally we start one of these every minute and finishes quickly, but
        # we're prepared to run several at once to clear a backlog
        if nworkers < cfbot_config.CONCURRENT_QUEUE_WORKERS:
            cursor.execute("set synchronous_commit = off")
            # ingest_task_logs(conn, "5009777160355840")
            # conn.commit()
            # process_one_job(conn)
            while process_one_job(conn, False):
                pass
