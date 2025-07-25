#!/bin/sh
#
# This scripts contains parts of cfbot that need to be run as root in order to
# manipulate FreeBSD jails and ZFS datasets.
#
# CFBOT_USER must already exist on the host.  The guest will have a user with
# the same UID for ease of access to the jailed filesystem.
#
# Requirements: ezjail, sudo, git installed, then:
#
# /etc/rc.conf:
# cloned_interfaces="lo1"
# ezjail_enable="YES"
#
# sudo netif cloneup
#
# /usr/local/etc/ezjail.conf:
# ezjail_use_zfs="YES"
# ezjail_use_zfs_for_jails="YES"
# ezjail_jailzfs="zroot/usr/jails"
#
# sudo ezjail-admin install

set -e

TEMPLATE_JAIL_NAME=patchbase
TEMPLATE_ZFS_NAME=zroot/usr/jails/$TEMPLATE_JAIL_NAME
TEMPLATE_HOST_ROOT_PATH=/usr/jails/$TEMPLATE_JAIL_NAME

JAIL_NAME=patchburner
ZFS_NAME=zroot/usr/jails/$JAIL_NAME
HOST_ROOT_PATH=/usr/jails/$JAIL_NAME

CFBOT_USER=cfbot
CFBOT_UID=1002

usage() {
	echo "Usage: $1 init|create|apply|destroy"
	echo
	echo "init-template -- create a new jail 'patchbase'"
	echo
	echo "create -- create a new jail 'patchburner'"
	echo "apply -- apply all the patches found in /work/patches'"
	echo "destroy -- destroy 'patchburner' if it exists"
	echo
	echo "template-repo-patch -- report path of template git repo"
	echo "burner-patch-path -- report path where patches should be placed"
	echo "burner-repo-path -- report path of burner git repo"
	exit 1
}

init_template() {
	ezjail-admin create $TEMPLATE_JAIL_NAME 'lo2|127.0.1.0'
	ezjail-admin start $TEMPLATE_JAIL_NAME
	jexec $TEMPLATE_JAIL_NAME pw useradd -n $CFBOT_USER -u $CFBOT_UID
	ezjail-admin stop $TEMPLATE_JAIL_NAME

	mkdir $TEMPLATE_HOST_ROOT_PATH/work
	chown $CFBOT_USER:$CFBOT_USER $TEMPLATE_HOST_ROOT_PATH/work
	sudo -u $CFBOT_USER git clone https://git.postgresql.org/git/postgresql.git $TEMPLATE_HOST_ROOT_PATH/work/postgresql
}

destroy_patchburner_if_exists() {
	if ezjail-admin list | grep " $JAIL_NAME " >/dev/null; then
		ezjail-admin delete -f $JAIL_NAME
	fi
	if zfs list $ZFS_NAME >/dev/null 2>&1; then
		zfs destroy -f $ZFS_NAME
	fi
	if zfs list $TEMPLATE_ZFS_NAME@mysnapshot >/dev/null 2>&1; then
		zfs destroy -f $TEMPLATE_ZFS_NAME@mysnapshot
	fi
}

create_patchburner() {
	# clone it
	zfs snapshot $TEMPLATE_ZFS_NAME@mysnapshot
	zfs clone $TEMPLATE_ZFS_NAME@mysnapshot $ZFS_NAME
	ezjail-admin create -x $JAIL_NAME 'lo2|127.0.1.2'
	mkdir $HOST_ROOT_PATH/work/patches
	chown $CFBOT_USER:$CFBOT_USER $HOST_ROOT_PATH/work/patches
	cat >$HOST_ROOT_PATH/work/apply-patches.sh <<'EOF'
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

if command -v gpatch >/dev/null 2>&1; then
	# gpatch is a GNU patch that is compatible with BSD patch, but has some
	# extra features. We use it if available. This is mostly for FreeBSD to
	# behave like Linux.
	PATCH_CMD=gpatch
else
	PATCH_CMD=patch
fi

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
	if ! ($PATCH_CMD -p1 --no-backup-if-mismatch -V none -f -N <"/work/patches/$f" && git add .); then
		git reset HEAD .
		git checkout -- .
		git clean -fdx
		# We use git apply as a last option, because it provides the best
		# output for conflicts. It also works well for patches that were
		# already applied.
		echo "=== using 'git apply' to apply patch $f ==="
		git apply --3way --allow-empty "/work/patches/$f" || { git diff && exit 1; }
	fi

	if git diff --cached --quiet; then
		# No need to clutter the GitHub commit history  with commits that don't
		# change anything, usually this happens if a subset of the patchset has
		# already been applied.
		echo "=== Patch was already applied, skipping commit ==="
		continue
	fi
	git commit -m "$MESSAGE" --author="${NAME:-Commitfest Bot} <${EMAIL:-cfbot@cputube.org}>" --date="${DATE:-now}"

done
EOF
	chmod 775 $HOST_ROOT_PATH/work/apply-patches.sh
}

apply_patches_in_patchburner() {
	ezjail-admin start $JAIL_NAME >/dev/null
	jexec -U $CFBOT_USER $JAIL_NAME /work/apply-patches.sh
	result=$?
	ezjail-admin stop $JAIL_NAME >/dev/null
	rm -rf $HOST_ROOT_PATH/work/postgresql/.git/hooks
	rm -rf $HOST_ROOT_PATH/work/postgresql/.git/config
	cp $TEMPLATE_HOST_ROOT_PATH/work/postgresql/.git/config $HOST_ROOT_PATH/work/postgresql/.git/config
	exit $result
}

case $1 in
init-template)
	init_template
	;;
create)
	create_patchburner
	;;
destroy)
	destroy_patchburner_if_exists
	;;
apply)
	apply_patches_in_patchburner
	;;
template-repo-path)
	echo $TEMPLATE_HOST_ROOT_PATH/work/postgresql
	;;
burner-patch-path)
	echo $HOST_ROOT_PATH/work/patches
	;;
burner-repo-path)
	echo $HOST_ROOT_PATH/work/postgresql
	;;
*)
	usage
	;;
esac
