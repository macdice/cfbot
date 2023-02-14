#!/usr/bin/env python

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import math
import os
import re
import unicodedata

from cfbot_commitfest_rpc import Submission

def build_page(conn, path):

  with open(path + ".tmp", "w") as f:
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
      <a href="statistics.html">Statistics</a>
    </p>
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
  </body>
</html>
""")
  os.rename(path + ".tmp", path)

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    build_page(conn, os.path.join(cfbot_config.WEB_ROOT, "statistics.html"))
