#!/usr/bin/env python3
"""Example worker: read JSON input and return it alongside the program arguments."""
import json
import os
from pathlib import Path
import sys

text = sys.stdin.read()
result = {"input": json.loads(text) if text else None, "arguments": sys.argv[1:]}
Path(os.environ["ASYS_RESULT"]).write_text(json.dumps(result) + "\n")
print("Completed", os.environ["ASYS_JOB_ID"])
