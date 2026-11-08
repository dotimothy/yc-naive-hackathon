"""Durable cancellation marker shared by the API and its analysis worker."""

from pathlib import Path

MARKER = ".cancel_requested"


class AnalysisCancelled(Exception):
    pass


def check_cancelled(path):
    if path and Path(path).exists():
        raise AnalysisCancelled("Analysis cancelled by user")
