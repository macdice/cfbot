#!/usr/bin/env python

import cfbot_util

def macro_gc(conn):
  cursor = conn.cursor()

  cursor.execute("""set work_mem = '256MB'""")
  #cursor.execute("""set max_parallel_workers_per_gather = 0""")
  # XXX figure out the order to delete old stuff like this in
  #cursor.execute("""DELETE FROM task WHERE created < now() - interval '6 months'""")
  #cursor.execute("""DELETE FROM branch WHERE created < now() - interval '6 months'""")
  #cursor.execute("""UPDATE branch SET status = 'timeout' WHERE created < now() - interval '2 hours' AND status = 'testing'""")
  # TODO: GC the git tree too!

  # delete old artifact bodies due to lack of disk space
  cursor.execute("""
  update artifact
     set body = null
    from task
   where artifact.task_id = task.task_id
     and artifact.body is not null
     and task.created < now() - interval '2 days'
              """)
  conn.commit()

  # likewise for task_command logs
  cursor.execute("""
  update task_command
     set log = null
    from task
   where task_command.task_id = task.task_id
     and task_command.log is not null
     and task.created < now() - interval '2 days'
              """)

  # likewise for everything derived from old tasks
  cursor.execute("""
  delete from artifact
   where task_id in (select task_id
                       from task
                      where created < now() - interval '90 days')""")
  cursor.execute("""
  delete from test
   where task_id in (select task_id
                       from task
                      where created < now() - interval '90 days')""")
  cursor.execute("""
  delete from task_command
   where task_id in (select task_id
                       from task
                      where created < now() - interval '90 days')""")
  conn.commit()

if __name__ == "__main__":
  with cfbot_util.db() as conn:
    macro_gc(conn)
