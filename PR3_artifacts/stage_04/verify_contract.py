#!/usr/bin/env python3
"""Independent HTTP functional contract checker for the proposed PR3 bench.

Standard library only. This is a real runnable verifier, but it requires a
running implementation of the REST SUT; it does not start/build the stand.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def expected_digest(data: bytes, iterations: int) -> str:
    state = data
    for _ in range(iterations):
        state = hashlib.sha256(state).digest()
    return state.hex()


def request_json(base_url: str, endpoint: str, timeout: float) -> tuple[int, str, object]:
    url = base_url.rstrip('/') + '/' + endpoint.lstrip('/')
    req = Request(url, headers={'Accept': 'application/json', 'Accept-Encoding': 'identity'})
    try:
        with urlopen(req, timeout=timeout) as response:
            status = response.status
            content_type = response.headers.get('Content-Type', '')
            body = response.read(1024 * 1024 + 1)
    except HTTPError as exc:
        raise ValueError(f'{endpoint}: HTTP {exc.code}, expected 200') from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ValueError(f'{endpoint}: HTTP request failed: {exc}') from exc
    if len(body) > 1024 * 1024:
        raise ValueError(f'{endpoint}: unexpectedly large body')
    try:
        payload = json.loads(body.decode('utf-8'))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(f'{endpoint}: response is not UTF-8 JSON') from exc
    return status, content_type, payload


def run_checks(base_url: str, scenario: str, iterations: int | None,
               fixture: Path, repeat: int, timeout: float) -> list[dict]:
    checks = []
    data = fixture.read_bytes()
    if data != bytes(32):
        raise ValueError(f'fixture must contain exactly 32 zero bytes, found {len(data)} bytes / wrong content')
    tasks = (['io', 'cpu'] if scenario == 'both' else [scenario])
    for target in tasks:
        for sample in range(1, repeat + 1):
            check = {'endpoint': '/' + target, 'sample': sample, 'ok': False, 'reason': None}
            try:
                status, content_type, payload = request_json(base_url, target, timeout)
                if status != 200:
                    raise ValueError(f'HTTP status {status} != 200')
                if content_type.partition(';')[0].strip().lower() != 'application/json':
                    raise ValueError(f'content-type {content_type!r} != application/json')
                if not isinstance(payload, dict):
                    raise ValueError('JSON root is not an object')
                if target == 'io':
                    expected = {'scenario': 'io', 'payload': 'OK'}
                else:
                    if iterations is None or iterations < 1:
                        raise ValueError('--iterations must be a positive integer for /cpu')
                    expected = {
                        'scenario': 'cpu',
                        'digest': expected_digest(data, iterations),
                        'iterations': iterations,
                    }
                if payload != expected:
                    raise ValueError(f'payload mismatch: actual={payload!r}, expected={expected!r}')
                check['ok'] = True
            except (ValueError, OSError) as exc:
                check['reason'] = str(exc)
            checks.append(check)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='SUT base URL, e.g. http://localhost:8080')
    parser.add_argument('--scenario', choices=['io', 'cpu', 'both'], default='both')
    parser.add_argument('--iterations', type=int, help='Configured SHA-256 chain length, required for cpu/both')
    parser.add_argument('--fixture', type=Path, default=Path(__file__).parent / 'fixtures' / 'cpu_input.bin')
    parser.add_argument('--repeat', type=int, default=3, help='Number of functional checks per endpoint')
    parser.add_argument('--timeout', type=float, default=2.0, help='HTTP client timeout, seconds')
    parser.add_argument('--output', type=Path, help='Machine-readable JSON results')
    args = parser.parse_args()
    if args.repeat < 1 or args.timeout <= 0:
        parser.error('--repeat and --timeout must be positive')
    if args.scenario in ('cpu', 'both') and (args.iterations is None or args.iterations < 1):
        parser.error('--iterations must be a positive integer for cpu/both')
    try:
        checks = run_checks(args.base_url, args.scenario, args.iterations,
                            args.fixture, args.repeat, args.timeout)
        result = {
            'schema_version': 1,
            'timestamp_utc': datetime.now(timezone.utc).isoformat(),
            'base_url': args.base_url,
            'scenario': args.scenario,
            'iterations': args.iterations,
            'fixture_sha256': hashlib.sha256(args.fixture.read_bytes()).hexdigest(),
            'checks': checks,
            'status': 'pass' if all(c['ok'] for c in checks) else 'fail',
        }
    except (OSError, ValueError) as exc:
        result = {'schema_version': 1, 'status': 'fail', 'fatal': str(exc), 'checks': []}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
