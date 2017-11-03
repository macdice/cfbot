"""A submission in a Commitfest."""

import urllib2
import time
import re

# politeness settings
SLOW_FETCH_SLEEP = 1.0

USER_AGENT = "cfbot from http://commitfest.cputube.org"

class Submission:
  """A submission in a Commitfest."""

  def __init__(self, submission_id, commitfest_id, name, status, authors):
    self.id = int(submission_id)
    self.commitfest_id = commitfest_id
    self.name = name
    self.status = status
    self.authors = authors

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
