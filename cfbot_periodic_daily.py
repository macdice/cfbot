#!/usr/bin/env python3

import cfbot_gc
import cfbot_util

if __name__ == "__main__":
    with cfbot_util.db() as conn:
        cfbot_gc.gc(conn)
        conn.commit()
