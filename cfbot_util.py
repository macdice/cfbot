import cfbot_config
import psycopg2
import requests
import time

def slow_fetch(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
  response = requests.get(url, headers={'User-Agent': cfbot_config.USER_AGENT}, timeout=cfbot_config.TIMEOUT)
  time.sleep(cfbot_config.SLOW_FETCH_SLEEP)
  return response.text

def slow_fetch_binary(url):
  """Fetch the body of a web URL, but sleep every time too to be kind to the
     commitfest server."""
  response = requests.get(url, headers={'User-Agent': cfbot_config.USER_AGENT}, timeout=cfbot_config.TIMEOUT)
  time.sleep(cfbot_config.SLOW_FETCH_SLEEP)
  return response.content

def gc(conn):
  cursor = conn.cursor()
  cursor.execute("""DELETE FROM task WHERE created < now() - interval '1 week'""")
  cursor.execute("""DELETE FROM task WHERE created < now() - interval '4 hours' AND status = 'EXECUTING'""") #?
  cursor.execute("""DELETE FROM branch WHERE created < now() - interval '1 week'""")
  # TODO: GC the git tree too!
  conn.commit()

def db():
  """Get a database connection."""
  return psycopg2.connect(cfbot_config.DSN)
