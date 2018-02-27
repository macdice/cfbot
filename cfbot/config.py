# A few configuable things.

# Settings used when hitting commitfest.postgresql.org.
SLOW_FETCH_SLEEP = 1.0                                                         
USER_AGENT = "cfbot from http://commitfest.cputube.org"                        

# File added to github branches to trigger Travis CI builds.
TRAVIS_FILE = """
sudo: required
addons:
  apt:
    packages:
      - gdb
      - lcov
      - libipc-run-perl
      - libperl-dev
      - libpython-dev
      - tcl-dev
      - libldap2-dev
      - libicu-dev
      - docbook
      - docbook-dsssl
      - docbook-xsl
      - libxml2-utils
      - openjade1.3
      - opensp
      - xsltproc
language: c
cache: ccache
before_install:
  - echo '/tmp/%e-%s-%p.core' | sudo tee /proc/sys/kernel/core_pattern
  - echo "deb http://archive.ubuntu.com/ubuntu xenial main" | sudo tee /etc/apt/sources.list.d/xenial.list > /dev/null
  - |
    sudo tee -a /etc/apt/preferences.d/trusty > /dev/null <<EOF
    Package: *
    Pin: release n=xenial
    Pin-Priority: 1
    
    Package: make
    Pin: release n=xenial
    Pin-Priority: 500
    EOF
  - sudo apt-get update && sudo apt-get install make
script: ./configure --enable-debug --enable-cassert --enable-coverage --enable-tap-tests --with-tcl --with-python --with-perl --with-ldap --with-icu && make -j4 all contrib docs && make -Otarget -j3 check-world
after_success:
  - bash <(curl -s https://codecov.io/bash)
after_failure:
  - for f in ` find . -name regression.diffs ` ; do echo "========= Contents of $f" ; head -1000 $f ; done
  - |
    for corefile in $(find /tmp/ -name '*.core' 2>/dev/null) ; do
      binary=$(gdb -quiet -core $corefile -batch -ex 'info auxv' | grep AT_EXECFN | perl -pe "s/^.*\\"(.*)\\"\$/\$1/g")
      echo dumping $corefile for $binary
      gdb --batch --quiet -ex "thread apply all bt full" -ex "quit" $binary $corefile
    done
"""

