"""Replace this stub with task-specific validation and measurement.

Input: {"candidate": ..., "problem": ...}.
Successful output: {"accepted": true, "score": NUMBER, "details": {...}}.
Accept candidates only after independently checking required properties.
The configured initial candidate is checked before any member uses inference.
"""
import json
import sys

json.load(sys.stdin)
print(json.dumps({"accepted": False, "reason": "Configure this evaluator for the intended task."}))
