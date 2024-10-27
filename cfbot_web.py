#!/usr/bin/env python3

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import math
import os
import re
import unicodedata
from html import escape as html_escape

from cfbot_commitfest_rpc import Submission

# Define SVG content
SVG_CONTENT = {
    "new_success": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
      <circle cx="26" cy="26" r="25" fill="green"/>
      <path stroke-width="3" fill="none" stroke="white" d="M14.1 27.2 l7.1 7.2 16.7-16.8"/>
    </svg>""",
    "old_success": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
      <circle cx="26" cy="26" r="25" stroke="green" fill="none"/>
      <path stroke-width="3" fill="none" stroke="green" d="M14.1 27.2 l7.1 7.2 16.7-16.8"/>
    </svg>""",
    "needs_rebase_success": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
      <circle stroke-width="3" cx="26" cy="26" r="25" stroke="#ff8f00" fill="none"/>
      <path stroke-width="5" fill="none" stroke="#ff8f00" d="M14.1 27.2 l7.1 7.2 16.7-16.8"/>
    </svg>""",
    "new_failure": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
      <circle cx="26" cy="26" r="25" fill="red"/>
      <path stroke-width="5" fill="none" stroke="white" d="M17 17 35 35"/>
      <path stroke-width="5" fill="none" stroke="white" d="M17 35 35 17"/>
    </svg>""",
    "old_failure": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
      <circle stroke-width="3" cx="26" cy="26" r="25" stroke="red" fill="none"/>
      <path stroke-width="4" fill="none" stroke="red" d="M17 17 35 35"/>
      <path stroke-width="4" fill="none" stroke="red" d="M17 35 35 17"/>
    </svg>""",
    "waiting_to_start": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
      <circle cx="26" cy="26" r="25" stroke="gray" fill="none"/>
    </svg>"""
}

# Generate HTML with <img> tag for a specific SVG type
def svg_img(svg_type):
    if svg_type not in SVG_CONTENT:
        raise ValueError(f"Invalid SVG type: {svg_type}")
    html_template = '<img src="{src}" alt="" width="20" height="20"/>'
    return html_template.format(src=f"{svg_type}.svg")

def building(fraction):
  if fraction > 0.5:
    large = 1
  else:
    large = 0
  fraction = float(fraction)
  fraction -= 0.25
  return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <circle cx="26" cy="26" r="25" stroke="blue" fill="none"/>
  <path d="M26 26 L26 1 A25 25 0 %s 1 %s %s Z" fill="blue"/>
</svg>""" % (large, 26 + 25 * math.cos(math.pi * (fraction * 2)), 26 + 25 * math.sin(math.pi * (fraction * 2)))

class BuildResult:
  def __init__(self, task_name, status, url, recent, change, only, age):
    self.task_name = task_name
    self.url = url
    self.status = status
    self.recent = recent
    self.change = change
    self.only = only
    self.new = False
    self.age = age

def load_expected_runtimes(conn):
  cursor = conn.cursor()
  cursor.execute("""SELECT task_name,
                           EXTRACT(epoch FROM avg(modified - created))
                      FROM task
                     WHERE status = 'COMPLETED'
                       AND created > now() - INTERVAL '12 hours'
                  GROUP BY 1""")
  results = {}
  for task_name, seconds in cursor.fetchall():
    results[task_name] = seconds
  return results

def load_submissions(conn, commitfest_id):
  results = []
  cursor = conn.cursor()
  cursor.execute("""SELECT s.commitfest_id,
                           s.submission_id,
                           s.name,
                           s.authors,
                           s.status,
                           s.last_branch_message_id
                      FROM submission s
                     WHERE s.commitfest_id >= %s
                       AND s.status IN ('Ready for Committer',
                                        'Needs review',
                                        'Waiting on Author')
                  ORDER BY CASE s.status
                             WHEN 'Ready for Committer' THEN 0
                             WHEN 'Needs review' THEN 1
                             ELSE 2
                           END,
                           s.name""",
                  (commitfest_id,))
  for commitfest_id, submission_id, name, authors, status, last_branch_message_id in cursor.fetchall():
    submission = Submission(submission_id, commitfest_id, name, status, authors, None)
    submission.last_branch_message_id = last_branch_message_id
    submission.has_highlights = False
    results.append(submission)

    # find the latest branch
    submission.apply_failed_url = None
    submission.apply_failed_since = None
    cursor.execute("""SELECT commit_id, status, url
                        FROM branch
                       WHERE submission_id = %s
                    ORDER BY created DESC LIMIT 1""",
                    (submission_id,))
    row = cursor.fetchone()
    if not row:
        # no branches at all yet
        continue
    commit_id, status, url = row

    # did it fail to apply?
    if status == 'failed':
        submission.apply_failed_url = url

        # find the most recent commit from when we succeeded in applying
        # so we can show that instead
        cursor.execute("""SELECT commit_id, status, url, created
                            FROM branch
                           WHERE submission_id = %s
                             AND commit_id IS NOT NULL
                        ORDER BY created DESC LIMIT 1""",
                       (submission_id,))
        row = cursor.fetchone()
        if row:
            commit_id, status, url, created = row

            # also find the date on which we first started failing after
            # that successful branch construction
            cursor.execute("""SELECT to_char(created AT TIME ZONE 'GMT', 'YYYY-MM-DD')
                            FROM branch
                           WHERE submission_id = %s
                             AND created > %s
                        ORDER BY created LIMIT 1""",
                            (submission_id, created))
              
            row = cursor.fetchone()
            if row:
                submission.apply_failed_since = row[0]

    # see if there are any highlights for this commit
    cursor.execute("""SELECT DISTINCT type
                        FROM highlight
                        JOIN task USING (task_id)
                       WHERE task.commit_id = %s
                       ORDER BY 1
                       """,
                   (commit_id,))
    types = []
    for type, in cursor.fetchall():
        types.append(type)
    submission.has_highlights = types

    # get latest build status from each task, and also figure out if it's
    # new or had a different status in the past 24 hours
    cursor.execute("""
