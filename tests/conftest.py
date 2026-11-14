"""The tests run on the stand-in catalog and its rule set, whatever valio-challenge/.env says.

backend/app.py reads .env and keeps a value that the environment already holds. So these lines
win over a .env that points the backend at the real Valio catalog. A test that needs another
value sets it with monkeypatch.
"""

import os
from pathlib import Path

os.environ["CATALOG"] = "local"
os.environ["RULES_FILE"] = str(Path(__file__).resolve().parent.parent / "rules" / "school_kitchen.yaml")
os.environ["SHOW_PINS"] = ""  # a PIN on the page is for a demo only
