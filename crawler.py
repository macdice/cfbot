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
#       xxx.patch
#       yyy.patch
#       zzz.patch
#       ...

import HTMLParser
import os
import re
import shutil
import time
import urllib
import urllib2
import urlparse

SLOW_FETCH_SLEEP = 0.0
PATCH_DIR = "patches"
USER_AGENT = "Personal Commitfest crawler of Thomas Munro <munro@ip9.org>"

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

def get_thread_url_for_submission(commitfest_id, submission_id):
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
  
def get_submissions_for_commitfest(commitfest_id):
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

def get_open_or_next_commitfest_id():
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

def sync_patches():
  commitfest_id = get_open_or_next_commitfest_id()
  if not os.path.isdir(os.path.join(PATCH_DIR, commitfest_id)):
    os.mkdir(os.path.join(PATCH_DIR, commitfest_id))
  if os.path.exists(os.path.join(PATCH_DIR, "current")):
    os.unlink(os.path.join(PATCH_DIR, "current"))
  os.symlink(commitfest_id, os.path.join(PATCH_DIR, "current"))
  submissions = get_submissions_for_commitfest(commitfest_id)
  for submission_id, name, status in submissions:
    thread_url = get_thread_url_for_submission(commitfest_id, submission_id)
    if thread_url == None:
      continue
    message_id, patches = get_latest_patches_from_thread_url(thread_url)
    if message_id:
      patch_dir = os.path.join(PATCH_DIR, commitfest_id, submission_id)
      if os.path.isdir(patch_dir):
        write_file(os.path.join(patch_dir, "status"), status)
        write_file(os.path.join(patch_dir, "name"), name)
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
  
if __name__ == "__main__":
  sync_patches()
