#!/usr/bin/env python3
"""Run the local toroidal artifact world."""
from pathlib import Path
import sys

directory = Path(__file__).resolve()
sys.path.insert(0, str(directory.parents[2]))
sys.path.insert(0, str(directory.parents[3] / 'asys-runtime'))
from worlds.common import main

if __name__ == '__main__':
    main('torus')
