#!/usr/bin/env python3

import cfbot_commitfest_rpc
import cfbot_util
import os
import shutil
from git import Repo
from urllib.parse import urlparse

PATCH_DIR = '/tmp/patch_files/'

# No need to clone, have Postgres repo in docker
# POSTGRES_GIT_URL = 'https://github.com/postgres/postgres.git'
POSTGRES_PATH = os.path.abspath('../postgres')
POSTGRES_REPO = Repo(POSTGRES_PATH)

# COMMITFEST_GIT_URL = 'git@github.com:postgresql-cfbot/postgresql.git'
COMMITFEST_GIT_URL = 'git@github.com:nbyavuz/postgres.git'
COMMITFEST_REMOTE_REPO = POSTGRES_REPO \
    .create_remote('commitfest', url=COMMITFEST_GIT_URL)

# COMMITFEST_ID = os.getenv('COMMITFEST_ID')
# SUBMISSION_ID = os.getenv('SUBMISSION_ID')
COMMITFEST_ID = 42
SUBMISSION_ID = 4173
BRANCH_NAME = f'commitfest/{COMMITFEST_ID}/{SUBMISSION_ID}'

THREAD_URL = cfbot_commitfest_rpc \
        .get_thread_url_for_submission(COMMITFEST_ID, SUBMISSION_ID)


def fetch_and_update_postgres_repo() -> list:
    POSTGRES_REPO.remotes.origin.fetch()
    POSTGRES_REPO.git.reset('--hard', 'origin/master')


def download_patches() -> list:
    """Downloads patches and returns list of patch_urls"""
    os.makedirs(PATCH_DIR, exist_ok=True)
    msg_id, patch_urls = cfbot_commitfest_rpc \
        .get_latest_patches_from_thread_url(THREAD_URL)
    print(msg_id, patch_urls)

    for patch_url in patch_urls:
        parsed = urlparse(patch_url)
        filename = os.path.basename(parsed.path)
        dest = os.path.join(PATCH_DIR, filename)
        with open(dest, "wb+") as f:
            f.write(cfbot_util.slow_fetch_binary(patch_url))

    return patch_urls


def find_files_by_extensions(extensions: tuple) -> list:
    files = []

    for f in os.listdir(PATCH_DIR):
        file_path = os.path.join(PATCH_DIR, f)
        if file_path.endswith(extensions) and os.path.isfile(file_path):
            files.append(file_path)

    return files


def extract_patches() -> None:
    extensions = ('.tgz', '.tar.gz', '.tar.bz2', '.gz', '.zip')
    files = find_files_by_extensions(extensions)

    for file in files:
        print(f'Extracting "{file}"...')
        shutil.unpack_archive(file, PATCH_DIR)


def apply_patches() -> None:
    extensions = ('.patch', '.diff')
    files = sorted(find_files_by_extensions(extensions))

    print(f'Checkout {BRANCH_NAME}...')
    POSTGRES_REPO.git.checkout('-b', BRANCH_NAME)

    for file in sorted(files):
        print(f'Applying "{file}"...')
        POSTGRES_REPO.git.execute(['git', 'am', file])


def push_repo() -> None:
    COMMITFEST_REMOTE_REPO \
        .push(refspec=f'{BRANCH_NAME}:{BRANCH_NAME}', force=True)


def main() -> None:
    fetch_and_update_postgres_repo()

    if download_patches():
        # Do these steps if there are any patches
        extract_patches()

        apply_patches()

        push_repo()


if __name__ == "__main__":
    main()
