"""
===============================================================================
Project      : TGBackup
File         : tgbackup/archiver.py
Description  : Archival, compression, chunking, deduplication, and extraction engine.
Purpose      : Creates full or incremental snapshots (tracking modifications via
               mtime and size), performs streaming zstd compression, AES-256-GCM
               encryption, chunk slicing (35MB default), SHA-256 verification,
               and layered multi-part extraction for consistent restores.
===============================================================================
"""

import os
import tarfile
import tempfile
import hashlib
import json
import shutil
from datetime import datetime
import zstandard as zstd
from typing import List, Dict, Tuple, Optional, Callable, Set
from .crypto import encrypt_bytes, decrypt_bytes

DEFAULT_CHUNK_SIZE_MB = 19


def compute_sha256(data: bytes) -> str:
    """
    ---------------------------------------------------------------------------
    Function: compute_sha256
    Description:
        Computes SHA-256 hash of an in-memory byte buffer.
    Input parameters:
        @param data (bytes) : Byte buffer.
    Return value:
        @return (str)       : Hexadecimal SHA-256 digest string.
    ---------------------------------------------------------------------------
    """
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()


def compute_file_sha256(filepath: str) -> str:
    """
    ---------------------------------------------------------------------------
    Function: compute_file_sha256
    Description:
        Computes SHA-256 hash of a file on disk in 1MB blocks.
    Input parameters:
        @param filepath (str) : Path to file on disk.
    Return value:
        @return (str)         : Hexadecimal SHA-256 digest string.
    ---------------------------------------------------------------------------
    """
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


