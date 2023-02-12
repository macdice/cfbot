#!/usr/bin/env python

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import math
import os
import re

from cfbot_commitfest_rpc import Submission

def retry_limit(type):
    if type.startswith("fetch-"):
        # Things that hit network APIs get multiple retries
        return 3

    # Everything else is just assumed to be a bug/data problem and should just fail
    return 0

def binary_to_safe_utf8(bytes):
      text = bytes.decode('utf-8', errors='ignore') # strip illegal UTF8 sequences
      text = text.replace("\x00", "") # postgres doesn't like nul codepoint
      text = text.replace("\r", "") # strip windows noise
      return text

def ingest_cores(conn, task_id):
    collected = []
    cursor = conn.cursor()
    command = None

    # prevent concurrency at the task level (should really be per work item type?)
    cursor.execute("""select from task where task_id = %s for update""", (task_id,))

    # just in case we are re-run, remove older core highlights
    cursor.execute("""delete from highlight where task_id = %s and type = 'core'""", (task_id,))

    def dump(source):
        cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'core', %s, %s)""", (task_id, source, "\n".join(collected)))
        collected.clear()

    # Linux/FreeBSD/macOS have backtraces in the "cores" task command
    state = "none"
    source = None
    cursor.execute("""select name, log from task_command where task_id = %s and name = 'cores'""", (task_id,))
    for name, log in cursor.fetchall():
        source = "command:" + name
        for line in log.splitlines():
            # GDB (Linux, FreeBSD) backtraces start with "Thread N", LLDB (macOS) with "thread #N"
            if re.match(r'.* [Tt]hread #?[0-9]+ ?.*', line):
                if state == "in-backtrace":
                    # if multiple core files, dump previous one
                    dump(source)
                state = "in-backtrace"
                continue
            if state == "in-backtrace":
                # GDB stack frames start like " #N ", LLDB like "frame #N:"
                if re.match(r'.* #[0-9]+[: ].*', line):
                    if len(collected) < 10:
                        collected.append(line)
                    else:
                        # that's enough lines for a highlight
                        dump(source)
                        state = "none"
    if state == "in-backtrace":
        dump(source)

    # Windows has backtraces in artifact files
    state = "none"
    cursor.execute("""select name, path, body from artifact where task_id = %s and name = 'crashlog'""", (task_id,))
    for name, path, body, in cursor.fetchall():
        source = "artifact:" + name + "/" + path
        for line in body.splitlines():
            # backtraces start like this:
            if re.match(r'Child-SP.*', line):
                if state == "in-backtrace":
                    # if multiple core files, dump previous one
                    dump(source)
                state = "in-backtrace"
                continue
            if state == "in-backtrace":
                # stack frames start like this:
                if re.match(r'[0-9a-fA-F]{8}`.*', line):
                    if len(collected) < 10:
                        collected.append(line)
                    else:
                        # that's enough lines for a highlight
                        dump(source)
                        state = "none"
    if state == "in-backtrace":
        dump(source)

def ingest_task_artifacts(conn, task_id):
    cursor = conn.cursor()

    # prevent concurrency at the task level (should really be per work item type?)
    cursor.execute("""select from task where task_id = %s for update""", (task_id,))

    # just in case we are re-run, remove older highlights of the type we will insert
    cursor.execute("""delete from highlight where task_id = %s and type in ('sanitizer', 'assertion', 'panic')""", (task_id,))

    # scan all artifact files for patterns we recognise
    cursor.execute("""select name, path, body from artifact where task_id = %s and body is not null""", (task_id,))
    for name, path, body in cursor.fetchall():
        source = "artifact:" + name + "/" + path
        for line in body.splitlines():
            # TODO: put the patterns into a table of precompiled regexes?
            if re.match(r'SUMMARY: .*Sanitizer.*', line):
                cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'sanitizer', %s, %s)""", (task_id, source, line))
            elif re.match(r'.*TRAP: failed Assert.*', line):
                cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'assertion', %s, %s)""", (task_id, source, line))
            elif re.match(r'.*PANIC: .*', line):
                cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'panic', %s, %s)""", (task_id, source, line))

