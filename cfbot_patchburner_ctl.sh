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

CFBOT_USER=munro

usage()
{
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

init_template()
{
  ezjail-admin create $TEMPLATE_JAIL_NAME 'lo1|127.0.1.0'
  ezjail-admin start $TEMPLATE_JAIL_NAME
  jexec $TEMPLATE_JAIL_NAME pw useradd -n $CFBOT_USER -u $CFBOT_UID
  ezjail-admin stop $TEMPLATE_JAIL_NAME

  mkdir $TEMPLATE_HOST_ROOT_PATH/work
  chown $CFBOT_USER:$CFBOT_USER $TEMPLATE_HOST_ROOT_PATH/work
  sudo -u $CFBOT_USER git clone git://git.postgresql.org/git/postgresql.git $TEMPLATE_HOST_ROOT_PATH/work/postgresql
}

destroy_patchburner_if_exists()
{
  if ezjail-admin list | grep " $JAIL_NAME " > /dev/null ; then
    ezjail-admin delete -f $JAIL_NAME
  fi
  if zfs list $ZFS_NAME > /dev/null ; then
    zfs destroy $ZFS_NAME
  fi
  if zfs list $TEMPLATE_ZFS_NAME@mysnapshot > /dev/null ; then
    zfs destroy $TEMPLATE_ZFS_NAME@mysnapshot
  fi
}

create_patchburner()
{
  # clone it
  zfs snapshot $TEMPLATE_ZFS_NAME@mysnapshot
  zfs clone    $TEMPLATE_ZFS_NAME@mysnapshot $ZFS_NAME
  ezjail-admin create -x $JAIL_NAME 'lo1|127.0.1.2'
  mkdir $HOST_ROOT_PATH/work/patches
  chown $CFBOT_USER:$CFBOT_USER $HOST_ROOT_PATH/work/patches
  cat > $HOST_ROOT_PATH/work/apply-patches.sh <<EOF
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
  gzip -d \$f
done
for f in \$(find . -name '*.zip') ; do
  echo "=== expanding \$f"
  unzip \$f
done

# now apply all .patch and .diff files
cd /work/postgresql
for f in \$(cd /work/patches && find . -name '*.patch' -o -name '*.diff' | sort) ; do
  echo "=== applying patch \$f"
  patch -p1 -V none -f < "/work/patches/\$f"
done
EOF
  chmod 775 $HOST_ROOT_PATH/work/apply-patches.sh
}

apply_patches_in_patchburner()
{
  ezjail-admin start $JAIL_NAME > /dev/null
  jexec -U $CFBOT_USER $JAIL_NAME /work/apply-patches.sh
  result=$?
  ezjail-admin stop $JAIL_NAME > /dev/null
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
