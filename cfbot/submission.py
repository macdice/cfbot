"""A submission in a Commitfest."""

import urllib
import urlparse
import urllib2
import re
import os
import subprocess

from utils import *

class Submission:
  """A submission in a Commitfest."""

  def __init__(self, submission_id, commitfest_id, name, status, authors):
    self.id = int(submission_id)
    self.commitfest_id = commitfest_id
    self.name = name
    self.status = status
    self.authors = authors

  def get_thread_url_for_submission(self):
    """Given a commitfest ID, return the URL of the 'whole
      thread' page in the mailing list archives."""
    if self.id == 951:
      # this one has two threads, and the interesting one is listed first (need to learn about dates?)
      return "https://www.postgresql.org/message-id/flat/CAEepm=1iiEzCVLD=RoBgtZSyEY1CR-Et7fRc9prCZ9MuTz3pWg@mail.gmail.com"
    elif self.id == 994:
      # this one is truncated, and there is a new 'flat' URL for the continuation
      return "https://www.postgresql.org/message-id/flat/CAOGQiiN9m%3DKRf-et1T0AcimbyAB9hDzJqGkHnOBjWT4uF1z1BQ%40mail.gmail.com"
    # if there is more than one, we'll take the furthest down on the page...
    result = None
    url = "https://commitfest.postgresql.org/%s/%s/" % (self.commitfest_id, self.id)
    for line in slow_fetch(url).splitlines():
      groups = re.search('<dt><a href="(https://www.postgresql.org/message-id/flat/[^"]+)"', line)
      if groups:
        result = groups.group(1)
    return result

  def get_latest_patches_from_thread_url(self, thread_url):
    """Given a 'whole thread' URL from the archives, find the last message that
      had at least one attachment called something.patch.  Return the message
      ID and the list of URLs to fetch all the patches."""
    selected_message_attachments = []
    selected_message_id = None
    message_attachments = []
    message_id = None
    for line in slow_fetch(thread_url).splitlines():
      groups = re.search('<a href="(/message-id/attachment/[^"]*\\.(patch|patch\\.gz|tar\\.gz|tgz|tar\\.bz2))">', line)
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
      if any(x.endswith(".tgz") or x.endswith(".tar.gz") or x.endswith(".tar.bz2") for x in selected_message_attachments):
        if len(selected_message_attachments) > 1:
          selected_message_id = None
          selected_message_attachments = None
    # if there are multiple patch files, they had better follow the convention
    # of leading numbers, otherwise we don't know how to apply them in the right
    # order
    # TODO
    return selected_message_id, selected_message_attachments

  def check(self, log, commit_id):
    activity_message = ""
    
    log.write("==> considering submission ID %s\n" % self.id)
    log.flush()
    patch_dir = os.path.join("patches", str(self.commitfest_id), str(self.id))
    if os.path.isdir(patch_dir):
      # write name and status to disk so our web page builder can use them...
      write_file(os.path.join(patch_dir, "status"), self.status)
      write_file(os.path.join(patch_dir, "name"), self.name)
    thread_url = self.get_thread_url_for_submission()
    #if self.status not in ("Ready for Committer", "Needs review"):
    #  return
    if thread_url == None:
      return

    new_patch = False
    message_id, patches = self.get_latest_patches_from_thread_url(thread_url)
    if message_id:
      # download the patches, if we don't already have them
      message_id_path = os.path.join(patch_dir, "message_id")
      if not os.path.exists(message_id_path) or read_file(message_id_path) != message_id:
        new_patch = True # affects the friendly status message
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
        write_file(os.path.join(tmp, "status"), self.status)
        write_file(os.path.join(tmp, "name"), self.name)
        os.rename(tmp, patch_dir)

      # if the commit ID has moved since last time, or we
      # have a new patchest, then we need to make a new branch
      # to trigger a new build
      commit_id_path = os.path.join("patches", str(self.commitfest_id), str(self.id), "commit_id")
      if not os.path.exists(commit_id_path) or read_file(commit_id_path) != commit_id:
        log.write("    commit ID %s is new\n" % commit_id)
        log.flush()
        branch = "commitfest/%s/%s" % (self.commitfest_id, self.id)
        subprocess.check_call("cd postgresql && git checkout . > /dev/null && git clean -fd > /dev/null && git checkout -q master", shell=True)
        failed_to_apply = False
        with open(os.path.join("logs", str(self.commitfest_id), str(self.id) + ".log"), "w") as apply_log:
          apply_log.write("== Fetched patches from message ID %s\n" % message_id)
          apply_log.write("== Applying on top of commit %s\n" % commit_id)
          for path in sorted(os.listdir(patch_dir)):
            if path.endswith(".patch"):
              with open(os.path.join(patch_dir, path), "r") as f:
                apply_log.write("== Applying patch %s...\n" % path)
                apply_log.flush()
                popen = subprocess.Popen("cd postgresql && patch -p1 --no-backup-if-mismatch --batch --silent", shell=True, stdin=f, stdout=apply_log, stderr=apply_log)
                popen.wait()
                if popen.returncode != 0:
                  failed_to_apply = True
                  break
            elif path.endswith(".patch.gz"):
              with gzip.open(os.path.join(patch_dir, path), "r") as f:
                apply_log.write("== Applying patch %s...\n" % path)
                apply_log.flush()
                popen = subprocess.Popen("cd postgresql && patch -p1 --no-backup-if-mismatch --batch --silent", shell=True, stdin=subprocess.PIPE, stdout=apply_log, stderr=apply_log)
                popen.communicate(input=f.read())
                popen.wait()
                if popen.returncode != 0:
                  failed_to_apply = True
                  break
            elif path.endswith(".tgz") or path.endswith(".tar.gz") or path.endswith(".tar.bz2"):
              apply_log.write("== Applying patches from tarball %s...\n" % path)
              apply_log.flush()
              # TODO catch errors manipulating tar files...
              with tarfile.open(os.path.join(patch_dir, path), "r") as tarball:
                for name in sorted(tarball.getnames()):
                  if not name.endswith(".patch") or "/." in name:
                    continue
                  apply_log.write("== Applying patch %s...\n" % name)
                  apply_log.flush()
                  popen = subprocess.Popen("cd postgresql && patch -p1 --no-backup-if-mismatch --batch --silent", shell=True, stdin=subprocess.PIPE, stdout=apply_log, stderr=apply_log)
                  f = tarball.extractfile(name)
                  popen.communicate(input=f.read())
                  f.close()
                  popen.wait()
                  if popen.returncode != 0:
                    failed_to_apply = True
                    break
        apply_status_path = os.path.join("patches", str(self.commitfest_id), str(self.id), "apply_status")
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
          write_file("commit_message", """[CF %s/%s] %s

This commit was automatically generated by cfbot at commitfest.cputube.org.
It is based on patches submitted to the PostgreSQL mailing lists and
registered in the PostgreSQL Commitfest application.

This branch will be overwritten each time a new patch version is posted to
the email thread or the master branch changes.

Commitfest entry: https://commitfest.postgresql.org/%s/%s
Patch(es): https://www.postgresql.org/message-id/%s
Author(s): %s
""" % (self.commitfest_id, self.id, self.name, self.commitfest_id, self.id, message_id, self.authors))
          subprocess.check_call("cd postgresql && git commit -q -F ../commit_message", shell=True)
          write_file(commit_id_path, commit_id)
          if False: # disable pushing for my own testing purposes
            log.write("    pushing branch %s\n" % branch)
            log.flush()
            os.environ["GIT_SSH_COMMAND"] = CFBOT_REPO_SSH_COMMAND
            subprocess.check_call("cd postgresql && git push -q -f cfbot-repo %s" % (branch,), shell=True)
            if new_patch:
              activity_message = """Pushed branch <a href="https://github.com/postgresql-cfbot/postgresql/tree/%s">%s</a>, triggered by <a href="https://www.postgresql.org/message-id/%s">new patch</a>.""" % (branch, branch, message_id)
            else:
              activity_message = """Pushed branch <a href="https://github.com/postgresql-cfbot/postgresql/tree/%s">%s</a>, triggered by commit <a href="https://git.postgresql.org/gitweb/?p=postgresql.git;a=commitdiff;h=%s">%s</a>.  Waiting for a while to be polite before rebuilding items marked "&bull;"...""" % (branch, branch, commit_id, commit_id[:8])
    return activity_message

  def all_authors(self):
    results = []
    for author in self.authors.split(","):
      author = author.strip()
      if author != "":
        results.append(author)
    return results

  def sort_status_name(self):
    """An ordering function that puts statuses in order of most interest..."""
    if self.status == "Ready for Committer":
        return "0" + self.name.lower()
    elif self.status == "Needs review":
        return "1" + self.name.lower()
    else:
        return "2" + self.name.lower()
