"""Backward-compatible import shim for the snapshot-backed store."""

from .snapshot_store import SnapshotStore, SnapshotStore as SQLiteStore

__all__ = ["SnapshotStore", "SQLiteStore"]
