#!/usr/bin/env python
#
# Routines that interface with the Commitfest app.

from datetime import datetime

import cfbot_config
import cfbot_util
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
    url = f"{cfbot_config.COMMITFEST_HOST}/api/v1/patches/{submission_id}/threads"
    data = cfbot_util.slow_fetch_json(url, none_for_404=True)

    if data is None:
        return None

    # Filter to threads that have attachments, then pick the one with the most
    # recent message
    candidates = [
        (t["latest_message_time"], t["messageid"])
        for t in data["threads"]
        if t["has_attachment"]
    ]

    if not candidates:
        return None

    candidates.sort()
    return "https://www.postgresql.org/message-id/flat/" + candidates[-1][1]


def get_submissions_for_commitfest(commitfest_id):
    """Given a Commitfest ID, return a list of Submission objects."""
    url = f"{cfbot_config.COMMITFEST_HOST}/api/v1/commitfests/{commitfest_id}/patches"
    data = cfbot_util.slow_fetch_json(url, none_for_404=True)

    if data is None:
        return []

    return [
        Submission(
            p["id"],
            commitfest_id,
            p["name"],
            p["status"],
            p["authors"],
            p["last_email_time"],
        )
        for p in data["patches"]
    ]


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
