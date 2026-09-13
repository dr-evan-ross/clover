#!/usr/bin/env python3
"""Convenience entry point: python3 bridge/run_bridge.py --adapter stub"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clover_bridge.server import main
if __name__ == "__main__":
    main()
