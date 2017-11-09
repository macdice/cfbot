""" Just a few utility functions used throughout the project."""
import urllib2
import time
import os

# politeness settings
SLOW_FETCH_SLEEP = 1.0

USER_AGENT = "cfbot from http://commitfest.cputube.org"

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

def read_file(path):
  """Return the contents of file 'path'."""
  with open(path) as f:
    return f.read()

def write_file(path, data):
  """Write 'data' into 'path' atomically."""
  with open(path + ".tmp", "w+") as f:
    f.write(data)
  os.rename(path + ".tmp", path)
