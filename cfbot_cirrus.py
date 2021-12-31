import cfbot_config
import cfbot_util

import requests
import sys

def query_cirrus(query, variables):
    request = requests.post('https://api.cirrus-ci.com/graphql',
                            json={'query': query, 'variables': variables})
    if request.status_code == 200:
        result = request.json()
        return result["data"]
    else:
        raise Exception("Query failed to run by returning code of {}. {}".format(request.status_code, query))

def get_builds_for_commit(owner, repo, sha):
    query = '''
        query buildBySha($owner: String!, $repo: String!, $sha: String!) {
          searchBuilds(repositoryOwner: $owner, repositoryName: $repo, SHA: $sha) {
            id
            status
            buildCreatedTimestamp
          }
        }
    '''
    variables = dict(owner=owner, repo=repo, sha=sha)
    result = query_cirrus(query, variables)
    if "searchBuilds" in result and len(result["searchBuilds"]):
        return result["searchBuilds"]
    else:
        return []

def get_tasks_for_build(build_id):
    query = '''
        query tasksByBuildID($build_id: ID!) {
          build(id: $build_id) {
            tasks {
              id
              name
              status
            }
          }
        }
    '''
    variables = dict(build_id = build_id)
    result = query_cirrus(query, variables)
    return result["build"]["tasks"]

def get_task_results(commit):
  result = {}
  builds = get_builds_for_commit(cfbot_config.CIRRUS_USER, cfbot_config.CIRRUS_REPO, commit)
  if len(builds) > 0:
    build = builds[0]["id"]
    return get_tasks_for_build(build)
  return []

def pull_build_results(conn):
  builds = None
  task_results_for_commit = {}
  cursor = conn.cursor()
  cursor.execute("""SELECT commitfest_id,
                           submission_id,
                           commit_id
                      FROM branch
                     WHERE status = 'testing'""")
  for commitfest_id, submission_id, commit_id in cursor.fetchall():
      keep_polling = False
      tasks = get_task_results(commit_id)
      if len(tasks) == 0:
          keep_polling = True
          continue
      for task in get_task_results(commit_id):
        task_id = task["id"]
        name = task["name"]
        status = task["status"]
        if status not in ("FAILED", "ABORTED", "ERRORED", "COMPLETED"):
            keep_polling = True
        url = "https://cirrus-ci.com/task/" + task_id
        cursor.execute("""UPDATE task
                             SET status = %s,
                                 modified = now()
                           WHERE commitfest_id = %s
                             AND submission_id = %s
                             AND commit_id = %s
                             AND task_name = %s""",
                       (status, commitfest_id, submission_id, commit_id, name))
        if cursor.rowcount == 0:
          cursor.execute("""INSERT INTO task (commitfest_id, submission_id, task_name, commit_id, status, url, created, modified)
                            VALUES (%s, %s, %s, %s, %s, %s, now(), now())""",
                         (commitfest_id, submission_id, name, commit_id, status, url))
  
      if not keep_polling:
        cursor.execute("""UPDATE branch
                             SET status = 'finished',
                                 modified = now()
                           WHERE commitfest_id = %s
                             AND submission_id = %s
                             AND commit_id = %s""",
                       (commitfest_id, submission_id, commit_id))
  conn.commit()

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    pull_build_results(conn)
