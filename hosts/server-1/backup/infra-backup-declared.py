#!/usr/bin/env python3
"""Compatibility entrypoint; all backup logic lives in the shared directory."""
import os
from pathlib import Path
import sys
os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve().parents[3] / 'backup/infra-backup-declared.py'), *sys.argv[1:]])
