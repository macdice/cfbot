#!/usr/bin/env python
#
# Routines that interface with the Commitfest app.
# For now these use webscraping, but they could become real API calls.

import cfbot_config
import cfbot_util
import html

# from html.parser import HTMLParser
import re


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
    if selected_message_attachments is not None:
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
    url = f"{cfbot_config.COMMITFEST_HOST}/patch/{submission_id}/"
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
    url = f"{cfbot_config.COMMITFEST_HOST}/{commitfest_id}/"
    state = None
    latest_email = None
    authors = ""
    td_count = 0
    body = cfbot_util.slow_fetch(url, True)
    if body is None:
        return []
    for line in body.splitlines():
        # maybe it's easier to count rows and columns
        if re.search("<tr>", line):
            td_count = 0
            continue
        if re.search("<td>", line):
            td_count += 1

        groups = re.search('<a href="/patch/([0-9]+)/">([^<]+)</a>', line)
        if groups:
            submission_id = groups.group(1)
            name = html.unescape(groups.group(2))
            continue
        if td_count == 6:
            groups = re.search("<td>([^<]*)</td>", line)
            if groups:
                authors = groups.group(1)
                authors = re.sub(" *\\([^)]*\\)", "", authors)
                continue
        groups = re.search(
            '<td><span class="label label-[^"]*">([^<]+)</span></td>', line
        )
        if groups and not state:
            state = groups.group(1)
            continue
        groups = re.search('<td style="white-space: nowrap;" title="([^"]+)">', line)
        if groups:
            latest_email = groups.group(1)
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
            state = None
    return result


def get_current_commitfests():
    """Find the ID of the current open or next future Commitfest."""
    data = cfbot_util.slow_fetch_json(
        f"{cfbot_config.COMMITFEST_HOST}/api/v1/commitfests/needs_ci"
    )
    return data["commitfests"]


if __name__ == "__main__":
    for name, cf in get_current_commitfests().items():
        if not cf:
            continue
        for sub in get_submissions_for_commitfest(cf["id"]):
            print(name, str(sub))
    #    print get_thread_url_for_submission(19, 1787)
    # print(get_latest_patches_from_thread_url(get_thread_url_for_submission(37, 2901)))
