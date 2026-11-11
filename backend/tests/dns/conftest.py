import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

# Other CI jobs run the whole backend test suite without the DNS libraries;
# the dns-toolbox job installs them and runs these tests.
_missing = [m for m in ("dns", "httpx", "cryptography") if importlib.util.find_spec(m) is None]
collect_ignore_glob = ["test_*.py"] if _missing else []
