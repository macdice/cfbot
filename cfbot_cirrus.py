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

def get_artifacts_for_task(task_id):
    query = '''
        query TaskById($id: ID!) { task(id: $id) { id, name, artifacts { files { path, size } } } }
        '''
    variables = dict(id=task_id)
    result = query_cirrus(query, variables)
    #print(result)
    artifacts = result["task"]["artifacts"]
    paths = []
    #print(artifacts)
    for f in artifacts:
        for p in f["files"]:
            paths.append((p["path"], p["size"]))
    return paths

def get_commands_for_task(task_id):
    query = '''
        query TaskById($id: ID!) { task(id: $id) { commands { name, type, status, durationInSeconds, logsTail } } }
        '''
    variables = dict(id=task_id)
    result = query_cirrus(query, variables)
    #print(result)
    simple_result = []
    for command in result["task"]["commands"]:
      name = command["name"]
      xtype = command["type"]
      status = command["status"]
      duration = command["durationInSeconds"]
      log_lines = command["logsTail"]
      if log_lines == None:
          log = ""
      else:
          log = "\n".join([line.replace('\x00', '') for line in log_lines])
      simple_result.append((name, xtype, status, duration, log))
    return simple_result

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
        if status == "PAUSED":
            continue    # ignore for now
        if status not in ("FAILED", "ABORTED", "ERRORED", "COMPLETED"):
            keep_polling = True
        url = "https://cirrus-ci.com/task/" + task_id
        cursor.execute("""SELECT status
                            FROM task
                           WHERE task_id = %s""",
                       (task_id, ))
        row = cursor.fetchone()
        if row:
          # only update if status changes, so we can use the modified time
          if row[0] != status:
            cursor.execute("""UPDATE task
                                 SET status = %s,
                                     modified = now()
                               WHERE task_id = %s""",
                           (status, task_id))
        else:
          cursor.execute("""INSERT INTO task (task_id, commitfest_id, submission_id, task_name, commit_id, status, url, created, modified)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, now(), now())""",
                         (task_id, commitfest_id, submission_id, name, commit_id, status, url))
  
      if not keep_polling:
        cursor.execute("""UPDATE branch
                             SET status = 'finished',
                                 modified = now()
                           WHERE commitfest_id = %s
                             AND submission_id = %s
                             AND commit_id = %s""",
                       (commitfest_id, submission_id, commit_id))
        for path, size in get_artifacts_for_task(task_id):
          cursor.execute("""INSERT INTO artifact (task_id, path, size)
                            VALUES (%s, %s, %s)""",
                         (task_id, path, size))
        for name, xtype, status, duration, log in get_commands_for_task(task_id):
          cursor.execute("""INSERT INTO task_command (task_id, name, type, status, duration, log)
                            VALUES (%s, %s, %s, %s, %s * interval '1 second', %s)""",
                         (task_id, name, xtype, status, duration, log))
  conn.commit()

def backfill_artifact(conn):
  cursor = conn.cursor()
  cursor.execute("""SELECT commitfest_id, submission_id, task_name, commit_id, task_id
                      FROM task t
                     WHERE status = 'FAILED'
                       AND task_id IS NOT NULL
                       AND NOT EXISTS (SELECT *
                                         FROM artifact a
                                        WHERE a.task_id = a.task_id)""")
  for commitfest_id, submission_id, name, commit_id, task_id in cursor.fetchall():
    for path, size in get_artifacts_for_task(task_id):
      cursor.execute("""INSERT INTO artifact (task_id, path, size)
                        VALUES (%s, %s, %s)""",
                     (task_id, path, size))
    conn.commit()

def backfill_task_command(conn):
  cursor = conn.cursor()
  cursor.execute("""SELECT commitfest_id, submission_id, task_name, commit_id, task_id
                      FROM task t
                     WHERE status IN ('FAILED', 'COMPLETED')
                       AND task_id IS NOT NULL
                       AND NOT EXISTS (SELECT *
                                         FROM task_command c
                                        WHERE t.task_id = c.task_id)""")
  for commitfest_id, submission_id, name, commit_id, task_id in cursor.fetchall():
    for name, xtype, status, duration, log in get_commands_for_task(task_id):
      cursor.execute("""INSERT INTO task_command (task_id, name, type, status, duration, log)
                        VALUES (%s, %s, %s, %s, %s * interval '1 second', %s)""",
                     (task_id, name, xtype, status, duration, log))
    conn.commit()


if __name__ == "__main__":
#  print(get_commands_for_task('5646021133336576'))
#    get_artifacts_for_task('5646021133336576')
  with cfbot_util.db() as conn:
    backfill_artifact(conn)
    #backfill_task_command(conn)
#    backfill_task_command(conn)
#    pull_build_results(conn)
