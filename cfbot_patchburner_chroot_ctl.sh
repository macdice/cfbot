#!/bin/sh
#
# This script applies patches inside a chroot.  As the Linux chroot man page
# surprisingly admits, it'd be better to use FreeBSD for this purpose, but here
# we go.
#
# TODO: Figure out how to deal with the fact that chroot requires privs
# TODO: Figure out how to run script without network access
# TODO: Figure out how to limit script run time

set -e

TEMPLATE_DIR=patchburner_template
CHROOT_DIR=patchburner_chroot
RUNAS_USER=tmunro

usage()
{
  echo "Usage: $1 init|create|apply|destroy"
  echo
  echo "init-template -- create 'patchburner_template'"
  echo
  echo "create -- create a new chroot under 'patchburner'"
  echo "apply -- apply all the patches found in patchburner/work/patches'"
  echo "destroy -- destroy 'patchburner' if it exists"
  echo
  echo "template-repo-patch -- report path of template git repo"
  echo "burner-patch-path -- report path where patches should be placed"
  echo "burner-repo-path -- report path of burner git repo"
  exit 1
}

init_template()
{
  # This is just a clean checkout of the git repo, which cfbot will keep
  # updated, and we'll copy whever we need a throw-away copy to apply patches
  # to.  This just avoids having to clone it every time, which would suck.
  # You should only need to init once.
  mkdir $TEMPLATE_DIR
  mkdir $TEMPLATE_DIR/work
  git clone git://git.postgresql.org/git/postgresql.git $TEMPLATE_DIR/work/postgresql
}

destroy_patchburner_if_exists()
{
  rm -fr $CHROOT_DIR
}

create_patchburner()
{
  # Copy the template to get a clean up-to-date repo.
  cp -r $TEMPLATE_DIR $CHROOT_DIR

  # Things we need to be able to apply patches in our throw-away chroot:
  #
  # * sh
  # * patch (must be GNU patch)
  # * tar
  # * unzip
  # * gzip
  # * gunzip
  # * find
  # * sort
  #
  # We copy these from the OS root ever time rather than using the template,
  # to pick up bugfixes.

  case "`uname`" in
    Linux*)
      # Copy the minimal set of bits and pieces from from Debian 10 layout.
      mkdir -p $CHROOT_DIR/bin $CHROOT_DIR/usr/bin
      mkdir -p $CHROOT_DIR/lib64 $CHROOT_DIR/lib/x86_64-linux-gnu
      for bin in sh dash ; do
        cp /bin/$bin $CHROOT_DIR//bin/
      done
      for bin in patch unzip gzip gunzip tar find sort ; do
        cp /usr/bin/$bin $CHROOT_DIR/usr/bin/
      done
      for lib in libbz2.so libacl.so libselinux.so libc.so libm.so libattr.so libpcre.so libdl.so libpthread.so ; do
        cp /lib/x86_64-linux-gnu/$lib* $CHROOT_DIR/lib/x86_64-linux-gnu/
      done
      cp /lib64/ld-linux-x86-64.so* $CHROOT_DIR/lib64/
      ;;
    *)
      echo "I don't know what operating system this is."
      exit 1
      ;;
  esac

  # create the patching script
  mkdir $CHROOT_DIR/work/patches
  cat > $CHROOT_DIR/work/apply-patches.sh <<EOF
#!/bin/sh
#
# apply all patches found in /work/patches

set -e

# unpack and zip archives, tarballs etc
cd /work/patches
for f in \$(find . -name '*.tgz' -o -name '*.tar.gz' -o -name '*.tar.bz2') ; do
  echo "=== expanding \$f"
  tar xzvf \$f
done
for f in \$(find . -name '*.gz') ; do
  echo "=== expanding \$f"
  gunzip \$f
done
for f in \$(find . -name '*.zip') ; do
  echo "=== expanding \$f"
  unzip \$f
done

# now apply all .patch and .diff files
cd /work/postgresql
for f in \$(cd /work/patches && find . -name '*.patch' -o -name '*.diff' | sort) ; do
  echo "=== applying patch \$f"
  patch --no-backup-if-mismatch -p1 -V none -f < "/work/patches/\$f"
done
EOF
  chmod 775 $CHROOT_DIR/work/apply-patches.sh
}

apply_patches_in_patchburner()
{
  # TODO: Rather than running this directly so we need privs, perhaps there
  # should be a setuid script that can do this bit for us.
  /sbin/chroot --userspec=$RUNAS_USER $CHROOT_DIR /work/apply-patches.sh
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
    echo $CHROOT_DIR/work/patches
    ;;
  burner-repo-path)
    echo $CHROOT_DIR/work/postgresql
    ;;
  *)
    usage
    ;;
esac
