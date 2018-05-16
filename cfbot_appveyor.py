#!/usr/bin/env python
#
# Poll appveyor.com's API to fetch build results and merge them into our
# build_result table.

import cfbot_config
import cfbot_util
import json

def pull_build_results(conn):
  builds = None
  cursor = conn.cursor()
  cursor.execute("""SELECT id,
                           commitfest_id,
                           submission_id,
                           ci_commit_id
                      FROM build_result
                     WHERE provider = 'appveyor'
                       AND result IS NULL""")
  for id, commitfest_id, submission_id, ci_commit_id in cursor.fetchall():
    # lazily fetch data from travis only when we first need it
    if builds == None:
       builds = {}
       for item in json.loads(cfbot_util.slow_fetch(cfbot_config.APPVEYOR_API_BUILDS))["builds"]:
         builds[(item["branch"], item["commitId"])] = (item["status"], item["version"])
    branch = "commitfest/%s/%s" % (commitfest_id, submission_id)
    key = (branch, ci_commit_id)
    if key in builds:
      result, build_id = builds[key]
      if result == "success":
        result = "success"
      elif result == "failed":
        result = "failure"
      else:
        result = None
      url = cfbot_config.APPVEYOR_BUILD_URL % build_id
      cursor.execute("""UPDATE build_result
                           SET result = %s,
                               url = %s,
                               modified = now()
                         WHERE id = %s""",
                     (result, url, id))
      conn.commit()

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    pull_build_results(conn)
