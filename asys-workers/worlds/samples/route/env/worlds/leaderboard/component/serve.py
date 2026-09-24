"""Serve the leaderboard world component."""
from worlds.common import serve

if __name__ == "__main__":
    serve('leaderboard', ["python3", "/opt/world/evaluate.py"])
