#!/bin/sh

for patchset in patches/current/* ; do
  echo "Examining patchset $patchset..."
  if [ ! -f $patchset/trust ] ; then
    less $patchset/*.patch
    read -p "Do you trust that?" trust
    case "$trust" in
      y|Y) touch $patchset/trust;;
    esac
  fi
done
