#!/usr/bin/env python

import requests
import time

# politeness settings
SLOW_FETCH_SLEEP = 1.0
USER_AGENT = "cfbot from http://commitfest.cputube.org"

def slow_fetch(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
  response = requests.get(url, headers={'User-Agent': USER_AGENT}, timeout=10)
  body = response.content
  response.close()
  time.sleep(SLOW_FETCH_SLEEP)
  return body
