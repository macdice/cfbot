""" Just a few utility functions used throughout the project."""
import requests
import time
import os

from config import *

def slow_fetch(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
  response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=10)
  body = response.content
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
