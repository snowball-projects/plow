"""Small shared contracts for the bounded pilot."""
import gzip
import hashlib
import json
import math
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'
OUT = ROOT / 'output'
SEED = 20261029
LIMIT_SECONDS = 5400
GRID_METRES = 2000


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n')


def download(url, path, expected=None, algorithm='sha256', expected_bytes=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.new(algorithm)
    size = 0
    with urllib.request.urlopen(url, timeout=120) as response, path.open('wb') as target:
        while chunk := response.read(1024 * 1024):
            target.write(chunk)
            h.update(chunk)
            size += len(chunk)
    if expected_bytes is not None and size != expected_bytes:
        raise ValueError(f'Input size mismatch for {url}')
    if expected is not None and h.hexdigest() != expected:
        raise ValueError(f'Input checksum mismatch for {url}')
    return {'url': url, 'bytes': size, algorithm: h.hexdigest(),
            'sha256': hashlib.file_digest(path.open('rb'), 'sha256').hexdigest()}


def whole_minutes(seconds):
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError('Duration must be finite and nonnegative')
    return math.floor(seconds / 60 + 0.5)


def nearest10(values):
    """Ascending duration then immutable catalog ID, including encoded-minute ties."""
    return [key for key, _ in sorted(values.items(), key=lambda x: (x[1], x[0]))[:10]]


def gzip_file(path):
    with path.open('rb') as src, path.with_name(path.name + '.gz').open('wb') as dst:
        with gzip.GzipFile(filename='', fileobj=dst, mode='wb', mtime=0) as compressed:
            while chunk := src.read(1024 * 1024):
                compressed.write(chunk)
    return {'raw_bytes': path.stat().st_size,
            'gzip_bytes': path.with_name(path.name + '.gz').stat().st_size}
