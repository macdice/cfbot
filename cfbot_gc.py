#!/usr/bin/env python

import cfbot_config
import cfbot_util
import logging


def gc(conn):
    cursor = conn.cursor()

    cursor.execute("""set work_mem = '256MB'""")

    # Delete large objects to free up disk space.  These should have been
    # ingested by the highlights system after a short time (though we have no
    # state machine to track that).
    #
    # XXX In fact we don't really need the artifact and task_command rows at
    # all, so we could perhaps trim them much sooner?
    cursor.execute(
        """
  update artifact
     set body = null
    from task
   where artifact.task_id = task.task_id
     and artifact.body is not null
     and task.created < now() - interval '1 days' * %s
              """,
        (cfbot_config.RETENTION_LARGE_OBJECTS,),
    )
    logging.info("garbage collected %d artifact bodies", cursor.rowcount)
    cursor.execute(
        """
  update task_command
     set log = null
    from task
   where task_command.task_id = task.task_id
     and task_command.log is not null
     and task.created < now() - interval '1 days' * %s
              """,
        (cfbot_config.RETENTION_LARGE_OBJECTS,),
    )
    logging.info("garbage collected %d task_command logs", cursor.rowcount)
    conn.commit()

    # Trim old builds and dependent data in referential integrity order.
    cursor.execute(
        """
  delete from artifact
   where task_id in (select task_id
                       from task join build using (build_id)
                      where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from test
   where task_id in (select task_id
                       from task join build using (build_id)
                      where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from task_command
   where task_id in (select task_id
                       from task join build using (build_id)
                      where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from task_command
   where task_id in (select task_id
                       from task join build using (build_id)
                      where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from highlight
   where task_id in (select task_id
                       from task join build using (build_id)
                      where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from task_status_history
   where task_id in (select task_id
                       from task join build using (build_id)
                      where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from task
   where build_id in (select build_id
                        from build
                       where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from branch
   where build_id in (select build_id
                        from build
                       where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from build_status_history
   where build_id in (select build_id
                        from build
                       where build.created < now() - interval '1 day' * %s)""",
        (cfbot_config.RETENTION_ALL,),
    )
    cursor.execute(
        """
  delete from build
   where created < now() - interval '1 day' * %s""",
        (cfbot_config.RETENTION_ALL,),
    )
    logging.info(
        "garbage collected %d builds older than %d days",
        cursor.rowcount,
        cfbot_config.RETENTION_ALL,
    )
    conn.commit()

    # Trim old branches that don't have an associated build (legacy or they
    # failed to apply and never got a build).
    cursor.execute(
        """
  delete from branch
   where build_id is null
     and created < now() - interval '1 day' * %s""",
        (cfbot_config.RETENTION_ALL,),
    )
    logging.info(
        "garbage collected %d branches with no build older than %d days",
        cursor.rowcount,
        cfbot_config.RETENTION_ALL,
    )
    conn.commit()


if __name__ == "__main__":
    with cfbot_util.db() as conn:
        gc(conn)