def ingest_task_logs(conn, task_id):
    cursor = conn.cursor()
    cursor.execute("""select from task where task_id = %s for update""", (task_id,))
    cursor.execute("""delete from highlight where task_id = %s and type in ('compiler', 'linker')""", (task_id,))
    cursor.execute("""select name, log from task_command where task_id = %s and (name = 'build' or name like '%%_warning') and log is not null""", (task_id,))
    for name, log in cursor.fetchall():
        source = "command:" + name
        if name == 'build':
            for line in log.splitlines():
                # look for MSVC's warnings
                if re.match(r'.* : (warning|error) [^:]+: .*', line):
                    cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'compiler', %s, %s)""", (task_id, source, line))
        elif name.endswith('_warning'):
            for line in log.splitlines():
                # look for Clang and GCC's warnings
                if re.match(r'.*:[0-9]+: (error|warning): .*', line):
                    cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'compiler', %s, %s)""", (task_id, source, line))
                elif re.match(r'.*: undefined reference to .*', line):
                    cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'linker', %s, %s)""", (task_id, source, line))

def ingest_tests(conn, task_id):
    cursor = conn.cursor()

    # prevent concurrency at the task level (should really be per work item type?)
    cursor.execute("""select from task where task_id = %s for update""", (task_id,))

    # just in case we are re-run, remove older highlights of the type we will insert
    cursor.execute("""delete from highlight where task_id = %s and type in ('regress', 'isolation', 'tap')""", (task_id,))
    cursor.execute("""delete from test where task_id = %s and type = 'tap'""", (task_id,))

    # XXX why do we use different names on Windows and *nix?
    # XXX log should not be null, because this job should only run after fetch-task-logs
    cursor.execute("""select name, log from task_command where task_id = %s and name in ('test_world', 'test_running', 'check_world') and log is not null""", (task_id,))
    for name, log in cursor.fetchall():
        source = "command:" + name
        in_tap_summary = False
        collected_tap = []

        def dump_tap(source):
            if len(collected_tap) > 0:
                cursor.execute("""insert into highlight (task_id, type, source, excerpt) values (%s, 'tap', %s, %s)""", (task_id, source, "\n".join(collected_tap)))
                collected_tap.clear()

        for line in log.splitlines():
            # "structured" test result capture: we want all the results
            # including success (later this might come from meson's .json file
            # so we don't need hairy regexes)
            #
            # note: failures captured here will affect the fetch-task-artifacts
            # job
            groups = re.match(r'.* postgresql:[^ ]+ / ([^ /]+)/([^ ]+) *([A-Z]+) *([0-9.]+s).*', line)
            if groups:
                suite = groups.group(1)
                test = groups.group(2)
                result = groups.group(3)
                duration = groups.group(4)
                cursor.execute("""insert into test (task_id, type, suite, name, result, duration) values (%s, 'tap', %s, %s, %s, %s) on conflict do nothing""", (task_id, suite, test, result, duration))

            # "unstructured" highlight, raw log excerpt
            if re.match(r'.*Summary of Failures:', line):
                dump_tap(source)
                in_tap_summary = True
                continue
            if in_tap_summary and re.match(r'.* postgresql:[^ ]+ / [^ ]+ .*', line):
                collected_tap.append(line)
            elif re.match(r'.*Expected Fail:.*', line):
                dump_tap(source)
                in_tap_summary = False
        dump_tap(source)

    # now that we have the list of failed tests, we can pull down the correct
    # subset of the artifact bodies
    cursor.execute("""insert into work_queue (type, key, status) values ('fetch-task-artifacts', %s, 'NEW')""", (task_id,))
 
def fetch_task_logs(conn, task_id):
    cursor = conn.cursor()
    cores = False

    # find all the commands for this task, and pull down the logs
    cursor.execute("""select name from task_command where task_id = %s""", (task_id,))
    for command, in cursor.fetchall():
      if command == "cores":
        cores = True
      log = binary_to_safe_utf8(cfbot_util.slow_fetch_binary("https://api.cirrus-ci.com/v1/task/%s/logs/%s.log" % (task_id, command)))
      cursor.execute("""update task_command set log = %s where task_id = %s and name = %s""", (log, task_id, command))

    # if we just pulled down 'cores' log (where backtraces show up on
    # Linux/FreeBSD/macOS), create a new job to scan it for highlights
    if cores:
      cursor.execute("""insert into work_queue (type, key, status) values ('ingest-cores', %s, 'NEW')""", (task_id,))

    # index meson test summary (note: we do this before we try to pull down artifact bodies)
    cursor.execute("""insert into work_queue (type, key, status) values ('ingest-tests', %s, 'NEW')""", (task_id,))

    # index compiler/linker warnings and errors
    cursor.execute("""insert into work_queue (type, key, status) values ('ingest-task-logs', %s, 'NEW')""", (task_id,))

def fetch_task_artifacts(conn, task_id):
    cursor = conn.cursor()
    cores = False
    other_artifacts = False

    # download the artifacts for this task.   we want the Windows crashlog ones
    # always, and the testrun ones, but we exclude subdirectories corresponding to
    # tests that passed, to save on disk space
    cursor.execute("""select name, path
                        from artifact
                       where task_id = %s
                         and body is null
                         and (name = 'crashlog' or
                              (name = 'testrun' and
                              substring(regexp_replace(path, 'build/testrun/', '') from '[^/]+/[^/]+') not in
                              (select suite || '/' || name
                                 from test
                                where task_id = %s
                                  and result = 'OK')))""", (task_id, task_id))
    artifacts_to_fetch = cursor.fetchall()
    if len(artifacts_to_fetch) == 0:
        # if that didnt' find any, then perhaps we don't have any "test" rows because
        # this is an autoconf build with unparseable logs.  just download everything (note that artifacts
        # only exist at all if *something* failed, we just don't know what it was)
        cursor.execute("""select name, path from artifact where task_id = %s and body is null""", (task_id,))
        artifacts_to_fetch = cursor.fetchall()

    for name, path in artifacts_to_fetch:
      if name == "crashlog":
        cores = True
      else:
        other_artifacts = True

      url = "https://api.cirrus-ci.com/v1/artifact/task/%s/%s/%s" % (task_id, name, path)
      #print(url)
      log = binary_to_safe_utf8(cfbot_util.slow_fetch_binary(url))
      cursor.execute("""update artifact set body = %s where task_id = %s and name = %s and path = %s""", (log, task_id, name, path))

    # if we pulled down any "crashlog" artifacts (where backtraces show up on
    # Windows), create a new job to scan it for highlights (for non-Windows,
    # backtraces are in task logs)
    if cores:
      cursor.execute("""insert into work_queue (type, key, status) values ('ingest-cores', %s, 'NEW')""", (task_id,))

    # if we pulled down anything else, scan it for highlights
    if other_artifacts:
      cursor.execute("""insert into work_queue (type, key, status) values ('ingest-task-artifacts', %s, 'NEW')""", (task_id,))

def process_one_job(conn, fetch_only):
    cursor = conn.cursor()
    if fetch_only:
      cursor.execute("""select id, type, key, retries from work_queue where type like 'fetch-%' and (status = 'NEW' or (status = 'WORK' and lease < now())) for update skip locked limit 1""")
    else:
      cursor.execute("""select id, type, key, retries from work_queue where status = 'NEW' or (status = 'WORK' and lease < now()) for update skip locked limit 1""")
    row = cursor.fetchone()
    if not row:
      return False
    id, type, key, retries = row
    print("XXX " + type + " " + key);
    if retries and retries >= retry_limit(type):
      cursor.execute("""update work_queue set status = 'FAIL' where id = %s""", (id,))
      id = None
    else:
      cursor.execute("""update work_queue set lease = now() + interval '15 minutes', status = 'WORK', retries = coalesce(retries + 1, 0) where id = %s""", (id,))
    conn.commit()
    if not id:
      return True # done, go around again

    # dispatch to the right work handler
    if type == "fetch-task-logs":
      fetch_task_logs(conn, key)
    elif type == "fetch-task-artifacts":
      fetch_task_artifacts(conn, key)
    elif type == "ingest-cores":
      ingest_cores(conn, key)
    elif type == "ingest-task-artifacts":
      ingest_task_artifacts(conn, key)
    elif type == "ingest-task-logs":
      ingest_task_logs(conn, key)
    elif type == "ingest-tests":
      ingest_tests(conn, key)
    else:
      pass

    # if we made it this far without an error, this work item is done
    cursor.execute("""delete from work_queue where id = %s""", (id,))
    conn.commit()
    return True # go around again

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    #highlight_tests(conn, "4595408144433152")
    #conn.commit()
    #process_one_job(conn)
    while process_one_job(conn, False):
     pass
