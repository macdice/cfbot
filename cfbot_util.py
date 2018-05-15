#!/usr/bin/env python

import requests
import time
import cfbot_config

def slow_fetch(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
  response = requests.get(url, headers={'User-Agent': cfbot_config.USER_AGENT}, timeout=cfbot_config.TIMEOUT)
  body = response.content
  response.close()
  time.sleep(cfbot_config.SLOW_FETCH_SLEEP)
  return body
