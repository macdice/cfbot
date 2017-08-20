#!/usr/bin/env python
#
# Fetch the latest Commitfest patches.  The resulting tree of files looks like
# this:
#
# PATCH_DIR
#   14               = commitfest ID
#     1234           = commitfest submission ID
#       name         = commitfest submission name
#       status       = commitfest status ("Ready for Committer" etc)
#       message_id   = patch message ID
#       commit_id    = commit ID of last branch we created for this submission
#       xxx.patch
#       yyy.patch
#       zzz.patch
#       ...

import HTMLParser
import os
import re
import subprocess
import shutil
import time
import urllib
import urllib2
import urlparse

SLOW_FETCH_SLEEP = 0.0
PATCH_DIR = "patches"
USER_AGENT = "Personal Commitfest crawler of Thomas Munro <munro@ip9.org>"

APPLY_PASSING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="90" height="20"><linearGradient id="a" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><rect rx="3" width="90" height="20" fill="#555"/><rect rx="3" x="37" width="53" height="20" fill="#4c1"/><path fill="#4c1" d="M37 0h4v20h-4z"/><rect rx="3" width="90" height="20" fill="url(#a)"/><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="19.5" y="15" fill="#010101" fill-opacity=".3">apply</text><text x="19.5" y="14">apply</text><text x="62.5" y="15" fill="#010101" fill-opacity=".3">passing</text><text x="62.5" y="14">passing</text></g></svg>
"""

APPLY_FAILING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="81" height="20"><linearGradient id="a" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><rect rx="3" width="81" height="20" fill="#555"/><rect rx="3" x="37" width="44" height="20" fill="#e05d44"/><path fill="#e05d44" d="M37 0h4v20h-4z"/><rect rx="3" width="81" height="20" fill="url(#a)"/><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="19.5" y="15" fill="#010101" fill-opacity=".3">apply</text><text x="19.5" y="14">apply</text><text x="58" y="15" fill="#010101" fill-opacity=".3">failing</text><text x="58" y="14">failing</text></g></svg>
"""

TRAVIS_FILE = """
language: c
script: ./configure && make && make check && (cd src/test/isolation && make check)
"""

