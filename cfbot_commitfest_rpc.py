#!/usr/bin/env python
#
# Routines that interface with the Commitfest app.
# For now these use webscraping, but they could become real API calls.

import cfbot_util
import datetime
import errno
import html

# from html.parser import HTMLParser
import os
import re
import requests
import subprocess
import shutil
import sys
import tarfile
import time
import unicodedata
from urllib.parse import urlparse


class Submission:
    """A submission in a Commitfest."""

    def __init__(
        self, submission_id, commitfest_id, name, status, authors, last_email_time
    ):
        self.id = int(submission_id)
        self.commitfest_id = commitfest_id
        self.name = name
        self.status = status
        self.authors = authors
        self.last_email_time = last_email_time
        self.build_results = []

    def __str__(self):
        return str(
            [self.id, self.name, self.status, self.authors, self.last_email_time]
        )


def get_latest_patches_from_thread_url(thread_url):
    """Given a 'whole thread' URL from the archives, find the last message that
    had at least one attachment called something.patch.  Return the message
    ID and the list of URLs to fetch all the patches."""
    selected_message_attachments = []
    selected_message_id = None
    message_attachments = []
    message_id = None
    for line in cfbot_util.slow_fetch(thread_url).splitlines():
        groups = re.search(
            '<a href="(/message-id/attachment/[^"]*\\.(diff|diff\\.gz|patch|patch\\.gz|tar\\.gz|tgz|tar\\.bz2|zip))">',
            line,
        )
        if groups and not groups.group(1).endswith("subtrans-benchmark.tar.gz"):
            message_attachments.append("https://www.postgresql.org" + groups.group(1))
            selected_message_attachments = message_attachments
            selected_message_id = message_id
        # groups = re.search('<a name="([^"]+)"></a>', line)
        groups = re.search('<td><a href="/message-id/[^"]+">([^"]+)</a></td>', line)
        if groups:
            message_id = groups.group(1)
            message_attachments = []
    # if there is a tarball attachment, there must be only one attachment,
    # otherwise give up on this thread (we don't know how to combine patches and
    # tarballs)
    if selected_message_attachments != None:
        if any(
            x.endswith(".tgz") or x.endswith(".tar.gz") or x.endswith(".tar.bz2")
            for x in selected_message_attachments
        ):
            if len(selected_message_attachments) > 1:
                selected_message_id = None
                selected_message_attachments = None
    # if there are multiple patch files, they had better follow the convention
    # of leading numbers, otherwise we don't know how to apply them in the right
    # order
    return selected_message_id, selected_message_attachments


def get_thread_url_for_submission(commitfest_id, submission_id):
    """Given a Commitfest ID and a submission ID, return the URL of the 'whole
    thread' page in the mailing list archives."""
    # find all the threads and latest message times
    result = None
    url = "https://commitfest.postgresql.org/%s/%s/" % (commitfest_id, submission_id)
    candidates = []
    candidate = None
    for line in cfbot_util.slow_fetch(url).splitlines():
        groups = re.search(
            """Latest at <a href="https://www.postgresql.org/message-id/([^"]+)">(2[^<]+)""",
            line,
        )
        if groups:
            candidate = (groups.group(2), groups.group(1))
        # we'll only take threads that are followed by evidence that there is at least one attachment
        groups = re.search("""Latest attachment .* <button type="button" """, line)
        if groups:
            candidates.append(candidate)
    # take the one with the most recent email
    if len(candidates) > 0:
        candidates.sort()
        result = "https://www.postgresql.org/message-id/flat/" + candidates[-1][1]
    return result


def get_submissions_for_commitfest(commitfest_id):
    """Given a Commitfest ID, return a list of Submission objects."""
    result = []
    # parser = HTMLParser()
    url = "https://commitfest.postgresql.org/%s/" % (commitfest_id,)
    next_line_has_version = False
    next_line_has_authors = False
    next_line_has_latest_email = False
    state = None
    latest_email = None
    authors = ""
    for line in cfbot_util.slow_fetch(url).splitlines():
        groups = re.search('<a href="/patch/([0-9]+)/">([^<]+)</a>', line)
        if groups:
            submission_id = groups.group(1)
            name = html.unescape(groups.group(2))
        if next_line_has_version:
            next_line_has_version = False
            next_line_has_authors = True
            continue
        if next_line_has_authors:
            next_line_has_authors = False
            groups = re.search("<td>([^<]*)</td>", line)
            if groups:
                authors = groups.group(1)
                authors = re.sub(" *\\([^)]*\\)", "", authors)
                continue
        if next_line_has_latest_email:
            next_line_has_latest_email = False
            groups = re.search(
                '<td style="white-space: nowrap;">(.*)<br/>(.*)</td>', line
            )
            if groups:
                latest_email = groups.group(1) + " " + groups.group(2)
                if latest_email == " ":
                    latest_email = None
                result.append(
                    Submission(
                        submission_id,
                        commitfest_id,
                        name,
                        state,
                        authors.split(", "),
                        latest_email,
                    )
                )
        groups = re.search(
            '<td><span class="label label-[^"]*">([^<]+)</span></td>', line
        )
        if groups:
            state = groups.group(1)
            next_line_has_version = True
            continue
        groups = re.search('<td style="white-space: nowrap;">.*<br/>.*</td>', line)
        if groups:
            next_line_has_latest_email = True
            continue
        next_line_has_authors = False
        next_line_has_latest_email = False
    return result


def get_current_commitfest_id():
    """Find the ID of the current open or next future Commitfest."""
    result = None
    for line in cfbot_util.slow_fetch("https://commitfest.postgresql.org").splitlines():
        groups = re.search(
            '<a href="/([0-9]+)/">[0-9]+-[0-9]+</a> \((Open|In Progress) ', line
        )
        if groups:
            commitfest_id = groups.group(1)
            state = groups.group(2)
            result = int(commitfest_id)
    if result == None:
        raise Exception("Could not determine the current Commitfest ID")
    return result


if __name__ == "__main__":
    # for sub in get_submissions_for_commitfest(get_current_commitfest_id()):
    #  print str(sub)
    # print get_thread_url_for_submission(19, 1787)
    print(get_latest_patches_from_thread_url(get_thread_url_for_submission(37, 2901)))
