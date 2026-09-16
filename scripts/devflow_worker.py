#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from devflow.db import DB
from devflow.worker import Worker
import os
db=DB(os.getenv('DEVFLOW_DB','runtime/devflow.sqlite3'))
Worker(db).run_forever()
