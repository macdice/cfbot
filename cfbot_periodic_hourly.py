#!/usr/bin/env python3

import cfbot_cirrus
import cfbot_util

if __name__ == "__main__":
    with cfbot_util.db() as conn:
        cfbot_cirrus.refresh_build_status_statistics(conn)
        cfbot_cirrus.refresh_task_status_statistics(conn)
        conn.commit()
