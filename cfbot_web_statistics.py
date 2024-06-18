#!/usr/bin/env python3

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import math
import os
import re
import unicodedata

from cfbot_commitfest_rpc import Submission

def header(f):
    f.write("""<html>
  <head>
    <meta charset="UTF-8"/>
    <title>PostgreSQL Patch Tester</title>
    <style type="text/css">
      body {
        margin: 1rem auto;
        font-family: -apple-system,BlinkMacSystemFont,avenir next,avenir,helvetica neue,helvetica,ubuntu,roboto,noto,segoe ui,arial,sans-serif;
        color: #444;
        max-width: 920px;
      }
      h1 {
        font-size: 3rem;
      }
      h2 {
        font-size: 2rem;
      }
      table {
        border-collapse: collapse;
      	font-size: 0.875rem;
        width: 100%%;
      }
      td {
        padding: 1rem 1rem 1rem 0;
        border-bottom: solid 1px rgba(0,0,0,.2);
      }
    </style>
  </head>
  <body>
    <h1>PostgreSQL Patch Tester</h1>
    <p>
      <a href="index.html">Current commitfest</a> |
      <a href="next.html">Next commitfest</a> |
      <a href="https://wiki.postgresql.org/wiki/Cfbot">FAQ</a> |
      <b>Statistics</b> |
      <a href="highlights/all.html">Highlights</a>
    </p>
""")

def per_day(f, conn):
    f.write("""
    <h2>Per day</h2>
    <p>
      Resources consumed per UTC day over the past month, according to Cirrus's reported "duration" value.
      Also average and stddev, but these only count successful runs because otherwise fast failures would make the data hard to interpret.
    </p>
    <table>
      <tr>
        <td width="20%">Task</td>
        <td width="10%">Date</td>
        <td width="20%" align="center">Total time</td>
        <td width="20%" align="center">Avg (success)</td>
        <td width="20%" align="center">Stddev (success)</td>
        <td width="10%" align="center">Count</td>
      </tr>
""")
    cursor = conn.cursor()
    cursor.execute("""
with subtotals as (
select t.created::date date,
       t.task_name,
       t.task_id,
       t.status,
       sum(c.duration) duration
  from task t join task_command c using (task_id)
 where t.created between date_trunc('day', now() - interval '30 days') and
                         date_trunc('day', now())
    and t.status not in ('CREATED', 'ABORTED')
 group by 1, 2, 3)
select task_name, date, 
       
       sum(duration),
       avg(duration) filter (where status = 'COMPLETED') avg_success,
       stddev(extract(epoch from duration)) filter (where status = 'COMPLETED') stddev_success,
       count(*)
  from subtotals
 group by date, task_name
 order by task_name collate "en_US", date
""")
    last_task = None
    for task, date, s, avg, stddev, count in cursor.fetchall():
      if task == last_task:
        task = ""
      else:
        last_task = task
      if stddev == None:
        stddev = 0
      f.write("""
      <tr>
        <td>%s</td>
        <td>%s</td>
        <td align="right">%s</td>
        <td align="right">%s</td>
        <td align="right">%.2f</td>
        <td align="right">%d</td>
      </tr>
""" %
      (task, date, s, avg, stddev, count))
    f.write("""
    </table>
    """)

 
