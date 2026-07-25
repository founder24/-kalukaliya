#!/bin/bash
# Wrapper so nohup has a concrete file to run (not a bash -c string)
export PYTHONPATH=/home/runner/workspace/apps/backend
exec python3 /home/runner/workspace/apps/backend/scripts/retranslate_assamese.py --concurrency 3
