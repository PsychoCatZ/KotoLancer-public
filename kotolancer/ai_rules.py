import hashlib
from pathlib import Path

RULES_VERSION = "consultant-rules-v4"
RULES_PATH = Path(__file__).parent / "data" / "consultant_rules_v4.txt"


def rules_snapshot():
    text = RULES_PATH.read_text(encoding="utf-8")
    return {
        "version": RULES_VERSION,
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "text": text,
    }
