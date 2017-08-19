#!/bin/sh

PATCHES=patches
TREE=postgresql
DATE=$( date +%Y%m%d )
TIMESTAMP=$( date +%Y%m%dT%H%M%S )
LOGDIR=logs/$DATE/$TIMESTAMP

mkdir -p $LOGDIR
rm -f logs/latest
ln -s $DATE/$TIMESTAMP logs/latest

# clean and update tree
( cd $TREE ; git checkout . > /dev/null && git clean -fd > /dev/null && git pull -q )

commit_id="$(cd $TREE && git show | head -1 | cut -d' ' -f2)"
echo "Commitfest submissions vs commit $commit_id:" > $LOGDIR/fail.log

# find all patchsets in suitable state...
for submission in $(ls $PATCHES/current | grep -v ".tmp" ) ; do
  status_file=$PATCHES/current/$submission/status
  if [ -f $status_file ] ; then
    if grep -i -E '(needs review|ready for committer)' < $status_file > /dev/null ; then
      (
        cd $TREE
        fail_log=../$LOGDIR/fail.log
        submission_dir=../patches/current/$submission
        message_id="$(cat $submission_dir/message_id)"
        name="$(cat $submission_dir/name)"
        status="$(cat $submission_dir/status)"
        success=1
        git checkout . > /dev/null
        git clean -fd > /dev/null
        echo "=== Commitfest submission: $submission \"$name\""
        echo "=== Patches fetched from message ID: $message_id"
        echo "=== Attempting to apply on top of commit $commit_id"
        for patch in $(ls $submission_dir/*.patch) ; do
          echo "=== Applying patch: $(basename $patch)"
          patch --batch -p1 < $patch || success=0
        done
        echo "=== Successfully applied: $success"
        if [ $success = "0" ] ; then
          echo "Apply failed: #$submission, [$status], message $message_id" >> $fail_log
        elif [ ! -f $submission_dir/trust ] ; then
          echo "Auto-build not trusted: #$submission, [$status], message $message_id" >> $fail_log
        else
          echo "=== Building..."
          prefix=/tmp/cfmon-$$
          rm -fr $prefix
          ( ./my_configure.sh && make clean && make ) || success=0
          echo "=== Successfully built: $success"
          if [ $success = "0" ] ; then
            echo "Build failed: #$submission, [$status], message $message_id" >> $fail_log
          else
            echo "=== Testing..."
            #( make check && ( cd src/test/isolation && gmake check ) ) || success=0
            make check || success=0
            if [ $success = "0" ] ; then
              echo "=== Dumping regression.diffs due to regression test failure"
              cat src/test/regress/regression.diffs
              echo "Regression tests failed: #$submission, [$status], message $message_id" >> $fail_log
            else
              ( cd src/test/isolation && gmake check ) || success=0
              if [ $success = "0" ] ; then
                echo "=== Dumping regression.diffs due to isolation test failure"
                cat src/test/isolation/output_iso/regression.diffs
                echo "Isolation tests failed: #$submission, [$status], message $message_id" >> $fail_log
              fi
            fi
            echo "=== Successfully tested: $success"
          fi
        fi
      ) > $LOGDIR/$submission.log 2>&1
    fi
  fi
done
