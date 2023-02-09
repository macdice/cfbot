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
      only configure, build and test.  Week, month and year windows are used so
      we can see (very crudely) if it's speeding up or slowing down.
    </p>
    <table>
      <tr>
        <td>Task</td>
        <td>Step</td>
        <td colspan="3" align="center">7-day</td>
        <td colspan="3" align="center">30-day</td>
        <td colspan="3" align="center">365-day</td>
      </tr>
      <tr>
        <td colspan="2"></td>
        <td>Count</td>
        <td>Avg</td>
        <td>Stddev</td>
        <td>Count</td>
        <td>Avg</td>
        <td>Stddev</td>
        <td>Count</td>
        <td>Avg</td>
        <td>Stddev</td>
      </tr>
""")
    cursor = conn.cursor()
    cursor.execute("""
select t.task_name,
       c.name,
       count(*) filter (where created > now() - interval '1 week') as count_7,
       avg(extract(epoch from duration)) filter (where created > now() - interval '1 week') as avg_7,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '1 week') as stddev_7,
       count(*) filter (where created > now() - interval '1 month') as count_30,
       avg(extract(epoch from duration)) filter (where created > now() - interval '1 month') as avg_30,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '1 month') as stddev_30,
       count(*) filter (where created > now() - interval '1 year') as count_365,
       avg(extract(epoch from duration)) filter (where created > now() - interval '1 year') as avg_365,
       stddev(extract(epoch from duration)) filter (where created > now() - interval '1 year') as stddev_365
  from task t
  join task_command c using (task_id)
 where c.name in ('configure', 'build', 'test_world', 'check_world')
   and t.status = 'COMPLETED'
 group by 1, 2
 order by 1, 2
""")
    last_task = ""
    for task, command, c7, a7, d7, c30, a30, d30, c365, a365, d365 in cursor.fetchall():
      if task == last_task:
        task = ""
      else:
        last_task = task
      f.write("""
      <tr>
        <td>%s</td>
        <td>%s</td>
        <td align="right">%d</td>
        <td align="right">%.2f</td>
        <td align="right">%.2f</td>
        <td align="right">%d</td>
        <td align="right">%.2f</td>
        <td align="right">%.2f</td>
        <td align="right">%d</td>
        <td align="right">%.2f</td>
        <td align="right">%.2f</td>
      </tr>
""" %
      (task, command, c7, a7, d7, c30, a30, d30, c365, a365, d365))
    f.write("""
    </table>
  </body>
</html>
""")
  os.rename(path + ".tmp", path)

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    build_page(conn, os.path.join(cfbot_config.WEB_ROOT, "statistics.html"))
