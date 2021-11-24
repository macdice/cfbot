#!/usr/bin/env python

import cfbot_commitfest_rpc
import cfbot_config
import cfbot_util
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

BUILDING = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 52" width="20" height="20">
  <title>%s</title>
  <circle cx="26" cy="26" r="25" stroke="blue" fill="none"/>
  <path d="M26 26 L26 1 A25 25 0 0 1 51 26 Z" fill="blue"/>
</svg>"""

class BuildResult:
  def __init__(self, provider, status, url, recent, change, only):
    self.provider = provider
    self.url = url
    self.status = status
    self.recent = recent
    self.change = change
    self.only = only
    self.new = False
  
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

    # get latest build status from each provider, and also figure out if it's
    # new or had a different status in the past 24 hours
    cursor.execute("""SELECT b.provider, b.result, b.url,
                             b.modified > now() - interval '24 hours'
                        FROM build_result b
                       WHERE b.commitfest_id = %s
                         AND b.submission_id = %s
                         AND (b.provider = 'apply' OR b.url IS NOT NULL)
                    ORDER BY b.provider, b.modified DESC""",
                   (commitfest_id, submission_id))
    seen = {}
    for provider, result, url, recent in cursor.fetchall():
      if provider not in seen:
        r = BuildResult(provider, result, url, recent, None, True)
        submission.build_results.append(r)
        seen[provider] = r
      else:
        r = seen[provider]
        r.only = False # there is more than one result
        if (recent or r.change == None) and result != r.status:
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
      Here lives an experimental bot that does this:
      <a href="https://commitfest.postgresql.org/%s">PostgreSQL Commitfest</a>
      &rarr; 
      <a href="https://github.com/postgresql-cfbot/postgresql/branches">Github</a>
      &rarr;
      (
        <a href="https://ci.appveyor.com/project/postgresql-cfbot/postgresql/history">AppVeyor</a>,
        <a href="https://cirrus-ci.com/github/postgresql-cfbot/postgresql">Cirrus CI</a> ).
        The results appear in the following order: apply, Windows/amd64 check, FreeBSD/amd64 check-world, Linux/aarch64 check-world + docs, macOS/amd64 check.
        The plan is eventually to show them on the Commitfest app.
    </p>
    <p>
      <a href="index.html">Current commitfest</a> |
      <a href="next.html">Next commitfest</a>
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
        alt = build_result.provider
        if build_result.status == "success":
          if build_result.new:
            html = NEW_SUCCESS
            alt += " success (new)"
          else:
            html = OLD_SUCCESS
            alt += " success"
        elif build_result.status == "failure":
          if build_result.new:
            html = NEW_FAILURE
            alt += " failure (new)"
          else:
            html = OLD_FAILURE
            alt += " failure"
        else:
          html = BUILDING
          alt += " building"
        html = html % alt
        if build_result.url:
          html = """<a href="%s">%s</a>""" % (build_result.url, html)
        build_results += "&nbsp;" + html

      # construct patch link
      patch_html = ""
      if submission.last_branch_message_id:
        patch_html = """<a href="https://www.postgresql.org/message-id/%s">patch</a>""" % submission.last_branch_message_id

      # write out an entry
      f.write("""
      <tr>
        <td width="10%%">%s/%s</td>
        <td width="50%%"><a href="https://commitfest.postgresql.org/%s/%s/">%s</a></td>
        <td width="20%%">%s</td>
        <td width="5%%" align="right">%s</td>
        <td width="15%%">%s</td>
""" % (submission.commitfest_id, submission.id, submission.commitfest_id, submission.id, name, author_links_string, patch_html, build_results))
      f.write("      </tr>\n")
    f.write("""
    </table>

    <p>Please send feedback to thomas.munro-at-gmail.com.</p>
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
    commitfest_id = cfbot_commitfest_rpc.get_current_commitfest_id()
    rebuild(conn, commitfest_id)
