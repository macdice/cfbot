# How to run cfbot locally

## Setup database and config

```bash
cp example_cfbot_config.py cfbot_config.py
createdb cfbot
createuser cfbot
psql -v ON_ERROR_STOP=1 -f create.sql "dbname=cfbot"
```

## Setup python dependencies

If you want to install them globally:
```
pip install --user -r requirements.txt
```

If that doesn't work for some reason or you prefer to scope these dependencies
to the project, you can instead use a virtual env:

```bash
# create a virtual environment (only needed once)
python3 -m venv env

# activate the environment. You will need to activate this environment in
# your shell every time you want to run the tests. (so it's needed once per
# shell).
source env/bin/activate

# Install the dependencies (only needed once, or whenever extra dependencies
# get added to requirements.txt)
pip install -r requirements.txt
```

## Initialize patch burner template

On Linux

```bash
./cfbot_patchburner_docker_ctl.sh init-template
```

On FreeBSD
```bash
./cfbot_patchburner_ctl.sh init-template
```

## Run cfbot

```bash
./cfbot_periodic_minutely.py
```

## Debug a specific patch

```bash
./cfbot_patch.py 48 4496
```

## Code formatting and linting

```bash
# Format code
make format
# lint code
make lint
# Automatically fix linting errors
make lint-fix
# Automatically fix linting errors including unsafe fixes
# Unsafe fixes are those that may change the behavior of the code, but usually
# you want that behavior
make lint-fix-unsafe
# Run both "make format" and "make lint-fix-unsafe" (usually what you want)
make fix
```

# Useful production commands

Restart all services:
```bash
supervisorctl restart cfbot_worker: cfbot_api
```

Reset backoff from all submissions:
```sql
UPDATE submission set backoff_until = NULL, last_backoff = NULL where backoff_until is not null;
```

Check for old stuck runs:
```sql
\x auto
SELECT * FROM branch WHERE status='testing' and created < now() - interval '2 hours';
SELECT * FROM build WHERE created < now() - interval '2 hours' AND status='EXECUTING';
SELECT * FROM task  WHERE created < now() - interval '2 hours' AND status not in ('FAILED', 'ABORTED', 'ERRORED', 'COMPLETED', 'PAUSED');
```

Remove all runs that have been stuck for a while:
```sql
UPDATE branch SET status = 'failed' WHERE status='testing' and created < now() - interval '2 hours' RETURNING branch.id;
UPDATE build SET status = 'FAILED' WHERE created < now() - interval '2 hours' AND status='EXECUTING';
UPDATE task SET status = 'FAILED' WHERE created < now () - interval '2 hours' AND status not in ('FAILED', 'ABORTED', 'ERRORED', 'COMPLETED', 'PAUSED');
```

Remove a specific stuck run:
```sql
UPDATE branch SET status = 'failed' WHERE build_id = '<some build id>'
```
