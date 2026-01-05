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


def url_looks_like_patch(url):
    return "/nocfbot" not in url and re.match(
        r"https://.*\.(diff|patch)(\.gz|\.bz2)?$", url
    )


def url_looks_like_patch_tarball(url):
    return "/nocfbot" not in url and re.match(
        r"https://.*\.(tar|tgz|tar\.gz|tar\.bz2|zip)$", url
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
            '<a href="(/message-id/attachment/[^"]*)">',
            line,
        )
        if groups:
            attachment = groups.group(1)
            url = "https://www.postgres.org" + attachment
            if url_looks_like_patch(url) or url_looks_like_patch_tarball(url):
                message_attachments.append(url)
                selected_message_attachments = message_attachments
                selected_message_id = message_id

        # start of a new message?
        groups = re.search('<td><a href="/message-id/[^"]+">([^"]+)</a></td>', line)
        if groups:
            message_id = groups.group(1)
            message_attachments = []

    if selected_message_attachments is not None:
        if any(
            url_looks_like_patch_tarball(url) for url in selected_message_attachments
        ):
            # there is a tarball.  we don't actually know if it contains any
            # patches (rather than, say, benchmark results).  this is stupid,
            # but we'll try to guess...
            #
            # XXX the basic problem here is that we can't peek into the
            # tarballs and see if they contain patches, which is a bit sad;
            # perhaps we should just take everything, and teach the patch
            # burner script to examine everything and fail with a special
            # result code for 'nothing to do here' if it can't find any
            # patches?  the point of that would be to avoid running any code
            # that downloads and unpacks stuff outside the container, since we
            # don't really have enough information here but also don't want to
            # touch untrusted data here
            if any(url_looks_like_patch(url) for url in selected_message_attachments):
                # mixture of tarballs and patches, keep only the patches (not
                # great as it would be nice to be able to post a tarball + an
                # extra plain patch)
                selected_message_attachments = list(
                    filter(url_looks_like_patch, selected_message_attachments)
                )
            elif len(selected_message_attachments) > 1:
                # tarball-only, multi-tarball messages not currently supported
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
    submission_page = cfbot_util.slow_fetch(url, none_for_404=True)

    if submission_page is None:
        return None

    for line in submission_page.splitlines():
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
        if re.search("<td[^>]*>", line):
            td_count += 1

        groups = re.search('<a href="/patch/([0-9]+)/">([^<]+)</a>', line)
        if groups:
            submission_id = groups.group(1)
            name = html.unescape(groups.group(2))
            continue
        if td_count == 8:
            groups = re.search("<td>([^<]*)</td>", line)
            if groups:
                authors = groups.group(1)
                authors = re.sub(" *\\([^)]*\\)", "", authors)
                continue
        if td_count == 3:
            groups = re.search(
                '<td><span class="badge[^"]*">([^<]+)</span></td>',
                line,
                #            '<td><span class="label label-[^"]*">([^<]+)</span></td>', line
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
    # test case
    print(
        get_latest_patches_from_thread_url(
            "https://www.postgresql.org/message-id/flat/CAApHDvrF6DG7=xD8JGo2HoQKN0LRFNF0ysVt6cKSNPiqbdQOSA@mail.gmail.com"
        )
    )