def per_task(f, conn):
    f.write("""
    <h2>Per task</h2>
    <p>
      Time taken, in seconds, for successfully completed task steps.  Showing
      only configure, build and test.  All numbers are shown as 7-day, 30-day
      and 90-day windows.  Perhaps we can see (very crudely) if it's speeding
      up or slowing down.
    </p>
    <table>
      <tr>
        <td width="20%">Task</td>
        <td width="20%">Step</td>
        <td width="20%" align="center">Avg.</td>
        <td width="20%" align="center">Std. dev.</td>
        <td width="20%" align="center">Count</td>
      </tr>
""")

    cursor = conn.cursor()
    cursor.execute("""
select t.task_name,
       c.name,
       count(*) filter (where created > now() - interval '7 days') as count_7,
       avg(extract(epoch from duration)) filter (where created > now() - interval '7 days') as avg_7,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '7 days') as stddev_7,
       count(*) filter (where created > now() - interval '30 days') as count_30,
       avg(extract(epoch from duration)) filter (where created > now() - interval '30 days') as avg_30,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '30 days') as stddev_30,
       count(*) filter (where created > now() - interval '90 days') as count_90,
       avg(extract(epoch from duration)) filter (where created > now() - interval '90 days') as avg_90,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '90 days') as stddev_90
  from task t
  join task_command c using (task_id)
 where c.name in ('configure', 'configure_32', 'build', 'build_32', 'test_world', 'test_world_32', 'check_world')
   and t.status = 'COMPLETED'
 group by 1, 2
 order by 1, 2
""")
    last_task = ""
    for task, command, c7, a7, d7, c30, a30, d30, c90, a90, d90 in cursor.fetchall():
      if task == last_task:
        task = ""
      else:
        last_task = task
      if not a7: a7 = 0
      if not d7: d7 = 0
      if not a30: a30 = 0
      if not d30: d30 = 0
      if not a90: a90 = 0
      if not d90: d90 = 0
      f.write("""
      <tr>
        <td>%s</td>
        <td>%s</td>
        <td align="right">%.2f, %.2f, %.2f</td>
        <td align="right">%.2f, %.2f, %.2f</td>
        <td align="right">%d, %d, %d</td>
      </tr>
""" %
      (task, command, a7, a30, a90, d7, d30, d90, c7, c30, c90))
    f.write("""
    </table>
    """)

def per_test(f, conn):
    cursor = conn.cursor()
    f.write("""
    <h2>Per test</h2>
    <p>
      Time taken for individual tests (Meson builds only, successful tasks
      only).  Again, numbers are 7-day, 30-day, 90-day.
    </p>
    <table>
      <tr>
        <td width="20%">Task</td>
        <td width="10%">Suite</td>
        <td width="10%">Test</td>
        <td width="20%" align="center">Avg.</td>
        <td width="20%" align="center">Std. dev.</td>
        <td width="20%" align="center">Count</td>
      </tr>
""")

    cursor.execute("""
select task.task_name,
       test.command,
       test.suite,
       test.name,
       count(*) filter (where created > now() - interval '7 days') as count_7,
       avg(extract(epoch from duration)) filter (where created > now() - interval '7 days') as avg_7,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '7 days') as stddev_7,
       count(*) filter (where created > now() - interval '30 days') as count_30,
       avg(extract(epoch from duration)) filter (where created > now() - interval '30 days') as avg_30,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '30 days') as stddev_30,
       count(*) filter (where created > now() - interval '90 days') as count_90,
       avg(extract(epoch from duration)) filter (where created > now() - interval '90 days') as avg_90,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '90 days') as stddev_90
  from task
  join test using (task_id)
 where task.status = 'COMPLETED'
   and test.result = 'OK'
 group by 1, 2, 3, 4
 order by 1, 2, 3, 4
""")
    last_task = ""
    last_suite = ""
    for task, command, suite, test, c7, a7, d7, c30, a30, d30, c90, a90, d90 in cursor.fetchall():
      if command.endswith('32'):
          task += "/32" # rather than wasting a whole column on "command"
      if task == last_task:
        task = ""
      else:
        last_task = task
      if suite == last_suite:
        suite = ""
      else:
        last_suite = suite
      if not a7: a7 = 0
      if not d7: d7 = 0
      if not a30: a30 = 0
      if not d30: d30 = 0
      if not a90: a90 = 0
      if not d90: d90 = 0
      f.write("""
      <tr>
        <td>%s</td>
        <td>%s</td>
        <td>%s</td>
        <td align="right">%.2f, %.2f, %.2f</td>
        <td align="right">%.2f, %.2f, %.2f</td>
        <td align="right">%d, %d, %d</td>
      </tr>
""" %
      (task, suite, test, a7, a30, a90, d7, d30, d90, c7, c30, c90))

    f.write("""
    </table>
    """)

def footer(f):
    f.write("""
  </body>
</html>
""")

def build_page(conn, path):
  with open(path + ".tmp", "w") as f:
    header(f)
    per_day(f, conn)
    per_task(f, conn)
    per_test(f, conn)
    footer(f)
  os.rename(path + ".tmp", path)

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    build_page(conn, os.path.join(cfbot_config.WEB_ROOT, "statistics.html"))
