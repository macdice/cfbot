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

def gc(conn):
  cursor = conn.cursor()
  cursor.execute("""DELETE FROM build_result WHERE created < now() - interval '1 week'""")
  cursor.execute("""DELETE FROM build_result WHERE created < now() - interval '4 hours' AND result is NULL""")
  conn.commit()

def db():
  """Get a database connection."""
  return psycopg2.connect(cfbot_config.DSN)
