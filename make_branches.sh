#!/bin/sh

PATCHES=patches
TREE=postgresql
DATE=$( date +%Y%m%d )
TIMESTAMP=$( date +%Y%m%dT%H%M%S )
LOGDIR=logs/$DATE/$TIMESTAMP
BRANCH_TO_TRACK=master
REMOTE_TO_PUSH_TO=github

GIT_SSH_COMMAND='ssh -i ~/.ssh/cfbot_github_rsa'

# TODO figure this out
commitfest=14

mkdir -p $LOGDIR
rm -f logs/latest
ln -s $DATE/$TIMESTAMP logs/latest

# clean and update tree
( cd $TREE ; git checkout . > /dev/null && git clean -fd > /dev/null && git checkout $BRANCH_TO_TRACK && git pull -q )

# push the master branch
git push github master >> $LOGDIR/branch.log 2>&1 || exit 1

#exit 0

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
        branch="commitfest/14/$submission"
        success=1
        # get a clean master branch
        git checkout . > /dev/null
        git clean -fd > /dev/null
        git checkout master
        # delete if it already exists
        git branch -D $branch
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
        else
          echo "=== Creating a branch for Travis..."
          git checkout -b $branch
          git add -a
          echo "language: c" > .travis.yml
          echo "script: ./configure && make && make check && (cd src/test/isolation && make check)" >> .travis.yml
          git add .travis.yml
          git commit -F - <<EOF
Automatic commit for Commitfest submission #$submission.

This commit was automatically generated and includes a Travis control file
to tell travis-ci.org how to build and test the submission.  This branch
will be overwritten so there is not much point in cloning it!

Commitfest entry: https://commitfest.postgresql.org/$commitfest/$submission
Patches fetched from: https://www.postgresql.org/message-id/$message_id
EOF
        fi
      ) > $LOGDIR/$submission.log 2>&1
    fi
  fi
done
