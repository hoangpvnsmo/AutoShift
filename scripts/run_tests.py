"""UTF-8 test runner retaining exact result counts and elapsed time."""
import json
from pathlib import Path
import sys
import time
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
sys.stderr = sys.stdout
start = time.perf_counter()
suite = unittest.defaultTestLoader.discover('tests', pattern=sys.argv[1] if len(sys.argv)>1 else 'test*.py')
result = unittest.TextTestRunner(verbosity=2).run(suite)
raw = dict(tests=result.testsRun, failures=len(result.failures), errors=len(result.errors), skipped=len(result.skipped),
           seconds=round(time.perf_counter()-start, 2), success=result.wasSuccessful())
print(json.dumps(raw))
sys.exit(not result.wasSuccessful())
