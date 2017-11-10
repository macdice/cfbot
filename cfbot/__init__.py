import gzip
import unicodedata

from prep import *
from submission import *
from commitfest import *

def sort_and_rotate_submissions(log, submissions):
  """Sort the given list of submissions, and then rotate them so that the one
     that follows 'last_submission_id' comes first (unless it is None).  This
     provides a simple way for us to carry on where we left off each time we
     run."""
  last_submission_id = load_last_submission_id(log)

  submissions = sorted(submissions, key=lambda s: s.id)
  if last_submission_id == None:
      return submissions
  done = [s for s in submissions if s.id <= last_submission_id]
  rest = [s for s in submissions if s.id > last_submission_id]
  return rest + done

LAST_SUBMISSION_ID_PATH = "last_submission_id"

def load_last_submission_id(log):
  """ what was the last submission ID we checked? """
  if os.path.exists(LAST_SUBMISSION_ID_PATH):
    last_submission_id = int(read_file(LAST_SUBMISSION_ID_PATH))
    log.write("last submission ID was %s\n" % last_submission_id)
    log.flush()
  else:
    last_submission_id = None

def write_last_submission_id(submission):
  """ update the last submission ID we checked? """
  write_file(LAST_SUBMISSION_ID_PATH, str(submission.id))

def check_n_submissions(log, commit_id, submissions, n):
  activity_message = "Idle."

  # now process n submissions, starting after that one
  for submission in sort_and_rotate_submissions(log, submissions):
    activity_message = submission.check(log, commit_id)
    n = n - 1
    # remember this ID so we can start after this next time
    write_last_submission_id(submission)

    if n <= 0:
      break
  return activity_message

def make_author_url(author):
    text = author.strip()
    text = unicode(text, "utf-8")
    text = unicodedata.normalize('NFD', text)
    text = text.encode('ascii', 'ignore')
    text = text.decode("utf-8")
    text = str(text).lower()
    text = re.sub('[ ]+', '-', text)
    text = re.sub('[^0-9a-zA-Z_-]', '', text)
    return text + ".html"
 
def build_web_page(commit_id, commitfest_id, submissions, filter_author, activity_message, path):
  """Build a web page that lists all known entries and shows the badges."""

  last_status = None
  submissions = sorted(submissions, key=lambda s: s.sort_status_name)
  commitfest_id_for_link = commitfest_id
  if commitfest_id_for_link == None:
    commitfest_id_for_link = ""
  with open(path + ".tmp", "w") as f:
    f.write("""
<html>
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
      <a href="https://commitfest.postgresql.org/%s">Commitfest</a>
      &rarr; 
      <a href="https://github.com/postgresql-cfbot/postgresql/branches">Github</a>
      &rarr;
      <a href="https://travis-ci.org/postgresql-cfbot/postgresql/branches">Travis CI</a>
      &rarr;
      <a href="https://codecov.io/gh/postgresql-cfbot/postgresql/commits">Codecov</a>.
      You can find a report for the <a href="index.html">current CF</a>, the
      <a href="next.html">next CF</a> or individual patch authors.
    </p>
    <p>Current status: %s</p>
    <table>
""" % (commitfest_id_for_link, activity_message))
    for submission in sorted(submissions, key=lambda s: s.sort_status_name):

      # skip if we need to filter by commitfest
      if commitfest_id != None and submission.commitfest_id != commitfest_id:
        continue

      # skip if we need to filter by author
      if filter_author != None and filter_author not in submission.all_authors():
        continue

      # load the info about this submission that was recorded last time
      # we actually rebuilt the branch
      submission_dir = os.path.join("patches", str(submission.commitfest_id), str(submission.id))
      apply_status_path = os.path.join(submission_dir, "apply_status")
      message_id_path = os.path.join(submission_dir, "message_id")
      commit_id_path = os.path.join(submission_dir, "commit_id")
      name_path = os.path.join(submission_dir, "name")
      status_path = os.path.join(submission_dir, "status")
      if not os.path.exists(apply_status_path) or not os.path.exists(message_id_path) or not os.path.exists(name_path) or not os.path.exists(status_path):
        continue
      apply_status = read_file(apply_status_path)
      message_id = read_file(message_id_path)
      name = submission.name #read_file(name_path)
      status = submission.status #read_file(status_path)

      # check if this submission is queued for rebuilding
      build_needed_indicator = False
      if apply_status == "passing" and (not os.path.exists(commit_id_path) or read_file(commit_id_path) != commit_id):
        build_needed_indicator = True

      # create a new heading row if this is a new CF status
      if last_status == None or last_status != status:
        f.write("""      <tr><td colspan="6"><h2>%s</h2></td></tr>\n""" % status)
        last_status = status

      # create an apply pass/fail badge
      commitfest_dir = os.path.join("www", str(submission.commitfest_id))
      if not os.path.exists(commitfest_dir):
        os.mkdir(commitfest_dir)
      # write an image file for each submission, so that the badge could be included on other websites
      if apply_status == "failing":
        write_file(os.path.join(commitfest_dir, "%s.apply.svg" % (submission.id,)), APPLY_FAILING_SVG)
      else:
        write_file(os.path.join(commitfest_dir, "%s.apply.svg" % (submission.id,)), APPLY_PASSING_SVG)
      write_file(os.path.join(commitfest_dir, "%s.log" % submission.id), read_file(os.path.join("logs", str(submission.commitfest_id), str(submission.id) + ".log")))
      if len(name) > 80:
        name = name[:80] + "..."
      # convert list of authors into links
      author_links = []
      for author in submission.all_authors():
        author_links.append("""<a href="%s">%s</a>""" % (make_author_url(author), author))
      author_links_string = ", ".join(author_links)
      # write out an entry
      f.write("""
      <tr>
        <td>%s/%s</td>
        <td><a href="https://commitfest.postgresql.org/%s/%s/">%s</a></td>
        <td>%s</td>
        <td><a href="https://www.postgresql.org/message-id/%s">patch</a></td>
""" % (submission.commitfest_id, submission.id, submission.commitfest_id, submission.id, name, author_links_string, message_id))
      if apply_status == "failing":
        f.write("""        <td><a href="%s/%s.log"><img src="apply-failing.svg"/></a></td>\n""" % (submission.commitfest_id, submission.id))
        f.write("""        <td></td>\n""")
      else:
        f.write("""        <td><a href="%s/%s.log"><img src="apply-passing.svg"/></a></td>\n""" % (submission.commitfest_id, submission.id))
        #f.write("""        <td><a href="https://github.com/postgresql-cfbot/postgresql/tree/commitfest/%s/%s"><img src="apply-passing.svg"/></a></td>\n""" % (commitfest_id, submission.id))
        f.write("""        <td><a href="https://travis-ci.org/postgresql-cfbot/postgresql/branches"><img src="https://travis-ci.org/postgresql-cfbot/postgresql.svg?branch=commitfest/%s/%s" alt="Build Status" /></a></td>\n""" % (submission.commitfest_id, submission.id))
        if build_needed_indicator:
          f.write("""        <td>&bull;</td>\n""")
        else:
          f.write("""        <td></td>\n""")
      f.write("      </tr>\n")
    f.write("""
    </table>

    <p>Please send feedback to thomas.munro-at-enterprisedb.com.</p>
  </body>
</html>
""")
  os.rename(path + ".tmp", path)

def unique_authors(submissions):
  results = []
  for submission in submissions:
    results += submission.all_authors()
  return list(set(results))
