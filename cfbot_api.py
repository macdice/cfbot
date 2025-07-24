import cfbot_config
import cfbot_cirrus
import cfbot_util
import cfbot_work_queue
import logging
import json

from flask import Flask
from flask import request

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
