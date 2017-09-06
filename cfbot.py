#!/usr/bin/env python

import datetime
import errno
import fcntl
import HTMLParser
import os
import re
import subprocess
import shutil
import sys
import tarfile
import time
import urllib
import urllib2
import urlparse

# politeness settings
SLOW_FETCH_SLEEP = 1.0
USER_AGENT = "cfbot from http://commitfest.cputube.org"

# where to pull PostgreSQL master branch from
PG_REPO="git://git.postgresql.org/git/postgresql.git"

# where to push automatically generated branches (if enabled)
CFBOT_REPO="git@github.com:postgresql-cfbot/postgresql.git"
CFBOT_REPO_SSH_COMMAND="ssh -i ~/.ssh/cfbot_github_rsa"

# travis build settings that will be added to automatically generated branches
TRAVIS_FILE = """
language: c
cache: ccache
before_install:
 - "sudo apt-get update"
 - "sudo apt-get install libipc-run-perl libperl-dev libpython-dev tcl-dev libldap2-dev libicu-dev"
 - "sudo apt-get install docbook docbook-dsssl docbook-xsl libxml2-utils openjade1.3 opensp xsltproc"
script: ./configure --enable-tap-tests --with-tcl --with-python --with-perl --with-ldap --with-icu && make && make check-world && (cd doc && make)
"""

# images used for "apply" badges
APPLY_PASSING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="90" height="20"><linearGradient id="a" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><rect rx="3" width="90" height="20" fill="#555"/><rect rx="3" x="37" width="53" height="20" fill="#4c1"/><path fill="#4c1" d="M37 0h4v20h-4z"/><rect rx="3" width="90" height="20" fill="url(#a)"/><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="19.5" y="15" fill="#010101" fill-opacity=".3">apply</text><text x="19.5" y="14">apply</text><text x="62.5" y="15" fill="#010101" fill-opacity=".3">passing</text><text x="62.5" y="14">passing</text></g></svg>
"""
APPLY_FAILING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="81" height="20"><linearGradient id="a" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><rect rx="3" width="81" height="20" fill="#555"/><rect rx="3" x="37" width="44" height="20" fill="#e05d44"/><path fill="#e05d44" d="M37 0h4v20h-4z"/><rect rx="3" width="81" height="20" fill="url(#a)"/><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="19.5" y="15" fill="#010101" fill-opacity=".3">apply</text><text x="19.5" y="14">apply</text><text x="58" y="15" fill="#010101" fill-opacity=".3">failing</text><text x="58" y="14">failing</text></g></svg>
"""

class Submission:
  """A submission in a Commitfest."""

  def __init__(self, submission_id, name, status):
    self.id = int(submission_id)
    self.name = name
    self.status = status
    self.message_id = None
    self.apply_status = None