WITH task_positions AS (SELECT DISTINCT ON (task_name)
                               task_name,
                               position
                          FROM task
                         WHERE commit_id = %s
                      ORDER BY task_name, modified),
     latest_tasks AS   (SELECT DISTINCT ON (task_name)
                               task_name,
                               task_id,
                               status,
                               EXTRACT(epoch FROM now() - modified) AS age
                          FROM task
                         WHERE commit_id = %s
                      ORDER BY task_name, modified DESC),
     prev_statuses AS  (SELECT DISTINCT ON (task_name)
                               task_name,
                               status AS prev_status
                          FROM task
                         WHERE commit_id != %s
                           AND submission_id = %s
                      ORDER BY task_name, modified DESC)
     SELECT task_id,
            task_name,
            age,
            status,
            status IS DISTINCT FROM prev_status AS is_new
       FROM latest_tasks
       JOIN task_positions USING (task_name)
  LEFT JOIN prev_statuses USING (task_name)
      WHERE task_name NOT LIKE '% MinGW64 %'
   ORDER BY position
    """, (commit_id, commit_id, commit_id, submission_id))
    for task_id, task_name, age, status, is_new in cursor.fetchall():
      url = "https://cirrus-ci.com/task/" + task_id
      r = BuildResult(task_name, status, url, False, None, True, age)
      r.new = is_new
      submission.build_results.append(r)

  return results

def rebuild(conn, commitfest_id):
  submissions = load_submissions(conn, commitfest_id)
  build_page(conn, "x", commitfest_id, submissions, None, None, os.path.join(cfbot_config.WEB_ROOT, "index.html"))
  build_page(conn, "x", commitfest_id + 1, submissions, None, None, os.path.join(cfbot_config.WEB_ROOT, "next.html"))
  for author in unique_authors(submissions):
    build_page(conn, "x", None, submissions, author, None, os.path.join(cfbot_config.WEB_ROOT, make_author_url(author)))

def make_author_url(author):
    text = author.strip()
    #text = str(text, "utf-8")
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore')
    text = text.decode("utf-8")
    text = str(text).lower()
    text = re.sub('[ ]+', '-', text)
    text = re.sub('[^0-9a-zA-Z_-]', '', text)
    return text + ".html"

def all_authors(submission):
  return submission.authors


def build_page(conn, commit_id, commitfest_id, submissions, filter_author, activity_message, path):
  """Build a web page that lists all known entries and shows the badges."""

  save_svgs()

  expected_runtimes = load_expected_runtimes(conn)
  last_status = None
  commitfest_id_for_link = commitfest_id
  if commitfest_id_for_link == None:
    commitfest_id_for_link = ""
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
      <a href="statistics.html">Statistics</a> |
      <a href="highlights/all.html">Highlights</a>
    </p>
    <p>
      Here lives an experimental bot that converts email threads that are registered in the
      <a href="https://commitfest.postgresql.org/%s">Commitfest system</a> into
      <a href="https://github.com/postgresql-cfbot/postgresql/branches">branches on Github</a>,
      and collates test results from
      <a href="https://cirrus-ci.com/github/postgresql-cfbot/postgresql">Cirrus CI</a>.  Key: %s or %s = new/recently changed, %s or %s = stable, %s needs rebase but previous build was successful, %s = working.
    </p>
    <table>
""" % (commitfest_id_for_link, svg_img('new_success'), svg_img('new_failure'), svg_img('old_success'), svg_img('old_failure'), svg_img('needs_rebase_success'), building(0.3)))
    for submission in submissions:

      # skip if we need to filter by commitfest
      if commitfest_id != None and submission.commitfest_id != commitfest_id:
        continue

      # skip if we need to filter by author
      if filter_author != None and filter_author not in all_authors(submission):
        continue

      # create a new heading row if this is a new CF status
      status = submission.status
      if last_status == None or last_status != status:
        f.write("""      <tr><td colspan="5"><h2>%s</h2></td></tr>\n""" % html_escape(status))
        last_status = status

      name = submission.name
      if len(name) > 80:
        name = name[:80] + "..."

      # convert list of authors into links
      author_links = []
      for author in all_authors(submission):
        author_links.append("""<a href="%s">%s</a>""" % (html_escape(make_author_url(author), quote=True), html_escape(author)))
      author_links_string = ", ".join(author_links)

      # construct build results
      build_results = ""
      for build_result in submission.build_results:
        alt = build_result.task_name + ": " + build_result.status
        if build_result.status == "COMPLETED":
            if submission.apply_failed_url:
                alt += " (rebase needed)"
                html = svg_img('needs_rebase_success')
            elif build_result.new:
                alt += " (new)"
                html = svg_img('new_success')
            else:
                html = svg_img('old_success')
        elif build_result.status in ("FAILED", "ABORTED", "ERRORED"):
            if build_result.new:
                alt += " (new)"
                html = svg_img('new_failure')
            else:
                html = svg_img('old_failure')
        elif build_result.status == "CREATED":
            html = svg_img('waiting_to_start')
        else:
          # hocus pocus time prediction
          if build_result.task_name in expected_runtimes:
            expected_runtime = expected_runtimes[build_result.task_name]
          else:
            expected_runtime = 60 * 30
          if build_result.age > 0 and expected_runtime > 0:
            fraction = build_result.age / expected_runtime
          else:
            fraction = 0.1
          if fraction <= 0:
            fraction = 0.1
          if fraction >= 0.9:
            fraction = 0.9
          html = building(fraction)
        if build_result.url:
          html = """<a href="%s" title="%s">%s</a>""" % (html_escape(build_result.url, quote=True), html_escape(alt), html)
        build_results += "&nbsp;" + html

      # construct email link
      patch_html = ""
      if submission.apply_failed_url:
        if submission.apply_failed_since:
          patch_html += """<a title="Rebase needed since %s" href="%s">\u2672</a>""" % (submission.apply_failed_since, html_escape(submission.apply_failed_url, quote=True))
        else:
          patch_html += """<a title="Rebase needed" href="%s">\u2672</a>""" % html_escape(submission.apply_failed_url, quote=True)
      if submission.has_highlights:
          patch_html += """&nbsp;<a title="Interesting log excerpts found: %s" href="/highlights/all.html#%s">\u26a0</a>""" % (html_escape(", ".join(submission.has_highlights), quote=True), submission.id)
      if submission.last_branch_message_id:
        patch_html += """&nbsp;<a title="Patch email" href="https://www.postgresql.org/message-id/%s">\u2709</a>""" % html_escape(submission.last_branch_message_id, quote=True)
      branch = f"cf/{submission.id}"
      patch_html += f"""&nbsp;<a title="Diff on GitHub" href="https://github.com/{cfbot_config.GITHUB_FULL_REPO}/compare/{branch}~1...{branch}">D</a>"""
      patch_html += f"""&nbsp;<a title="Test history" href="https://cirrus-ci.com/github/{cfbot_config.GITHUB_FULL_REPO}/{branch}">H</a>"""

      # write out an entry
      f.write("""
      <tr>
        <td width="8%%">%s/%s</td>
        <td width="42%%"><a href="https://commitfest.postgresql.org/%s/%s/">%s</a></td>
        <td width="20%%">%s</td>
        <td width="5%%" align="right">%s</td>
        <td width="25%%">%s</td>
""" % (submission.commitfest_id, submission.id, submission.commitfest_id, submission.id, html_escape(name), author_links_string, patch_html, build_results))
      f.write("      </tr>\n")
    f.write("""
    </table>
  </body>
</html>
""")
  os.rename(path + ".tmp", path)


# Save SVG content to files
def save_svgs():
    if not os.path.exists(cfbot_config.WEB_ROOT):
        os.makedirs(cfbot_config.WEB_ROOT)
    for name, content in SVG_CONTENT.items():
        with open(os.path.join(cfbot_config.WEB_ROOT, f"{name}.svg"), "r") as file:
            if file.read() == content:
                continue
        with open(os.path.join(cfbot_config.WEB_ROOT, f"{name}.svg"), "w") as file:
            file.write(content)


def unique_authors(submissions):
  results = []
  for submission in submissions:
    results += all_authors(submission)
  return list(set(results))

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    #rebuild(conn, commitfest_id)
    #commitfest_id = cfbot_commitfest_rpc.get_current_commitfest_id()
    submissions = load_submissions(conn, 42)
    build_page(conn, "x", 42, submissions, None, None, os.path.join(cfbot_config.WEB_ROOT, "index2.html"))
