UPDATE branch SET status = 'failed' WHERE status='testing' and created < now() - interval '2 hours' RETURNING build_id;
UPDATE build SET status = 'FAILED' WHERE created < now() - interval '2 hours' AND status='EXECUTING';
UPDATE task SET status = 'FAILED' WHERE created < now () - interval '2 hours' AND status not in ('FAILED', 'ABORTED', 'ERRORED', 'COMPLETED', 'PAUSED');
