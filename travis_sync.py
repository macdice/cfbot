#!/usr/bin/env python
#
# Poll travis-ci.org's API to fetch build results.

import cfbot_config
import cfbot_util
import json
import psycopg2

def poll_travis(conn):
  travis_builds = None
  cursor = conn.cursor()
  cursor.execute("""SELECT id,
                           commitfest_id,
                           submission_id,
                           ci_commit_id
                      FROM build_result
                     WHERE provider = 'travis'
                       AND result IS NULL""")
  for id, commitfest_id, submission_id, ci_commit_id in cursor:
    # lazily fetch data from travis only when we first need it
    if travis_builds == None:
       travis_builds = {}
       for item in json.loads(cfbot_util.slow_fetch(cfbot_config.TRAVIS_API_BUILDS)):
         print item
         travis_builds[(item["branch"], item["commit"])] = (item["result"], item["id"])
    branch = "commitfest/%s/%s" % (commitfest_id, submission_id)
    key = (branch, ci_commit_id)
    if key in travis_builds:
      result, build_id = travis_builds[key]
      if result == 1:
        result = "success"
      else:
        result = "failure"
      url = cfbot_config.TRAVIS_BUILD_URL % build_id
      cursor.execute("""UPDATE build_result
                           SET result = %s,
                               url = %s,
                               modified = now()
                         WHERE id = %s""",
                     (result, url, id))
      conn.commit()

if __name__ == "__main__":
  print cfbot_config.TRAVIS_API_BUILDS
  with psycopg2.connect(cfbot_config.DSN) as conn:
    poll_travis(conn)
