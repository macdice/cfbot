#!/usr/bin/env python3

import cfbot_gc
import cfbot_github
import cfbot_util

if __name__ == "__main__":
    with cfbot_util.db() as conn:
        cfbot_gc.gc(conn)
        conn.commit()

        # Now that we've deleted build records older than RETENTION_ALL, we can
        # delete unreferenced remote branches to avoid leaving junk in our
        # Github account.
        cfbot_github.gc_remote_branches(conn)
        conn.commit()
