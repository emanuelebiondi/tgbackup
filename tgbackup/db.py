"""
===============================================================================
Project      : TGBackup
File         : tgbackup/db.py
Description  : Local SQLite database manager (Vault DB) for TGBackup.
Purpose      : Maintains persistent snapshot history, mapping chunk parts (.partXXX)
               to Telegram message IDs, topic IDs, SHA-256 hashes, and metadata.
               Includes export/import functions for zero-loss Disaster Recovery
               and layered incremental chain resolution.
===============================================================================
"""

import os
import json
import aiosqlite
from typing import List, Dict, Optional, Any
from datetime import datetime

DEFAULT_DB_PATH = os.path.expanduser("~/.local/share/tgbackup/vault.db")


class VaultDB:
    """
    ---------------------------------------------------------------------------
    Class: VaultDB
    Description:
        Asynchronous interface to interact with the local SQLite database.
    ---------------------------------------------------------------------------
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self):
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._init_schema()

    async def close(self):
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _init_schema(self):
        await self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                profile TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                uncompressed_bytes INTEGER,
                compressed_bytes INTEGER,
                total_files INTEGER,
                total_parts INTEGER,
                manifest_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS parts (
                part_name TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL,
                message_id INTEGER,
                topic_id INTEGER,
                size INTEGER,
                sha256 TEXT,
                bot_idx INTEGER,
                uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_snapshots_profile ON snapshots(profile);
            CREATE INDEX IF NOT EXISTS idx_parts_snapshot ON parts(snapshot_id);
        """)
        await self._conn.commit()

    async def save_snapshot(self, manifest: dict) -> str:
        snapshot_id = manifest["snapshot_id"]
        profile = manifest["profile"]
        timestamp = manifest["created_at"]
        uncompressed = manifest.get("uncompressed_bytes", 0)
        compressed = manifest.get("compressed_bytes", 0)
        total_files = manifest.get("total_files", 0)
        total_parts = manifest.get("total_parts", 0)
        manifest_str = json.dumps(manifest)

        await self._conn.execute(
            """
            INSERT OR REPLACE INTO snapshots 
            (id, profile, timestamp, uncompressed_bytes, compressed_bytes, total_files, total_parts, manifest_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (snapshot_id, profile, timestamp, uncompressed, compressed, total_files, total_parts, manifest_str)
        )
        await self._conn.commit()
        return snapshot_id

    async def record_uploaded_part(
        self,
        part_name: str,
        snapshot_id: str,
        message_id: int,
        topic_id: int,
        size: int,
        sha256: str,
        bot_idx: int
    ):
        await self._conn.execute(
            """
            INSERT OR REPLACE INTO parts
            (part_name, snapshot_id, message_id, topic_id, size, sha256, bot_idx)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (part_name, snapshot_id, message_id, topic_id, size, sha256, bot_idx)
        )
        await self._conn.commit()

    async def get_snapshots(self, profile: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM snapshots"
        params = []
        if profile:
            query += " WHERE profile = ?"
            params.append(profile)
        query += " ORDER BY timestamp DESC"

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_latest_snapshot(self, profile: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM snapshots WHERE profile = ? ORDER BY timestamp DESC LIMIT 1",
            (profile,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_snapshot(self, snapshot_id: str) -> Optional[Dict[str, Any]]:
        async with self._conn.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_snapshot_parts(self, snapshot_id: str) -> List[Dict[str, Any]]:
        async with self._conn.execute(
            "SELECT * FROM parts WHERE snapshot_id = ? ORDER BY part_name ASC", (snapshot_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_parts_for_manifest(self, manifest: dict) -> List[Dict[str, Any]]:
        """
        -----------------------------------------------------------------------
        Method: get_all_parts_for_manifest
        Description:
            Returns all chunk records required to reconstruct a snapshot
            (including base parts for incremental snapshots).
        -----------------------------------------------------------------------
        """
        all_part_names = manifest.get("all_parts", [p["filename"] for p in manifest.get("parts", [])])
        if not all_part_names:
            return []

        placeholders = ",".join("?" for _ in all_part_names)
        query = f"SELECT * FROM parts WHERE part_name IN ({placeholders}) ORDER BY part_name ASC"
        async with self._conn.execute(query, all_part_names) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_parts(self) -> List[Dict[str, Any]]:
        async with self._conn.execute("SELECT * FROM parts ORDER BY part_name ASC") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def export_catalog(self) -> Dict[str, Any]:
        """
        -----------------------------------------------------------------------
        Method: export_catalog
        Description:
            Exports the entire local database into a dictionary ready to be
            serialized, encrypted, and pinned on Telegram.
        -----------------------------------------------------------------------
        """
        snaps = await self.get_snapshots()
        parts = await self.get_all_parts()
        return {
            "version": "1.0",
            "exported_at": datetime.now().isoformat(),
            "snapshots": snaps,
            "parts": parts
        }

    async def import_catalog(self, catalog: Dict[str, Any]):
        """
        -----------------------------------------------------------------------
        Method: import_catalog
        Description:
            Restores snapshots and parts tables from an exported catalog dictionary,
            enabling full Disaster Recovery from scratch.
        -----------------------------------------------------------------------
        """
        for s in catalog.get("snapshots", []):
            await self._conn.execute(
                """
                INSERT OR REPLACE INTO snapshots 
                (id, profile, timestamp, uncompressed_bytes, compressed_bytes, total_files, total_parts, manifest_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    s["id"], s["profile"], s["timestamp"],
                    s.get("uncompressed_bytes", 0), s.get("compressed_bytes", 0),
                    s.get("total_files", 0), s.get("total_parts", 0),
                    s.get("manifest_json", "{}")
                )
            )

        for p in catalog.get("parts", []):
            await self._conn.execute(
                """
                INSERT OR REPLACE INTO parts
                (part_name, snapshot_id, message_id, topic_id, size, sha256, bot_idx)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p["part_name"], p["snapshot_id"], p["message_id"],
                    p.get("topic_id", 0), p.get("size", 0),
                    p.get("sha256", ""), p.get("bot_idx", 0)
                )
            )
        await self._conn.commit()

    async def delete_snapshot(self, snapshot_id: str):
        await self._conn.execute("DELETE FROM parts WHERE snapshot_id = ?", (snapshot_id,))
        await self._conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        await self._conn.commit()
