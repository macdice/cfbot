FROM debian:latest
RUN apt-get --yes update && DEBIAN_FRONTEND=noninteractive apt-get --yes install gcc libreadline-dev flex bison make perl libipc-run-perl clang llvm-dev libperl-dev libpython-dev tcl-dev libldap2-dev libicu-dev docbook-xml docbook-xsl fop libxml2-utils xsltproc krb5-admin-server krb5-kdc krb5-user slapd ldap-utils libssl-dev pkg-config locales-all liblz4-dev
RUN useradd -m postgres
