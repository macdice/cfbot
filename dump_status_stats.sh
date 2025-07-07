#!/bin/sh

OUT=www/status_stats.txt

set -e

psql cfbot -c "select branch_name,
                      status as build_status,
                      avg_elapsed,
                      stddev_elapsed,
                      n
                 from build_status_statistics
                where status in ('CREATED', 'TRIGGERED', 'SCHEDULED', 'EXECUTING')
             order by branch_name,
                      array_position(array['CREATED', 'TRIGGERED', 'SCHEDULED', 'EXECUTING'],
                      status)" > $OUT.tmp
psql cfbot -c "select branch_name,
                      task_name,
                      status as task_status,
                      avg_elapsed,
                      stddev_elapsed,
                      n
                 from task_status_statistics
                where status in ('CREATED', 'TRIGGERED', 'SCHEDULED', 'EXECUTING')
             order by branch_name,
                      task_name,
                      array_position(array['CREATED', 'TRIGGERED', 'SCHEDULED', 'EXECUTING'],
                      status)" >> $OUT.tmp

mv $OUT.tmp $OUT
