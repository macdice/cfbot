import HTMLParser
import re

from utils import *
from submission import *

class CommitFest:
  def __init__(self, commitfest_id):
    self.id = int(commitfest_id)

  def get_submissions(self):
    """Fetch a list of Submission objects."""
    result = []
    parser = HTMLParser.HTMLParser()
    url = "https://commitfest.postgresql.org/%s/" % (self.id,)
    next_line_has_authors = False
    state = None
    for line in slow_fetch(url).splitlines():
      groups = re.search('\<a href="([0-9]+)/"\>([^<]+)</a>', line)
      if groups:
        submission_id = groups.group(1)
        name = parser.unescape(groups.group(2))
      if next_line_has_authors:
        next_line_has_authors = False
        groups = re.search("<td>([^<]*)</td>", line)
        if groups:
          authors = groups.group(1)
          authors = re.sub(" *\\([^)]*\\)", "", authors)
          result.append(Submission(submission_id, self.id, name, state, authors))
          continue
      groups = re.search('<td><span class="label label-[^"]*">([^<]+)</span></td>', line)
      if groups:
        state = groups.group(1)
        next_line_has_authors = True
        continue
      next_line_has_authors = False
    return result

def get_current_commitfest():
  """Find the ID of the current open or next future commitfest."""
  result = None
  for line in slow_fetch("https://commitfest.postgresql.org").splitlines():
    groups = re.search('<a href="/([0-9]+)/">[0-9]+-[0-9]+</a> \((Open|In Progress) ', line)
    if groups:
      commitfest_id = groups.group(1)
      state = groups.group(2)
      result = int(commitfest_id)
  return CommitFest(result)
