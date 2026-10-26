"""Run the SafeSound server:  python -m safesound.server [--host 0.0.0.0] [--port 8000]"""
import argparse

import uvicorn

from .app import create_app

ap = argparse.ArgumentParser()
ap.add_argument("--host", default="127.0.0.1")
ap.add_argument("--port", type=int, default=8000)
ap.add_argument("--db", default=None, help="SQLite file (default: $SAFESOUND_DB or ./safesound.db)")
ap.add_argument("--no-demo", action="store_true", help="do not seed the demo school")
args = ap.parse_args()
uvicorn.run(create_app(args.db, demo=not args.no_demo), host=args.host, port=args.port)
