#!/usr/bin/env python3

import cfbot_config
import cfbot_util
import cfbot_web
import html
import os

MODES = (
    "all",
    "assertion",
    "compiler",
    "core",
    "linker",
    "panic",
    "regress",
    "sanitizer",
    "tap",
    "test",
)
WHEN = ("current", "7", "30", "90")


def build_page(conn, base_path, mode, when):
    if when == "current":
        suffix = ""
    else:
        suffix = "-" + when
    path = base_path + "/" + mode + suffix + ".html"
    path_tmp = path + ".tmp" + str(os.getpid())
    with open(path_tmp, "w") as f:
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
      <a href="/index.html">Current commitfest</a> |
      <a href="/next.html">Next commitfest</a> |
      <a href="https://wiki.postgresql.org/wiki/Cfbot">FAQ</a> |
      <a href="/statistics.html">Statistics</a> |
      <b>Highlights</b>
    </p>
    <p>Highlight type: """)
        for t in MODES:
            if t == mode:
                f.write("<b>%s</b> " % (t,))
            else:
                f.write(
                    """<a href="/highlights/%s%s.html">%s</a> \n""" % (t, suffix, t)
                )
        f.write("""
    </p>
    <p>Time range: """)
        for t in WHEN:
            if t == "current":
                suffix = ""
                display = "current"
            else:
                suffix = "-" + t
                display = "%s-day" % (t,)
            if t == when:
                f.write("<b>%s</b> " % (display,))
            else:
                f.write(
                    """<a href="/highlights/%s%s.html">%s</a> \n"""
                    % (mode, suffix, display)
                )
        f.write("""
    </p>

    <p>
      This robot generates gigabytes of CI logs every week.  Here is an attempt to
      search for "highlights", so it's easier to find actionable information
      quickly.  New ideas for what patterns to search for are very welcome.
      "Current" shows only the most recent results from each submission.  The
      wider time ranges also show information about historical versions, which
      may be useful for flapping tests, and also for hunting for bugs in master.
    </p>
    <table>
""")
        if mode != "all":
            extra = "and h.type = '%s'" % (mode,)
        else:
            extra = ""
        if when != "current":
            days = when
        else:
            days = None

        cursor = conn.cursor()

        if when == "current":
            # The latest_submission CTE is due to a schema problem that needs to be
            # fixed: we have a separate row for each submission in each commitfest.  We
            # really only care about one.  Need to start treating subsmissions as a single
            # entity that can change commitfest.

            # XXX latest branch might be better as latest build?

            cursor.execute(
                """
with latest_submission as (select distinct on (submission_id)
                                  commitfest_id,
                                  submission_id,
                                  name,
                                  status
                             from submission
                            order by submission_id, commitfest_id desc),
     latest_branch as (select distinct on (submission_id)
                              submission_id,
                              commit_id
                         from branch
                        where commit_id is not null
                        order by submission_id, created desc)
select s.name,
       s.commitfest_id,
       s.submission_id,
       t.task_id,
       t.task_name,
       t.created,
       t.status,
       h.type,
       h.source,
       h.excerpt
  from latest_submission s
  join latest_branch b on b.submission_id = s.submission_id
  join build using (commit_id)
  join task t using (build_id)
  join highlight h on h.task_id = t.task_id
 where s.status in ('Ready for Committer', 'Needs review', 'Waiting on Author')
       %s
 order by t.created desc, t.task_name, h.type, h.source"""
                % (extra,)
            )

        else:
            cursor.execute(
                """
with latest_submission as (select distinct on (submission_id)
                                  commitfest_id,
                                  submission_id,
                                  name
                             from submission
                            order by submission_id, commitfest_id desc),
     latest_branch as (select distinct on (submission_id)
                              commit_id,
                              submission_id
                         from branch
                        order by submission_id, created desc)
select s.name,
       s.commitfest_id,
       s.submission_id,
       t.task_id,
       t.task_name,
       t.created,
       t.status,
       h.type,
       h.source,
       h.excerpt
  from latest_submission s
  join latest_branch b using (submission_id)
  join build using (commit_id)
  join task t using (build_id)
  join highlight h using (task_id)
 where t.created > now() - interval '%s days'
       %s
 order by t.created desc, t.task_name, h.type, h.source
"""
                % (days, extra)
            )

        last_submission_id = 0
        last_task_id = ""
        for (
            name,
            commitfest_id,
            submission_id,
            task_id,
            task_name,
            created,
            status,
            type,
            source,
            excerpt,
        ) in cursor.fetchall():
            if last_submission_id != submission_id:
                f.write(
                    """
            <tr>
              <td width="10%%" id="%s">%d/%d</td>
              <td width="90%%">%s</td>
            </tr>"""
                    % (
                        submission_id,
                        commitfest_id,
                        submission_id,
                        html.escape(name),
                    )
                )
            last_submission_id = submission_id
            if last_task_id != task_id:
                if status == "COMPLETED":
                    icon = cfbot_web.svg_img("new_success")
                else:
                    icon = cfbot_web.svg_img("new_failure")
                f.write(
                    """
            <tr>
              <td width="10%%" align="right"><a href="https://cirrus-ci.com/task/%s">%s</a></td>
              <td width="90%%"><a href="https://cirrus-ci.com/task/%s">%s</td></td>
            </tr>"""
                    % (task_id, icon, task_id, html.escape(task_name))
                )
            last_task_id = task_id

            if source.startswith("artifact:"):
                url = "https://api.cirrus-ci.com/v1/artifact/task/%s/%s" % (
                    task_id,
                    source[9:],
                )
            elif source.startswith("command:"):
                url = "https://api.cirrus-ci.com/v1/task/%s/logs/%s.log" % (
                    task_id,
                    source[8:],
                )
            else:
                url = "https://google.com"

            def trunc(line):
                if len(line) > 120:
                    return line[:120] + "..."
                return line

            narrow_excerpt = "\n".join([trunc(line) for line in excerpt.splitlines()])

            f.write(
                """
          <tr>
            <td width="10%%"><a href="%s">%s</a></td>
            <td width="90%%"><pre style="font-size: 9px">%s</pre></td>
          </tr>
    """
                % (url, html.escape(type), html.escape(narrow_excerpt))
            )

        f.write("""
    </table>
  </body>
</html>
""")
    os.rename(path_tmp, path)


def rebuild_type(conn, type):
    for when in WHEN:
        build_page(conn, os.path.join(cfbot_config.WEB_ROOT, "highlights"), type, when)


def rebuild_all(conn):
    for mode in MODES:
        for when in WHEN:
            build_page(
                conn, os.path.join(cfbot_config.WEB_ROOT, "highlights"), mode, when
            )


if __name__ == "__main__":
    with cfbot_util.db() as conn:
        rebuild_all(conn)
