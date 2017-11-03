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
