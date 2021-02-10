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

# Build a dict like { "macos": "success", ... }
def get_task_results(commit):
  result = {}
  builds = get_builds_for_commit(cfbot_config.CIRRUS_USER, cfbot_config.CIRRUS_REPO, commit)
  if len(builds) > 0:
    build = builds[0]["id"]
    tasks = get_tasks_for_build(build)
    for task in tasks:
      result[task["name"].lower()] = task
  #print(result)
  return result

def pull_build_results(conn):
  builds = None
  task_results_for_commit = {}
  cursor = conn.cursor()
  cursor.execute("""SELECT id,
                           commitfest_id,
                           submission_id,
                           ci_commit_id,
                           substring(provider, 8) as os
                      FROM build_result
                     WHERE provider like 'cirrus/%'
                       AND result IS NULL""")
  for id, commitfest_id, submission_id, ci_commit_id, os in cursor.fetchall():
      # avoid fetching the same build multiple times with a little cache
      if ci_commit_id not in task_results_for_commit:
          task_results_for_commit[ci_commit_id] = get_task_results(ci_commit_id)
      task_results = task_results_for_commit[ci_commit_id]
      if os in task_results:
        task_id = task_results[os]["id"]
        status = task_results[os]["status"]
        if status in ("FAILED", "ABORTED", "ERRORED"):
          result = "failure"
        elif status == "COMPLETED":
          result = "success"
        else:
          result = None
        url = "https://cirrus-ci.com/task/" + task_id
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
