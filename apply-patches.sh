#!/bin/sh
#
# apply all patches found in /work/patches. This is meant to be run from a
# docker container.

set -e

date # Add a timestamp to the log

# unpack and zip archives, tarballs etc
cd /work/patches
for f in $(find . -name '*.tgz' -o -name '*.tar.gz' -o -name '*.tar.bz2'); do
	echo "=== expanding $f"
	tar xzvf $f
done
for f in $(find . -name '*.gz'); do
	echo "=== expanding $f"
	gunzip $f
done
for f in $(find . -name '*.zip'); do
	echo "=== expanding $f"
	unzip $f
done

# now apply all .patch and .diff files
cd /work/postgresql

# But first set up the git user
git config user.name "Commitfest Bot"
git config user.email "cfbot@cputube.org"

# Somehow this is necessary to avoid "does not match index" when running "git apply"
git status

for f in $(cd /work/patches && find . -name '*.patch' -o -name '*.diff' | sort); do
	# This extracts the information from the patch, just like how "git am" would
	# do it. But because not all patches are created with "git format-patch" this
	# information, we need to do this manually and fallback to sensible defaults.
	# Also, even for patches created with "git format-patch", sometimes we fail
	# to apply it with "git am" and then fall back to patch(1) or "git apply".
	# For both of those we still want to restore the original message.
	git mailinfo ../msg ../patch <"/work/patches/$f" >../info
	# Clean out /dev/null in case git mailinfo wrote something to it
	: >/dev/null

	NAME=$(sed -n -e 's/^Author: //p' ../info)
	EMAIL=$(sed -n -e 's/^Email: //p' ../info)
	SUBJECT=$(sed -n -e 's/^Subject: //p' ../info)
	DATE=$(sed -n -e 's/^Date: //p' ../info)
	MESSAGE="$(cat ../msg)"
	MESSAGE="${SUBJECT:-"[PATCH]: $f"}${MESSAGE:+

}${MESSAGE}"

	set +x
	echo "=== using 'git am' to apply patch $f ==="
	# git am usually does a decent job at applying a patch, as long as the
	# patch was created with git format-patch. It also atuomatically creates a
	# git commit, so we don't need to do that manually and can just continue
	# with the next patch if it succeeds.
	git am --3way "/work/patches/$f" && continue
	# Okay it failed, let's clean up and try the next option.
	git reset HEAD .
	git checkout -- .
	git clean -fdx
	echo "=== using patch(1) to apply patch $f ==="
	if ! patch -p1 --no-backup-if-mismatch -V none -f -N <"/work/patches/$f" && git add .; then
		git reset HEAD .
		git checkout -- .
		git clean -fdx
		# We use git apply as a last option, because it provides the best
		# output for conflicts. It also works well for patches that were
		# already applied.
		echo "=== using 'git apply' to apply patch $f ==="
		git apply --3way --allow-empty "/work/patches/$f" || { git diff && exit 1; }
	fi

	if git diff --cached; then
		# No need to clutter the GitHub commit history  with commits that don't
		# change anything, usually this happens if a subset of the patchset has
		# already been applied.
		echo "=== Patch was already applied, skipping commit ==="
		continue
	fi
	git commit -m "$MESSAGE" --author="${NAME:-Commitfest Bot} <${EMAIL:-cfbot@cputube.org}>" --date="${DATE:-now}"

done