def slow_fetch(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
  print "fetching", url
  opener = urllib2.build_opener()
  opener.addheaders = [('User-Agent', USER_AGENT)]
  response = opener.open(url)
  body = response.read()
  response.close()
  time.sleep(SLOW_FETCH_SLEEP)
  return body
  
def get_latest_patches_from_thread_url(thread_url):
  """Given a 'whole thread' URL from the archives, find the last message that
     had at least one attachment called something.patch.  Return the message
     ID and the list of URLs to fetch all the patches."""
  selected_message_attachments = []
  selected_message_id = None
  message_attachments = []
  message_id = None
  for line in slow_fetch(thread_url).splitlines():
    groups = re.search('<a href="(/message-id/attachment/[^"]*.patch)">', line)
    if groups:
      message_attachments.append("https://www.postgresql.org" + groups.group(1))
      selected_message_attachments = message_attachments
      selected_message_id = message_id
    else:
      groups = re.search('<a name="([^"]+)"></a>', line)
      if groups:
        message_id = groups.group(1)
        message_attachments = []
  return selected_message_id, selected_message_attachments

def scrape_thread_url_for_submission(commitfest_id, submission_id):
  """Given a commitfest ID and a submission ID, return the URL of the 'whole
     thread' page in the mailing list archives."""
  result = None
  url = "https://commitfest.postgresql.org/%s/%s/" % (commitfest_id, submission_id)
  for line in slow_fetch(url).splitlines():
    groups = re.search('(https://www.postgresql.org/message-id/flat/[^"]+)', line)
    if groups:
      result = groups.group(1)
      break
  return result
  
def scrape_submissions_for_commitfest(commitfest_id):
  """Given a commitfest ID, return the list of submission ID, name."""
  result = []
  parser = HTMLParser.HTMLParser()
  url = "https://commitfest.postgresql.org/%s/" % (commitfest_id,)
  for line in slow_fetch(url).splitlines():
    groups = re.search('\<a href="([0-9]+)/"\>([^<]+)</a>', line)
    if groups:
      submission_id = groups.group(1)
      submission_name = parser.unescape(groups.group(2))
    groups = re.search('<td><span class="label label-[^"]*">([^<]+)</span></td>', line)
    if groups:
      submission_state = groups.group(1)
      result.append((submission_id, submission_name, submission_state))
  return result

def scrape_current_commitfest_id():
  """Find the ID of the current open or next future commitfest."""
  result = None
  for line in slow_fetch("https://commitfest.postgresql.org").splitlines():
    groups = re.search('<a href="/([0-9]+)/">[0-9]+-[0-9]+</a> \((Open|Future) ', line)
    if groups:
      commitfest_id = groups.group(1)
      state = groups.group(2)
      if state == "Open":
        result = commitfest_id
        break
      elif state == "Future":
        result = commitfest_id
  return result

def read_file(path):
  with open(path) as f:
    return f.read()

def write_file(path, data):
  with open(path, "w+") as f:
    f.write(data)

def sort_and_rotate_submissions(submissions, last_submission_id):
    submissions = sorted(submissions, key=lambda tup: tup[0])
    if last_submission_id == None:
        return submissions
    done = [tup for tup in submissions if int(tup[0]) <= int(last_submission_id)]
    rest = [tup for tup in submissions if int(tup[0]) > int(last_submission_id)]
    return rest + done

def check_patches(commitfest_id, commit_id, n):
  # make sure the commitfest directory and current symlink exist
  if not os.path.isdir(os.path.join(PATCH_DIR, commitfest_id)):
    os.mkdir(os.path.join(PATCH_DIR, commitfest_id))
  if os.path.exists(os.path.join(PATCH_DIR, "current")):
    os.unlink(os.path.join(PATCH_DIR, "current"))
  os.symlink(commitfest_id, os.path.join(PATCH_DIR, "current"))

  # process patches in order, starting after the last one we looked at
  last_submission_id_path = os.path.join(PATCH_DIR, commitfest_id, "last_submission_id")
  if os.path.exists(last_submission_id_path):
    last_submission_id = read_file(last_submission_id_path)
  else:
    last_submission_id = None
  submissions = scrape_submissions_for_commitfest(commitfest_id)
  submissions = sort_and_rotate_submissions(submissions, last_submission_id)

  # now process n submissions
  for submission_id, name, status in submissions:
    patch_dir = os.path.join(PATCH_DIR, commitfest_id, submission_id)
    if os.path.isdir(patch_dir):
      write_file(os.path.join(patch_dir, "status"), status)
      write_file(os.path.join(patch_dir, "name"), name)
    thread_url = scrape_thread_url_for_submission(commitfest_id, submission_id)
    if status not in ("Ready for Committer", "Needs review"):
      continue
    if thread_url == None:
      continue

    message_id, patches = get_latest_patches_from_thread_url(thread_url)
    if message_id:
      # download the patches, if we don't already have them
      message_id_path = os.path.join(patch_dir, "message_id")
      if not os.path.exists(message_id_path) or read_file(message_id_path) != message_id:
        tmp = patch_dir + ".tmp"
        if os.path.exists(tmp):
          shutil.rmtree(tmp)
        if os.path.exists(patch_dir):
          shutil.rmtree(patch_dir)
        os.mkdir(tmp)
        for patch in patches:
          parsed = urlparse.urlparse(patch)
          filename = os.path.basename(parsed.path)
          dest = os.path.join(tmp, filename)
          print "fetching patch", patch
          urllib.urlretrieve(patch, dest)
          time.sleep(1)
        write_file(os.path.join(tmp, "message_id"), message_id)
        write_file(os.path.join(tmp, "status"), status)
        write_file(os.path.join(tmp, "name"), name)
        os.rename(tmp, patch_dir)

      # if the commit ID has moved since last time, or we
      # have a new patchest, then we need to make a new branch
      # to trigger a new build
      commit_id_path = os.path.join(PATCH_DIR, commitfest_id, submission_id, "commit_id")
      if not os.path.exists(commit_id_path) or read_file(commit_id_path) != commit_id:
        branch = "commifest/%s/%s" % (commitfest_id, submission_id)
        subprocess.check_call("cd postgresql && git checkout . > /dev/null && git clean -fd > /dev/null && git checkout -q master", shell=True)
        subprocess.call("cd postgresql && git branch -q -D %s" % (branch,), shell=True) # ignore if fail
        subprocess.check_call("cd postgresql && git checkout -b %s" % (branch,), shell=True)
        failed_to_apply = False
        if not os.path.exists(os.path.join("logs", commitfest_id)):
          os.mkdir(os.path.join("logs", commitfest_id))
        with open(os.path.join("logs", commitfest_id, submission_id + ".log"), "w") as log:
          log.write("== Applying patchset on commit %s\n" % commit_id)
          for path in sorted(os.listdir(patch_dir)):
            if path.endswith(".patch"):
              print path
              with open(os.path.join(patch_dir, path), "r") as f:
                log.write("== Applying patch %s...\n" % path)
                log.flush()
                popen = subprocess.Popen("cd postgresql && patch -p1 --batch --silent", shell=True, stdin=f, stdout=log, stderr=log)
                popen.wait()
                if popen.returncode != 0:
                  failed_to_apply = True
                  break
        apply_status_path = os.path.join(PATCH_DIR, commitfest_id, submission_id, "apply_status")
        if failed_to_apply:
          write_file(apply_status_path, "failing")
        else:
          write_file(apply_status_path, "passing")
          write_file("postgresql/.travis.yml", TRAVIS_FILE)
          subprocess.check_call("cd postgresql && git add -A", shell=True)
          write_file("commit_message", """Automatic commit for Commitfest submission #%s.

This commit was automatically generated and includes a Travis control file
to tell travis-ci.org what to do with it.  This branch will be overwritten
each time a new patch version is posted or master changes.

Commitfest entry: https://commitfest.postgresql.org/%s/%s
Patches fetched from: https://www.postgresql.org/message-id/%s
""" % (submission_id, commitfest_id, submission_id, message_id))
          subprocess.check_call("cd postgresql && git commit -q -F ../commit_message", shell=True)
          write_file(commit_id_path, commit_id)
          os.environ["GIT_SSH_COMMAND"] = 'ssh -i ~/.ssh/cfbot_github_rsa'
          subprocess.check_call("cd postgresql && git push -q -f github %s" % (branch,), shell=True)
          n = n - 1

      # remember this ID so we can start after this next time
      write_file(last_submission_id_path, submission_id)

      if n <= 0:
        break

def sort_status_name(tup):
  if tup[4] == "Ready for Committer":
    return "0" + tup[3].lower()
  else:
    return "1" + tup[3].lower()

def write_web_page(commitfest_id):
  submissions = []
  for submission_id in os.listdir(os.path.join(PATCH_DIR, commitfest_id)):
    if submission_id in (".", ".."):
      continue
    submission_dir = os.path.join(PATCH_DIR, commitfest_id, submission_id)
    apply_status_path = os.path.join(submission_dir, "apply_status")
    message_id_path = os.path.join(submission_dir, "message_id")
    name_path = os.path.join(submission_dir, "name")
    status_path = os.path.join(submission_dir, "status")
    if os.path.exists(apply_status_path) and os.path.exists(message_id_path) and os.path.exists(name_path) and os.path.exists(status_path):
      apply_status = read_file(apply_status_path)
      message_id = read_file(message_id_path)
      name = read_file(name_path)
      status = read_file(status_path)
      if status in ("Ready for Committer", "Needs review"):
        submissions.append((submission_id, apply_status, message_id, name, status))
  submissions = sorted(submissions, key=sort_status_name)
  with open("www/index.html.tmp", "w") as f:
    f.write("""
<html>
<head><title>Unofficial PostgreSQL Commitfest CI</title></head>
<body>
<h1>Unofficial Experimental PostgreSQL Commitfest CI</title></h1>
<table>
""")
    for submission_id, apply_status, message_id, name, status in submissions:
      # create an apply pass/fail badge
      commitfest_dir = os.path.join("www", commitfest_id)
      if not os.path.exists(commitfest_dir):
        os.mkdir(commitfest_dir)
      if apply_status == "failing":
        write_file(os.path.join(commitfest_dir, "%s.apply.svg" % (submission_id,)), APPLY_FAILING_SVG)
      else:
        write_file(os.path.join(commitfest_dir, "%s.apply.svg" % (submission_id,)), APPLY_PASSING_SVG)
      write_file(os.path.join(commitfest_dir, "%s.log" % submission_id), read_file(os.path.join("logs", commitfest_id, submission_id + ".log")))
      f.write("""
<tr>
  <td>%s.  <a href="https://commitfest.postgresql.org/%s/%s/">%s</a></td>
  <td>[%s]</td>
  <td><a href="https://www.postgresql.org/message-id/%s">patch set</a></td>
""" % (submission_id, commitfest_id, submission_id, name, status, message_id))
      if apply_status == "failing":
        f.write("""<td><a href="%s/%s.log"><img src="%s/%s.apply.svg"/></a></td>\n""" % (commitfest_id, submission_id, commitfest_id, submission_id))
      else:
          f.write("""<td><a href="https://travis-ci.org/postgresql-cfbot/postgresql/branches"><img src="https://travis-ci.org/postgresql-cfbot/postgresql.svg?branch=commitfest/%s/%s" alt="Build Status" /></a></td>\n""" % (commitfest_id, submission_id))
      f.write("</tr>\n")
    f.write("""
</table>
</body>
</html>
""")
  os.rename("www/index.html.tmp", "www/index.html")

if __name__ == "__main__":
  subprocess.call("cd postgresql && git checkout . > /dev/null && git clean -fd > /dev/null && git checkout -q master && git pull -q", shell=True)
  commit_id = subprocess.check_output("cd postgresql && git show | head -1 | cut -d' ' -f2", shell=True).strip()
  commitfest_id = scrape_current_commitfest_id()
  check_patches(commitfest_id, commit_id, 1)
  write_web_page(commitfest_id)
