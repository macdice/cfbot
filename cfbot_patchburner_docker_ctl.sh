#!/bin/sh
#
# This script applies patches inside a docker container.

set -e

TEMPLATE_DIR=patchburner_template
MOUNTED_DIR=patchburner_docker

usage() {
	echo "Usage: $1 init-template|create|apply|destroy"
	echo
	echo "init-template -- create 'patchburner_template'"
	echo
	echo "create -- create a new docker image with the cfbot-patchburner tag"
	echo "apply -- apply all the patches found in patchburner/work/patches'"
	echo "destroy -- destroy 'patchburner' if it exists"
	echo
	echo "template-repo-patch -- report path of template git repo"
	echo "burner-patch-path -- report path where patches should be placed"
	echo "burner-repo-path -- report path of burner git repo"
	exit 1
}

init_template() {
	# This is just a clean checkout of the git repo, which cfbot will keep
	# updated, and we'll copy whever we need a throw-away copy to apply patches
	# to.  This just avoids having to clone it every time, which would suck.
	# You should only need to init once.
	mkdir $TEMPLATE_DIR
	mkdir $TEMPLATE_DIR/work
	git clone git://git.postgresql.org/git/postgresql.git $TEMPLATE_DIR/work/postgresql
}

destroy_patchburner_if_exists() {
	rm -fr $MOUNTED_DIR
}

create_patchburner() {
	# Copy the template to get a clean up-to-date repo.
	cp -r $TEMPLATE_DIR $MOUNTED_DIR

	mkdir $MOUNTED_DIR/work/patches

	docker build . -t cfbot-patchburner --build-arg USER_ID=$(id -u) --build-arg GROUP_ID=$(id -g)
}

apply_patches_in_patchburner() {
	docker run --mount=type=bind,source=$PWD/$MOUNTED_DIR/work,target=/work --workdir=/work/postgresql -u $(id -u):$(id -g) cfbot-patchburner /usr/local/bin/apply-patches.sh
	rm -rf $MOUNTED_DIR/work/postgresql/.git/hooks
	rm -rf $MOUNTED_DIR/work/postgresql/.git/config
	cp $TEMPLATE_DIR/work/postgresql/.git/config $MOUNTED_DIR/work/postgresql/.git/config
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
	echo $TEMPLATE_DIR/work/postgresql
	;;
burner-patch-path)
	echo $MOUNTED_DIR/work/patches
	;;
burner-repo-path)
	echo $MOUNTED_DIR/work/postgresql
	;;
*)
	usage
	;;
esac
