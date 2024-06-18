#!/bin/sh
#
# apply all patches found in /work/patches. This is meant to be run from a
# docker container.

set -e

# unpack and zip archives, tarballs etc
cd /work/patches
for f in $(find . -name '*.tgz' -o -name '*.tar.gz' -o -name '*.tar.bz2') ; do
  echo "=== expanding $f"
  tar xzvf $f
done
for f in $(find . -name '*.gz') ; do
  echo "=== expanding $f"
  gunzip $f
done
for f in $(find . -name '*.zip') ; do
  echo "=== expanding $f"
  unzip $f
done

# now apply all .patch and .diff files
cd /work/postgresql

# But first set up the git user
git config user.name "Commitfest Bot"
git config user.email "cfbot@cputube.org"

for f in $(cd /work/patches && find . -name '*.patch' -o -name '*.diff' | sort) ; do
  echo "=== applying patch $f"
  git mailinfo ../msg ../patch < "/work/patches/$f" > ../info
  # Clean out /dev/null in case git mailinfo wrote something to it
  : > /dev/null

  NAME=$(sed -n -e 's/^Author: //p' ../info)
  EMAIL=$(sed -n -e 's/^Email: //p' ../info)
  SUBJECT=$(sed -n -e 's/^Subject: //p' ../info)
  DATE=$(sed -n -e 's/^Date: //p' ../info)
  MESSAGE="$(cat ../msg)"
  MESSAGE="${SUBJECT:-"[PATCH]: $f"}${MESSAGE:+

}${MESSAGE}"

  patch --no-backup-if-mismatch -p1 -V none -f < "/work/patches/$f"
  git add .
  git commit -m "$MESSAGE" --author="${NAME:-Commitfest Bot} <${EMAIL:-cfbot@cputube.org}>" --date="${DATE:-now}" --allow-empty

done

