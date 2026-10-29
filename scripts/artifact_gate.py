"""No upload unless the caller has reserved enough verified free shared storage."""
import sys
from pathlib import Path

budget = int(sys.argv[1])
if not 0 <= budget <= 100_000_000:
    raise SystemExit('Budget must be between 0 and 100 MB; verify shared allowance first')
size = sum(p.stat().st_size for p in Path('output').rglob('*') if p.is_file())
# ZIP overhead is bounded generously for the small number of output files.
files = sum(p.is_file() for p in Path('output').rglob('*'))
reserve = size + files * 4096 + 65536
print(f'upload={str(0 < reserve <= budget).lower()}')
print(f'reserved_bytes={reserve}')
if not 0 < reserve <= budget:
    print(f'Artifact upload withheld: needs {reserve} bytes of verified free headroom.', file=sys.stderr)
