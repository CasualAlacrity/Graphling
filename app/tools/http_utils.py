import time

import requests

RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30


def get_with_retries(url: str, headers: dict, params: dict | None = None) -> requests.Response:
    response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    for attempt in range(1, RETRY_ATTEMPTS):
        if response.status_code < 500:
            return response
        time.sleep(RETRY_BACKOFF_SECONDS * attempt)
        response = requests.get(url, headers=headers, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
    return response
