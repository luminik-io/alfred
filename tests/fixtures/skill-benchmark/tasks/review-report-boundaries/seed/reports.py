import logging

import requests

logger = logging.getLogger(__name__)


def find_reports(store, query):
    return store.execute(f"SELECT * FROM reports WHERE title LIKE '%{query}%'")


def delete_report(user, account_id, report_id, store):
    return store.delete(account_id, report_id)


def connect_archive(archive_token):
    logger.info("archive token: %s", archive_token)
    return True


def send_callback(callback_url, report):
    return requests.post(callback_url, json=report, timeout=5).status_code