def slow_fetch(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
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
    groups = re.search('<a href="(/message-id/attachment/[^"]*\\.(patch|tar\\.gz|tgz))">', line)
    if groups:
      message_attachments.append("https://www.postgresql.org" + groups.group(1))
      selected_message_attachments = message_attachments
      selected_message_id = message_id
    else:
      groups = re.search('<a name="([^"]+)"></a>', line)
      if groups:
        message_id = groups.group(1)
        message_attachments = []
  # if there is a tarball attachment, there must be only one attachment,
  # otherwise give up on this thread (we don't know how to combine patches and
  # tarballs)
  if selected_message_attachments != None:
    if any(x.endswith(".tgz") or x.endswith(".tar.gz") for x in selected_message_attachments):
      if len(selected_message_attachments) > 1:
        selected_message_id = None
        selected_message_attachments = None
  # if there are multiple patch files, they had better follow the convention
  # of leading numbers, otherwise we don't know how to apply them in the right
  # order
  # TODO
  return selected_message_id, selected_message_attachments

def get_thread_url_for_submission(commitfest_id, submission_id):
  """Given a commitfest ID and a submission ID, return the URL of the 'whole
     thread' page in the mailing list archives."""
  # TODO: if there is more than one, how to choose?
  result = None
  url = "https://commitfest.postgresql.org/%s/%s/" % (commitfest_id, submission_id)
  for line in slow_fetch(url).splitlines():
    groups = re.search('(https://www.postgresql.org/message-id/flat/[^"]+)', line)
    if groups:
      result = groups.group(1)
      break
  return result
  
def get_submissions_for_commitfest(commitfest_id):
  """Given a commitfest ID, return a list of Submission objects."""
  result = []
  parser = HTMLParser.HTMLParser()
  url = "https://commitfest.postgresql.org/%s/" % (commitfest_id,)
  for line in slow_fetch(url).splitlines():
    groups = re.search('\<a href="([0-9]+)/"\>([^<]+)</a>', line)
    if groups:
      submission_id = groups.group(1)
      name = parser.unescape(groups.group(2))
    groups = re.search('<td><span class="label label-[^"]*">([^<]+)</span></td>', line)
    if groups:
      state = groups.group(1)
      result.append(Submission(submission_id, name, state))
  return result

def get_current_commitfest_id():
  """Find the ID of the current open or next future commitfest."""
  result = None
  for line in slow_fetch("https://commitfest.postgresql.org").splitlines():
    groups = re.search('<a href="/([0-9]+)/">[0-9]+-[0-9]+</a> \((Open|In Progress) ', line)
    if groups:
      commitfest_id = groups.group(1)
      state = groups.group(2)
      result = commitfest_id
  return result

def read_file(path):
  """Return the contents of file 'path'."""
  with open(path) as f:
    return f.read()

def write_file(path, data):
  """Write 'data' into 'path' atomically."""
  with open(path + ".tmp", "w+") as f:
    f.write(data)
  os.rename(path + ".tmp", path)

def sort_and_rotate_submissions(submissions, last_submission_id):
  """Sort the given list of submissions, and then rotate them so that the one
     that follows 'last_submission_id' comes first (unless it is None).  This
     provides a simple way for us to carry on where we left off each time we
     run."""
  submissions = sorted(submissions, key=lambda s: s.id)
  if last_submission_id == None:
      return submissions
  done = [s for s in submissions if s.id <= last_submission_id]
  rest = [s for s in submissions if s.id > last_submission_id]
  return rest + done

def check_n_submissions(log, commit_id, commitfest_id, submissions, n):

  # what was the last submission ID we checked?
  last_submission_id_path = os.path.join("patches", commitfest_id, "last_submission_id")
  if os.path.exists(last_submission_id_path):
    last_submission_id = int(read_file(last_submission_id_path))
    log.write("last submission ID was %s\n" % last_submission_id)
    log.flush()
  else:
    last_submission_id = None

  # now process n submissions, starting after that one
  for submission in sort_and_rotate_submissions(submissions, last_submission_id):
    log.write("==> considering submission ID %s\n" % submission.id)
    log.flush()
    patch_dir = os.path.join("patches", commitfest_id, str(submission.id))
    if os.path.isdir(patch_dir):
      # write name and status to disk so our web page builder can use them...
      write_file(os.path.join(patch_dir, "status"), submission.status)
      write_file(os.path.join(patch_dir, "name"), submission.name)
    thread_url = get_thread_url_for_submission(commitfest_id, submission.id)
    #if submission.status not in ("Ready for Committer", "Needs review"):
    #  continue
    if thread_url == None:
      continue

    message_id, patches = get_latest_patches_from_thread_url(thread_url)
    if message_id:
      # download the patches, if we don't already have them
      message_id_path = os.path.join(patch_dir, "message_id")
      if not os.path.exists(message_id_path) or read_file(message_id_path) != message_id:
        log.write("    message ID %s is new\n" % message_id)
        log.flush()
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
          log.write("    fetching patch %s\n" % patch)
          log.flush()
          urllib.urlretrieve(patch, dest)
          time.sleep(SLOW_FETCH_SLEEP)
        write_file(os.path.join(tmp, "message_id"), message_id)
        write_file(os.path.join(tmp, "status"), submission.status)
        write_file(os.path.join(tmp, "name"), submission.name)
        os.rename(tmp, patch_dir)

      # if the commit ID has moved since last time, or we
      # have a new patchest, then we need to make a new branch
      # to trigger a new build
      commit_id_path = os.path.join("patches", commitfest_id, str(submission.id), "commit_id")
      if not os.path.exists(commit_id_path) or read_file(commit_id_path) != commit_id:
        log.write("    commit ID %s is new\n" % commit_id)
        log.flush()
        branch = "commitfest/%s/%s" % (commitfest_id, submission.id)
        subprocess.check_call("cd postgresql && git checkout . > /dev/null && git clean -fd > /dev/null && git checkout -q master", shell=True)
        failed_to_apply = False
        with open(os.path.join("logs", commitfest_id, str(submission.id) + ".log"), "w") as apply_log:
          apply_log.write("== Fetched patches from message ID %s\n" % message_id)
          apply_log.write("== Applying on top of commit %s\n" % commit_id)
          for path in sorted(os.listdir(patch_dir)):
            if path.endswith(".patch"):
              with open(os.path.join(patch_dir, path), "r") as f:
                apply_log.write("== Applying patch %s...\n" % path)
                apply_log.flush()
                popen = subprocess.Popen("cd postgresql && patch -p1 --batch --silent", shell=True, stdin=f, stdout=apply_log, stderr=apply_log)
                popen.wait()
                if popen.returncode != 0:
                  failed_to_apply = True
                  break
            elif path.endswith(".tgz") or path.endswith(".tar.gz"):
              apply_log.write("== Applying patches from tarball %s...\n" % path)
              apply_log.flush()
              # TODO catch errors manipulating tar files...
              with tarfile.open(os.path.join(patch_dir, path), "r") as tarball:
                for name in sorted(tarball.getnames()):
                  if not name.endswith(".patch"):
                    continue
                  apply_log.write("== Applying patch %s...\n" % name)
                  apply_log.flush()
                  popen = subprocess.Popen("cd postgresql && patch -p1 --batch --silent", shell=True, stdin=subprocess.PIPE, stdout=apply_log, stderr=apply_log)
                  f = tarball.extractfile(name)
                  popen.communicate(input=f.read())
                  f.close()
                  popen.wait()
                  if popen.returncode != 0:
                    failed_to_apply = True
                    break
        apply_status_path = os.path.join("patches", commitfest_id, str(submission.id), "apply_status")
        if failed_to_apply:
          log.write("    apply failed (see apply log for details)\n")
          log.flush()
          write_file(apply_status_path, "failing")
          # no point in trying again until either the message ID or the commit ID moves
          write_file(commit_id_path, commit_id)
        else:
          write_file(apply_status_path, "passing")
          write_file("postgresql/.travis.yml", TRAVIS_FILE)
          subprocess.call("cd postgresql && git branch -q -D %s > /dev/null 2> /dev/null" % (branch,), shell=True) # ignore if fail
          subprocess.check_call("cd postgresql && git checkout -q -b %s" % (branch,), shell=True)
          subprocess.check_call("cd postgresql && git add -A", shell=True)
          log.write("    creating new branch %s\n" % branch)
          log.flush()
          write_file("commit_message", """Automatic commit for Commitfest submission #%s.

This commit was automatically generated and includes a Travis control file
to tell travis-ci.org what to do with it.  This branch will be overwritten
each time a new patch version is posted or master changes.

Commitfest entry: https://commitfest.postgresql.org/%s/%s
Patches fetched from: https://www.postgresql.org/message-id/%s
""" % (submission.id, commitfest_id, submission.id, message_id))
          subprocess.check_call("cd postgresql && git commit -q -F ../commit_message", shell=True)
          write_file(commit_id_path, commit_id)
          if n > 0:
            log.write("    pushing branch %s\n" % branch)
            log.flush()
            os.environ["GIT_SSH_COMMAND"] = CFBOT_REPO_SSH_COMMAND
            subprocess.check_call("cd postgresql && git push -q -f cfbot-repo %s" % (branch,), shell=True)
          n = n - 1

      # remember this ID so we can start after this next time
      write_file(last_submission_id_path, str(submission.id))

      if n <= 0:
        break

def sort_status_name(submission):
  """An ordering function that puts "Ready for Committer" first."""
  if submission.status == "Ready for Committer":
    return "0" + submission.name.lower()
  else:
    return "1" + submission.name.lower()

def build_web_page(commitfest_id, submissions):
  """Build a web page that lists all known entries and shows the badges."""

  submissions = sorted(submissions, key=sort_status_name)
  with open("www/index.html.tmp", "w") as f:
    f.write("""
<html>
<head><title>Skunkworks PostgreSQL Patch Testing Bot</title></head>
<body>
<h1>Skunkworks PostgreSQL Patch Testing Bot</h1>
<p>
This is an experiment to see if we can use modern continuous integration tools
while keeping our existing mailing-list based workflow.  It slurps patches out
of the current <a href="https://commitfest.postgresql.org/%s">PostgreSQL
Commitfest</a> and pushes them into
<a href="https://github.com/postgresql-cfbot/postgresql/branches">Github</a>
along with instructions to trigger builds on the
<a href="https://travis-ci.org/postgresql-cfbot/postgresql/branches">Travis
CI</a> build farm, and collates results here.  What could possibly go wrong?
(Thanks to those two companies who provide great free infrastructure to open
source projects!)
</p>

<p>Current problems: Results sometimes lag by quite a few hours (can only
build a couple at a time).  Currently can't see regression.diffs.  Can't
locate some patches because archives website cuts off long thread.  Confused
by CF entries with multiple associated threads.  Please send feedback to
thomas.munro-at-enterprisedb.com.</p>

<table>
""" % (commitfest_id,))
    for submission in sorted(submissions, key=sort_status_name):
      # load the info about this submission that was recorded last time
      # we actually rebuilt the branch
      # TODO:that means the sorting is wrong for recently changed names and statuses...
      submission_dir = os.path.join("patches", commitfest_id, str(submission.id))
      apply_status_path = os.path.join(submission_dir, "apply_status")
      message_id_path = os.path.join(submission_dir, "message_id")
      name_path = os.path.join(submission_dir, "name")
      status_path = os.path.join(submission_dir, "status")
      if not os.path.exists(apply_status_path) or not os.path.exists(message_id_path) or not os.path.exists(name_path) or not os.path.exists(status_path):
        continue
      apply_status = read_file(apply_status_path)
      message_id = read_file(message_id_path)
      name = read_file(name_path)
      status = read_file(status_path)

      # create an apply pass/fail badge
      commitfest_dir = os.path.join("www", commitfest_id)
      if not os.path.exists(commitfest_dir):
        os.mkdir(commitfest_dir)
      # write an image file for each submission, so that the badge could be included on other websites
      if apply_status == "failing":
        write_file(os.path.join(commitfest_dir, "%s.apply.svg" % (submission.id,)), APPLY_FAILING_SVG)
      else:
        write_file(os.path.join(commitfest_dir, "%s.apply.svg" % (submission.id,)), APPLY_PASSING_SVG)
      write_file(os.path.join(commitfest_dir, "%s.log" % submission.id), read_file(os.path.join("logs", commitfest_id, str(submission.id) + ".log")))
      if len(name) > 80:
        name = name[:80] + "..."
      f.write("""
<tr>
  <td>[%s]</td>
  <td>#%s</td>
  <td><a href="https://commitfest.postgresql.org/%s/%s/">%s</a></td>
  <td><a href="https://www.postgresql.org/message-id/%s">patch set</a></td>
""" % (status, submission.id, commitfest_id, submission.id, name, message_id))
      #f.write("""<td><a href="%s/%s.log"><img src="%s/%s.apply.svg"/></a></td>\n""" % (commitfest_id, submission.id, commitfest_id, submission.id))
      f.write("""<td><a href="%s/%s.log"><img src="apply-%s.svg"/></a></td>\n""" % (commitfest_id, submission.id, apply_status))
      if apply_status == "failing":
        f.write("""<td></td>\n""")
      else:
        f.write("""<td><a href="https://travis-ci.org/postgresql-cfbot/postgresql/branches"><img src="https://travis-ci.org/postgresql-cfbot/postgresql.svg?branch=commitfest/%s/%s" alt="Build Status" /></a></td>\n""" % (commitfest_id, submission.id))
      f.write("</tr>\n")
    f.write("""
</table>
</body>
</html>
""")
  os.rename("www/index.html.tmp", "www/index.html")

def prepare_repo():
  # set up a repo if we don't already have one
  if not os.path.exists("postgresql"):
    subprocess.check_call("rm -fr postgresql.tmp", shell=True)
    subprocess.check_call("git clone %s postgresql.tmp" % (PG_REPO,), shell=True)
    subprocess.check_call("cd postgresql.tmp && git remote add cfbot-repo %s" % (CFBOT_REPO,), shell=True)
    subprocess.check_call("mv postgresql.tmp postgresql", shell=True)

def prepare_filesystem(commitfest_id):
  """Create necessary directories and check out PostgreSQL source tree, if
     they aren't already present."""
  # set up the other directories we need
  if not os.path.exists("www"):
    os.mkdir("www.tmp")
    write_file(os.path.join("www.tmp", "apply-failing.svg"), APPLY_FAILING_SVG)
    write_file(os.path.join("www.tmp", "apply-passing.svg"), APPLY_PASSING_SVG)
    os.rename("www.tmp", "www")
  if not os.path.exists("logs"):
    os.mkdir("logs")
  if not os.path.exists(os.path.join("logs", commitfest_id)):
    os.mkdir(os.path.join("logs", commitfest_id))
  if not os.path.exists("patches"):
    os.mkdir("patches")
  if not os.path.isdir(os.path.join("patches", commitfest_id)):
    os.mkdir(os.path.join("patches", commitfest_id))

def update_tree():
  """Pull changes from PostgreSQL master and return the HEAD commit ID."""
  subprocess.call("cd postgresql && git checkout . > /dev/null && git clean -fd > /dev/null && git checkout -q master && git pull -q", shell=True)
  commit_id = subprocess.check_output("cd postgresql && git show | head -1 | cut -d' ' -f2", shell=True).strip()
  return commit_id

def try_lock():
  """Make sure that only one copy runs."""
  fd = open("lock-file", "w")
  try:
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd
  except IOError as e:
    if e.errno != errno.EAGAIN:
      raise
    else:
      return None

def run(num_branches_to_push):
  lock = try_lock()
  if not lock:
    # another copy is already running in this directory, so exit quietly (for
    # example if a cronjob starts before the last one has finished)
    return
  prepare_repo()
  commit_id = update_tree()
  commitfest_id = get_current_commitfest_id()
  prepare_filesystem(commitfest_id)
  submissions = get_submissions_for_commitfest(commitfest_id)
  submissions = filter(lambda s: s.status in ("Ready for Committer", "Needs review"), submissions)
  with open("logs/cfbot.%s.log" % datetime.date.today().isoformat(), "a") as log:
    log.write("== starting at %s\n" % str(datetime.datetime.now()))
    log.write("commitfest = %s\n" % commitfest_id)
    log.write("commit = %s\n" % commit_id)
    log.flush()
    check_n_submissions(log, commit_id, commitfest_id, submissions, num_branches_to_push)
    log.write("== finishing at %s\n" % str(datetime.datetime.now()))
    log.flush()
  build_web_page(commitfest_id, submissions)
  lock.close()

if __name__ == "__main__":
  num_branches_to_push = 0
  if len(sys.argv) > 1:
    num_branches_to_push = int(sys.argv[1])
  run(num_branches_to_push)
