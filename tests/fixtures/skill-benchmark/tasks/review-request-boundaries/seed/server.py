import logging
import subprocess

import requests

logger = logging.getLogger(__name__)


def run_export(name):
    return subprocess.run(f"export-tool {name}", shell=True, check=False)


def read_org_secret(user, org_id, store):
    return store.secret_for(org_id)


def connect_provider(api_token):
    logger.info("connecting with token %s", api_token)
    return True


def fetch_preview(url):
    return requests.get(url, timeout=5).text
