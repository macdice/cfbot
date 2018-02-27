""" Utility functions used to prepare the filesystem, check out Postgres, etc. """
import fcntl
import os
import subprocess
import shutil
import sys
import tarfile
import errno

# where to pull PostgreSQL master branch from
PG_REPO="git://git.postgresql.org/git/postgresql.git"

# where to push automatically generated branches (if enabled)
CFBOT_REPO="git@github.com:postgresql-cfbot/postgresql.git"
CFBOT_REPO_SSH_COMMAND="ssh -i ~/.ssh/cfbot_github_rsa"

# images used for "apply" badges
APPLY_PASSING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="90" height="20"><linearGradient id="a" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><rect rx="3" width="90" height="20" fill="#555"/><rect rx="3" x="37" width="53" height="20" fill="#4c1"/><path fill="#4c1" d="M37 0h4v20h-4z"/><rect rx="3" width="90" height="20" fill="url(#a)"/><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="19.5" y="15" fill="#010101" fill-opacity=".3">apply</text><text x="19.5" y="14">apply</text><text x="62.5" y="15" fill="#010101" fill-opacity=".3">passing</text><text x="62.5" y="14">passing</text></g></svg>
"""
APPLY_FAILING_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="81" height="20"><linearGradient id="a" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient><rect rx="3" width="81" height="20" fill="#555"/><rect rx="3" x="37" width="44" height="20" fill="#e05d44"/><path fill="#e05d44" d="M37 0h4v20h-4z"/><rect rx="3" width="81" height="20" fill="url(#a)"/><g fill="#fff" text-anchor="middle" font-family="DejaVu Sans,Verdana,Geneva,sans-serif" font-size="11"><text x="19.5" y="15" fill="#010101" fill-opacity=".3">apply</text><text x="19.5" y="14">apply</text><text x="58" y="15" fill="#010101" fill-opacity=".3">failing</text><text x="58" y="14">failing</text></g></svg>
"""

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
  if not os.path.exists(os.path.join("logs", str(commitfest_id))):
    os.mkdir(os.path.join("logs", str(commitfest_id)))
  if not os.path.exists("patches"):
    os.mkdir("patches")
  if not os.path.isdir(os.path.join("patches", str(commitfest_id))):
    os.mkdir(os.path.join("patches", str(commitfest_id)))

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