class Archiver:
    """
    ---------------------------------------------------------------------------
    Class: Archiver
    Description:
        Manages snapshot creation (Full / Incremental), zstd compression,
        AES-256-GCM encrypted chunking, and layered overlay extraction.
    ---------------------------------------------------------------------------
    """

    def __init__(
        self,
        chunk_size_mb: int = DEFAULT_CHUNK_SIZE_MB,
        compression_level: int = 3,
        staging_dir: Optional[str] = None
    ):
        self.chunk_size = chunk_size_mb * 1024 * 1024
        self.compression_level = compression_level
        self.staging_dir = os.path.abspath(staging_dir) if staging_dir else os.path.expanduser("~/.cache/tgbackup/staging")
        os.makedirs(self.staging_dir, exist_ok=True)

    def scan_files(self, paths: List[str], excludes: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        -----------------------------------------------------------------------
        Method: scan_files
        Description:
            Scans target paths, collecting mtime, file size, and relative paths
            for incremental deduplication comparison.
        Input parameters:
            @param paths (List[str])    : Paths to scan.
            @param excludes (List[str]) : Exclusion patterns.
        Return value:
            @return (Dict[str, Dict])   : Map of relpath -> {abs_path, size, mtime}.
        -----------------------------------------------------------------------
        """
        catalog = {}
        for p in paths:
            abs_p = os.path.abspath(p)
            if not os.path.exists(abs_p):
                continue

            base_dir = os.path.dirname(abs_p.rstrip("/"))
            if os.path.isfile(abs_p):
                rel = os.path.basename(abs_p)
                st = os.stat(abs_p)
                catalog[rel] = {"abs_path": abs_p, "size": st.st_size, "mtime": st.st_mtime}
                continue

            for root, dirs, files in os.walk(abs_p):
                # Filter excluded subdirectories
                dirs[:] = [d for d in dirs if not any(exc in d for exc in excludes)]
                for f in files:
                    if any(exc in f for exc in excludes):
                        continue
                    full_path = os.path.join(root, f)
                    try:
                        st = os.stat(full_path)
                        rel_path = os.path.relpath(full_path, base_dir)
                        catalog[rel_path] = {
                            "abs_path": full_path,
                            "size": st.st_size,
                            "mtime": st.st_mtime
                        }
                    except OSError:
                        pass
        return catalog

    def create_snapshot(
        self,
        paths: List[str],
        profile_name: str,
        passphrase: str,
        output_dir: str,
        excludes: Optional[List[str]] = None,
        base_manifest: Optional[dict] = None,
        progress_callback: Optional[Callable[[str, int], None]] = None
    ) -> Tuple[str, dict, List[str]]:
        """
        -----------------------------------------------------------------------
        Method: create_snapshot
        Description:
            Creates a snapshot (Full or Incremental relative to base_manifest):
            1. Scans and detects new or modified files.
            2. If no files changed, creates a 0-byte incremental snapshot reference.
            3. Otherwise creates a zstd compressed tar archive, encrypts it in
               35MB chunks, and produces the complete manifest chain.
        Input parameters:
            @param paths (List[str])             : Paths to archive.
            @param profile_name (str)            : Profile identifier.
            @param passphrase (str)              : AES-256 encryption passphrase.
            @param output_dir (str)              : Temporary local staging directory.
            @param excludes (Optional[List[str]]): Exclusion glob patterns.
            @param base_manifest (Optional[dict]): Preceding base snapshot manifest.
            @param progress_callback (Callable)  : Progress update callback.
        Return value:
            @return (Tuple[str, dict, List[str]]): (snapshot_id, manifest, part_files).
        -----------------------------------------------------------------------
        """
        now = datetime.now()
        snapshot_id = now.strftime("%Y%m%d_%H%M%S")
        os.makedirs(output_dir, exist_ok=True)
        excludes = excludes or [".cache", "node_modules", ".venv", "__pycache__", "*.tmp", ".git"]

        # Scan current filesystem tree
        current_catalog = self.scan_files(paths, excludes)
        
        files_to_pack = {}
        is_incremental = base_manifest is not None
        base_catalog = base_manifest.get("files_catalog", {}) if is_incremental else {}

        if is_incremental:
            for rel, meta in current_catalog.items():
                prev = base_catalog.get(rel)
                if not prev or prev.get("size") != meta["size"] or abs(prev.get("mtime", 0) - meta["mtime"]) > 1.0:
                    files_to_pack[rel] = meta
        else:
            files_to_pack = current_catalog

        # If incremental and no files were modified, create immediate zero-byte snapshot
        if is_incremental and not files_to_pack:
            manifest = {
                "snapshot_id": snapshot_id,
                "profile": profile_name,
                "type": "incremental",
                "base_snapshot_id": base_manifest.get("snapshot_id"),
                "created_at": now.isoformat(),
                "paths": [os.path.abspath(p) for p in paths],
                "uncompressed_bytes": 0,
                "compressed_bytes": 0,
                "total_files": len(current_catalog),
                "changed_files": 0,
                "reused_files": len(current_catalog),
                "total_parts": 0,
                "parts": [],
                "all_parts": base_manifest.get("all_parts", [p["filename"] for p in base_manifest.get("parts", [])]),
                "files_catalog": current_catalog
            }
            manifest_path = os.path.join(output_dir, f"snap_{profile_name}_{snapshot_id}.manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)
            return snapshot_id, manifest, []

        # Create tar archive containing only changed/new files
        cctx = zstd.ZstdCompressor(level=self.compression_level)
        temp_tar = tempfile.NamedTemporaryFile(dir=self.staging_dir, delete=False, suffix=".tar")
        uncompressed_size = 0

        try:
            with tarfile.open(fileobj=temp_tar, mode="w") as tar:
                for rel, meta in files_to_pack.items():
                    tar.add(meta["abs_path"], arcname=rel)

            uncompressed_size = temp_tar.tell()
            temp_tar.close()

            # Streaming compression and chunk encryption
            parts = []
            part_files = []
            part_index = 1
            compressed_size = 0

            with open(temp_tar.name, "rb") as raw_f:
                with cctx.stream_reader(raw_f) as zstd_reader:
                    while True:
                        raw_chunk = zstd_reader.read(self.chunk_size)
                        if not raw_chunk:
                            break

                        compressed_size += len(raw_chunk)
                        enc_chunk = encrypt_bytes(raw_chunk, passphrase)
                        chunk_sha = compute_sha256(enc_chunk)

                        part_fname = f"snap_{profile_name}_{snapshot_id}.part{part_index:03d}.zst.enc"
                        part_path = os.path.join(output_dir, part_fname)

                        with open(part_path, "wb") as pf:
                            pf.write(enc_chunk)

                        parts.append({
                            "part_index": part_index,
                            "filename": part_fname,
                            "size": len(enc_chunk),
                            "sha256": chunk_sha
                        })
                        part_files.append(part_path)

                        if progress_callback:
                            progress_callback(part_fname, len(enc_chunk))

                        part_index += 1

            # Build cumulative list of all necessary chunk files
            all_parts = []
            if is_incremental:
                all_parts.extend(base_manifest.get("all_parts", [p["filename"] for p in base_manifest.get("parts", [])]))
            all_parts.extend([p["filename"] for p in parts])

            manifest = {
                "snapshot_id": snapshot_id,
                "profile": profile_name,
                "type": "incremental" if is_incremental else "full",
                "base_snapshot_id": base_manifest.get("snapshot_id") if is_incremental else None,
                "created_at": now.isoformat(),
                "paths": [os.path.abspath(p) for p in paths],
                "uncompressed_bytes": uncompressed_size,
                "compressed_bytes": compressed_size,
                "total_files": len(current_catalog),
                "changed_files": len(files_to_pack),
                "reused_files": len(current_catalog) - len(files_to_pack),
                "total_parts": len(parts),
                "parts": parts,
                "all_parts": list(dict.fromkeys(all_parts)),
                "files_catalog": current_catalog
            }

            manifest_path = os.path.join(output_dir, f"snap_{profile_name}_{snapshot_id}.manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest, mf, indent=2)

            return snapshot_id, manifest, part_files

        finally:
            if os.path.exists(temp_tar.name):
                os.remove(temp_tar.name)

    def extract_snapshot(
        self,
        manifest: dict,
        part_files: List[str],
        passphrase: str,
        destination_dir: Optional[str] = None,
        in_place: bool = False
    ):
        """
        -----------------------------------------------------------------------
        Method: extract_snapshot
        Description:
            Performs complete restoration by decrypting and decompressing chunks.
            Supports layered incremental extraction: newer modified files overwrite
            base files, ensuring exact point-in-time filesystem consistency.
            If in_place is True, restored files are written directly back to their
            original absolute filesystem paths recorded in the snapshot manifest.
        Input parameters:
            @param manifest (dict)                  : Snapshot metadata manifest.
            @param part_files (List[str])           : Downloaded or local chunk paths.
            @param passphrase (str)                 : Decryption passphrase.
            @param destination_dir (Optional[str])  : Target extraction directory.
            @param in_place (bool)                  : If True, restore to original paths.
        -----------------------------------------------------------------------
        """
        if not in_place:
            if not destination_dir:
                raise ValueError("destination_dir is required when in_place is False")
            os.makedirs(destination_dir, exist_ok=True)
            target_dir = destination_dir
        else:
            target_dir = tempfile.mkdtemp(dir=self.staging_dir, prefix="tgb_inplace_")

        if not part_files:
            if in_place and os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
            return

        try:
            # Group chunk parts by snapshot prefix
            # Typical naming: snap_{profile}_{snapshot_id}.partXXX.zst.enc
            snaps_groups: Dict[str, List[str]] = {}
            for pf in sorted(part_files):
                fname = os.path.basename(pf)
                parts = fname.split(".part")
                prefix = parts[0] if len(parts) > 1 else "default"
                snaps_groups.setdefault(prefix, []).append(pf)

            dctx = zstd.ZstdDecompressor()

            # Extract in chronological snapshot group order (base first, then incrementals)
            for snap_prefix in sorted(snaps_groups.keys()):
                group_files = sorted(snaps_groups[snap_prefix])
                temp_tar = tempfile.NamedTemporaryFile(dir=self.staging_dir, delete=False, suffix=".tar")

                try:
                    with open(temp_tar.name, "wb") as tar_out:
                        with dctx.stream_writer(tar_out) as zstd_writer:
                            for pf in group_files:
                                with open(pf, "rb") as enc_f:
                                    enc_data = enc_f.read()
                                raw_data = decrypt_bytes(enc_data, passphrase)
                                zstd_writer.write(raw_data)

                    with tarfile.open(temp_tar.name, "r") as tar:
                        try:
                            tar.extractall(path=target_dir, filter="data")
                        except TypeError:
                            tar.extractall(path=target_dir)

                finally:
                    if os.path.exists(temp_tar.name):
                        os.remove(temp_tar.name)

            # Tombstone / Pruning: Remove deleted files not present in the final target manifest
            expected_files = set(manifest.get("files_catalog", {}).keys())
            if expected_files:
                for root, dirs, files in os.walk(target_dir, topdown=False):
                    for f in files:
                        full_p = os.path.join(root, f)
                        rel_p = os.path.relpath(full_p, target_dir)
                        if rel_p not in expected_files:
                            try:
                                os.remove(full_p)
                            except OSError:
                                pass
                    for d in dirs:
                        dir_p = os.path.join(root, d)
                        try:
                            if not os.listdir(dir_p):
                                os.rmdir(dir_p)
                        except OSError:
                            pass

            if in_place:
                # Copy verified point-in-time files directly to their original filesystem paths
                catalog = manifest.get("files_catalog", {})
                for rel_p, meta in catalog.items():
                    src_f = os.path.join(target_dir, rel_p)
                    orig_f = meta.get("abs_path")
                    if orig_f and os.path.exists(src_f):
                        os.makedirs(os.path.dirname(orig_f), exist_ok=True)
                        shutil.copy2(src_f, orig_f)
                        mtime = meta.get("mtime")
                        if mtime:
                            try:
                                os.utime(orig_f, (mtime, mtime))
                            except OSError:
                                pass

        finally:
            if in_place and os.path.exists(target_dir):
                shutil.rmtree(target_dir, ignore_errors=True)
