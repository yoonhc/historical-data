"""One bounded HTTPS GET for the exact T17 CHECKSUM; never invokes ingestion.

Writes only a new attempt directory beside this script. No automatic retries,
redirects, DB writes, service commands, or credentials are used.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path


NAME = 'BTCUSDT-metrics-2026-10-15.zip'
URL = 'https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/' + NAME + '.CHECKSUM'
ORIGINAL_ZIP = '5f1c31bdf645489221d120079529bd311168258ef524a669e88908db22139d90'
ORIGINAL_CHECKSUM = 'e83db5cde9085b39e8545c7c620f4de5044fae82648ad61967e6113c828524e5'
REPORT = Path(__file__).resolve().parent


def now():
    return datetime.now(timezone.utc).isoformat()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def main():
    folder = REPORT / ('attempt-' + uuid.uuid4().hex)
    folder.mkdir()
    result = {
        'started_at': now(), 'timestamp_basis': 'host UTC wall clock; not a Chrony interval',
        'url': URL, 'method': 'GET', 'timeout_seconds': 20, 'max_body_bytes': 4096,
        'request_headers': {'User-Agent': 'marketdata-t17-source-review/1',
                            'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'},
        'automatic_retries': 0, 'redirects_followed': 0,
        'operating_mutations_requested': False, 'zip_download_requested': False,
        'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'passed': False,
    }
    started = time.monotonic()
    try:
        request = urllib.request.Request(URL, headers=result['request_headers'], method='GET')
        opener = urllib.request.build_opener(NoRedirect())
        try:
            response = opener.open(request, timeout=20)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            result.update(status=response.status, response_url=response.geturl(),
                          response_headers=list(response.headers.items()),
                          headers_received_at=now())
            body = response.read(4097)
        if len(body) > 4096:
            raise ValueError('CHECKSUM response exceeded the fixed size limit')
        (folder / 'response.body').write_bytes(body)
        result.update(body_bytes=len(body), body_sha256=hashlib.sha256(body).hexdigest())
        if result['status'] != 200:
            raise ValueError('CHECKSUM HTTP status is not 200')
        if result['response_url'] != URL:
            raise ValueError('Unexpected final URL')
        match = re.fullmatch(rb'([0-9a-fA-F]{64})[ \t]+\*?([^\r\n]+)\r?\n?', body)
        if not match or match.group(2).decode('ascii') != NAME:
            raise ValueError('Invalid CHECKSUM syntax or filename')
        checksum = match.group(1).decode('ascii').lower()
        result.update(
            advertised_zip_sha256=checksum, expected_filename=NAME,
            original_zip_sha256=ORIGINAL_ZIP,
            same_advertised_zip=checksum == ORIGINAL_ZIP,
            same_checksum_bytes=result['body_sha256'] == ORIGINAL_CHECKSUM,
            passed=True,
            scope='Observed CHECKSUM response only; current ZIP bytes were not downloaded',
        )
    except Exception as exc:
        result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
    finally:
        result.update(finished_at=now(), elapsed_monotonic_seconds=time.monotonic() - started)
        (folder / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('passed', 'status', 'same_advertised_zip',
                                           'same_checksum_bytes', 'error') if k in result} |
                     {'report': str(folder / 'result.json')}, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
