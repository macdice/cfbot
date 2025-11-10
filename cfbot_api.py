import cfbot_cirrus
import cfbot_config
import cfbot_util
import logging
import secrets

from flask import Flask
from flask import request
from flask import jsonify

# extremely primitive approach to connection re-use; need to learn more flask
# philosophy and do better!
conn = cfbot_util.db()


def error_cleanup():
    try:
        conn.rollback()
    except:
        pass
    try:
        conn.close()
    except:
        pass
    conn = cfbot_util.db()


app = Flask("cfbot_api")


# This URL is registered with with cirrus so it calls us any time a build
# begins or a status changes:
#
# https://cirrus-ci.org/api/#builds-and-tasks-webhooks
#
# We extract commit_id:task_id and create a work_queue entry for later
# processing.
#
# XXX Should we process the status change immediately in this transaction?
#
@app.route("/api/cirrus-webhook", methods=["POST"])
def cirrus_webhook():
    try:
        event_type = request.headers.get("X-Cirrus-Event")
        event = request.json
        # logging.info("Cirrus webhook: type = %s, payload = %s", event_type, event)
        if event_type and "build" in event and "id" in event["build"]:
            cursor = conn.cursor()
            cfbot_cirrus.ingest_webhook(conn, event_type, event)
            conn.commit()
            return "OK"
        else:
            return "not understood"
    except:
        error_cleanup()
        logging.exception("Error processing webhook")
        return "NOT OK"


@app.route("/api/requeue-patch", methods=["POST"])
def rerun_patch():
    """API endpoint to requeue a specific patch for processing.

    This resets the last_branch_message_id so that the periodic job
    will pick it up and process it on the next run.

    Expected JSON payload:
    {
        "commitfest_id": 123,
        "submission_id": 456,
        "shared_secret": "secret_token"
    }
    """
    if not request.json:
        return jsonify({"error": "Invalid request, JSON payload required"}), 400

    # Extract parameters
    commitfest_id = request.json.get("commitfest_id")
    submission_id = request.json.get("submission_id")
    shared_secret = request.json.get("shared_secret")

    # Validate required parameters
    if not commitfest_id or not submission_id:
        return jsonify({"error": "commitfest_id and submission_id are required"}), 400

    # Check authentication if shared secret is configured
    if (
        hasattr(cfbot_config, "COMMITFEST_SHARED_SECRET")
        and cfbot_config.COMMITFEST_SHARED_SECRET
    ):
        if not shared_secret or not secrets.compare_digest(
            shared_secret, cfbot_config.COMMITFEST_SHARED_SECRET
        ):
            logging.warning(
                "Invalid shared secret for rerun request: cf=%s, sub=%s",
                commitfest_id,
                submission_id,
            )
            return jsonify({"error": "Invalid authentication"}), 403

    # Check if submission exists and get current state
    cursor = conn.cursor()
    cursor.execute(
        """SELECT name, last_message_id
           FROM submission
           WHERE commitfest_id = %s AND submission_id = %s""",
        (commitfest_id, submission_id),
    )
    row = cursor.fetchone()

    if not row:
        return jsonify({"error": "Submission not found"}), 404

    name, last_message_id = row

    # Check if there's actually a message to process
    if not last_message_id:
        return jsonify(
            {
                "error": "No patches found for this submission",
                "commitfest_id": commitfest_id,
                "submission_id": submission_id,
            }
        ), 400

    logging.info(
        "API request to requeue patch: cf=%s, sub=%s, name=%s",
        commitfest_id,
        submission_id,
        name,
    )

    # Reset last_branch_message_id to trigger reprocessing
    # This makes the submission appear as if it has a new patch that hasn't been built yet
    cursor.execute(
        """UPDATE submission
           SET last_branch_message_id = NULL,
               backoff_until = NULL
           WHERE commitfest_id = %s AND submission_id = %s""",
        (commitfest_id, submission_id),
    )
    conn.commit()

    return jsonify({"status": "success"})
