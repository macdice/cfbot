#!/usr/bin/env python

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
import math
import os
import re
import unicodedata

from cfbot_commitfest_rpc import Submission

NEW_SUCCESS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%s</title>
  <circle cx="26" cy="26" r="25" fill="green"/>
  <path stroke-width="3" fill="none" stroke="white" d="M14.1 27.2 l7.1 7.2 16.7-16.8"/>
</svg>"""

OLD_SUCCESS = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%s</title>
  <circle cx="26" cy="26" r="25" stroke="green" fill="none"/>
  <path stroke-width="3" fill="none" stroke="green" d="M14.1 27.2 l7.1 7.2 16.7-16.8"/>
</svg>"""

NEW_FAILURE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%s</title>
  <circle cx="26" cy="26" r="25" fill="red"/>
  <path stroke-width="3" fill="none" stroke="white" d="M17 17 35 35"/>
  <path stroke-width="3" fill="none" stroke="white" d="M17 35 35 17"/>
</svg>"""

OLD_FAILURE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%s</title>
  <circle cx="26" cy="26" r="25" stroke="red" fill="none"/>
  <path stroke-width="3" fill="none" stroke="red" d="M17 17 35 35"/>
  <path stroke-width="3" fill="none" stroke="red" d="M17 35 35 17"/>
</svg>"""

WAITING_TO_START = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%s</title>
  <circle cx="26" cy="26" r="25" stroke="gray" fill="none"/>
</svg>"""

def building(fraction):
  if fraction > 0.5:
    large = 1
  else:
    large = 0
  fraction -= 0.25
  return """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%%s</title>
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
                     WHERE s.commitfest_id >= commitfest_id
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
    results.append(submission)

    # check if we were able to apply the patch(es); if not,
    # we'll synthesise a task that represents the apply failure
    cursor.execute("""SELECT commit_id, status, url
                        FROM branch
                       WHERE commitfest_id = %s
                         AND submission_id = %s
                    ORDER BY modified DESC LIMIT 1""",
                    (commitfest_id, submission_id))
    row = cursor.fetchone()
    if not row:
        continue
    commit_id, status, url = row
    if status == 'failed':
        r = BuildResult("Apply patches", "FAILED", url, False, None, True, 0)
        submission.build_results.append(r)
        continue

    # get latest build status from each task, and also figure out if it's
    # new or had a different status in the past 24 hours
    cursor.execute("""SELECT b.task_name, b.status, b.task_id, b.commit_id,
                             b.modified > now() - interval '24 hours',
                             EXTRACT(epoch FROM now() - b.modified)
                        FROM task b
                       WHERE b.commitfest_id = %s
                         AND b.submission_id = %s
                    ORDER BY b.position, b.modified DESC""",
                   (commitfest_id, submission_id))
    seen = {}
    for task_name, status, task_id, b_commit_id, recent, age in cursor.fetchall():
      url = "https://cirrus-ci.com/task/" + task_id
      if task_name not in seen:
        if b_commit_id != commit_id:
          continue # Ignore tasks whose latest entry is not from the commit_id we want
        r = BuildResult(task_name, status, url, recent, None, True, age)
        submission.build_results.append(r)
        seen[task_name] = r
      else:
        r = seen[task_name]
        r.only = False # there is more than one result
        if (recent or r.change == None) and status != r.status:
          r.change = True

    # figure out if it deserves to be flags as new/interesting
    for r in submission.build_results:
      r.new = (r.only or r.change) and r.recent

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
      <a href="https://wiki.postgresql.org/wiki/Cfbot">FAQ</a>
    </p>
    <p>
      Here lives an experimental bot that converts email threads that are registered in the
      <a href="https://commitfest.postgresql.org/%s">Commitfest system</a> into
      <a href="https://github.com/postgresql-cfbot/postgresql/branches">branches on Github</a>,
      and collates test results from
      <a href="https://cirrus-ci.com/github/postgresql-cfbot/postgresql">Cirrus CI</a>.
    </p>
    <table>
""" % (commitfest_id_for_link,))
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
        f.write("""      <tr><td colspan="5"><h2>%s</h2></td></tr>\n""" % status)
        last_status = status

      name = submission.name
      if len(name) > 80:
        name = name[:80] + "..."

      # convert list of authors into links
      author_links = []
      for author in all_authors(submission):
        author_links.append("""<a href="%s">%s</a>""" % (make_author_url(author), author))
      author_links_string = ", ".join(author_links)

      # construct build results
      build_results = ""
      for build_result in submission.build_results:
        alt = build_result.task_name + ": " + build_result.status
        if build_result.status == "COMPLETED":
          if build_result.new:
            alt += " (new)"
            html = NEW_SUCCESS
          else:
            html = OLD_SUCCESS
        elif build_result.status in ("FAILED", "ABORTED", "ERRORED"):
          if build_result.new:
            alt += " (new)"
            html = NEW_FAILURE
          else:
            html = OLD_FAILURE
        elif build_result.status in ("CREATED"):
            html = WAITING_TO_START
        else:
          # hocus pocus time prediction
          if build_result.task_name in expected_runtimes:
            expected_runtime = expected_runtimes[build_result.task_name]
          else:
            expected_runtime = 60 * 30
          if build_result.age > 0:
            fraction = build_result.age / expected_runtime
          else:
            fraction = 0.1
          if fraction <= 0:
            fraction = 0.1
          if fraction >= 0.9:
            fraction = 0.9
          html = building(fraction)
        html = html % alt
        if build_result.url:
          html = """<a href="%s">%s</a>""" % (build_result.url, html)
        build_results += "&nbsp;" + html

      # construct email link
      patch_html = ""
      if submission.last_branch_message_id:
        patch_html = """<a title="Patch email" href="https://www.postgresql.org/message-id/%s">\u2709</a>""" % submission.last_branch_message_id
      patch_html += """ <a title="Test history" href="https://cirrus-ci.com/github/postgresql-cfbot/postgresql/commitfest/%s/%s">H</a>""" % (submission.commitfest_id, submission.id)

      # write out an entry
      f.write("""
      <tr>
        <td width="8%%">%s/%s</td>
        <td width="42%%"><a href="https://commitfest.postgresql.org/%s/%s/">%s</a></td>
        <td width="20%%">%s</td>
        <td width="5%%" align="right">%s</td>
        <td width="25%%">%s</td>
""" % (submission.commitfest_id, submission.id, submission.commitfest_id, submission.id, name, author_links_string, patch_html, build_results))
      f.write("      </tr>\n")
    f.write("""
    </table>
  </body>
</html>
""")
  os.rename(path + ".tmp", path)

def unique_authors(submissions):
  results = []
  for submission in submissions:
    results += all_authors(submission)
  return list(set(results))

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    #rebuild(conn, commitfest_id)
    #commitfest_id = cfbot_commitfest_rpc.get_current_commitfest_id()
    submissions = load_submissions(conn, 39)
    build_page(conn, "x", 39, submissions, None, None, os.path.join(cfbot_config.WEB_ROOT, "index2.html"))
