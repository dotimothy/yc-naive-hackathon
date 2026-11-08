#!/usr/bin/env python3
"""Serve the project website with byte-range support for seeking in demo videos."""

import argparse
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def create_app(directory=ROOT / "docs/html"):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/", StaticFiles(directory=directory, html=True), name="website")
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)
