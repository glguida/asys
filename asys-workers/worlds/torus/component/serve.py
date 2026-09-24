"""Serve the torus world component."""
from worlds.common import serve

if __name__ == "__main__":
    serve('torus', ["python3", "/opt/world/evaluate.py"])
