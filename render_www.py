#!/usr/bin/env python

import re

commitfest_id = 14
new_page = """<html>
<head><title>cfbot</title></head>
<body>
<h1>Commitfest steampunk robot</h1>

<p>
This page is regenerated periodically by analysing the current PostgreSQL Commitfest.
Think of it as a steampunk version of continuous integration for pull requests.
Only results for submissions in "Ready for Committer" and "Needs review" state
that failed to apply or build are shown, because those should be fixed.
</p>

<p>
Disclaimers: Not affiliated with the Commitfest app.  For amusement only.  May
contain nuts.  Currently confused about multi-thread Commitfest entries and patches
hiding in tarballs.
</p>

"""

def get_commit_id():
  with open("logs/latest/fail.log", "r") as f:
    for line in f.readlines():
      line = line.strip()
      groups = re.match("^.* commit (.+):$", line)
      if groups:
        return groups.group(1)
  raise Exception("Can't find commit ID")

def read_file(path):
  with open(path, "r") as f:
    return f.read()

def get_results():
  results = []
  with open("logs/latest/fail.log", "r") as f:
    for line in f.readlines():
      line = line.strip()
      groups = re.match("^([^:]+): #([0-9]+), \\[([^\\]]+)\\], message (.+)$", line)
      if groups:
        build_status = groups.group(1)
        submission_id = groups.group(2)
        cf_status = groups.group(3)
        message_id = groups.group(4)
        name = read_file("patches/%s/%s/name" % (commitfest_id, submission_id))
        results.append((submission_id, name, cf_status, build_status, message_id))
  return results

commit_id = get_commit_id()
results = get_results()

def make_per_status_table(heading):
  result = ""
  for submission_id, name, cf_status, build_status, message_id in results:
    if cf_status != heading:
      continue
    if build_status == "Auto-build not trusted":
      continue
    result += "<tr>\n"
    result += """<td><a href="https://commitfest.postgresql.org/%s/%s">%s</a></td>\n""" % (commitfest_id, submission_id, name)
    result += """<td><a href="https://www.postgresql.org/message-id/%s">patch</a></td>\n""" % (message_id, )
    result += """<td>[%s]</td>\n""" % (cf_status, )
    result += """<td><a href="%s.log">%s</a></td>\n""" % (submission_id, build_status)
    result += "</tr>\n"
  return result

new_page += """<p>The following results are based on commit <a href="https://git.postgresql.org/gitweb/?p=postgresql.git;a=commitdiff;h=%s">%s</a>.</p>\n\n""" % (commit_id, commit_id)

new_page += "<table>\n"
new_page += make_per_status_table("Ready for Committer")
new_page += make_per_status_table("Needs review")
new_page += "</table>\n"

new_page += "</body>\n</html>\n"

print new_page

