"""
Local filesystem backup functionality

Copyright 2025 HyperI

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import logging
import os
import shutil
import subprocess
import tarfile
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from tqdm import tqdm

from .base import Repository


def robust_rmtree(path: Path, logger: logging.Logger, max_retries: int = 3) -> bool:
    """
    Robustly remove a directory tree with retries.
    Handles race conditions where files may still be written during removal.

    Args:
        path: Path to remove
        logger: Logger for messages
        max_retries: Maximum number of retry attempts

    Returns:
        True if successfully removed, False otherwise
    """
    if not path.exists():
        return True

    for attempt in range(max_retries):
        try:
            shutil.rmtree(path)
            return True
        except OSError as e:
            if attempt < max_retries - 1:
                # Wait briefly before retry to allow any processes to complete
                time.sleep(0.5 * (attempt + 1))
                logger.debug(
                    f"[CLEANUP] Retry {attempt + 1}/{max_retries} removing {path}: {e}"
                )
            else:
                logger.warning(
                    f"[CLEANUP] Failed to remove {path} after {max_retries} attempts: {e}"
                )
                return False
    return False


class LocalBackup:
    def __init__(
        self, backup_path: str, method: str = "direct", work_dir: Optional[str] = None
    ):
        """
        Initialize local backup manager
        Args:
            backup_path: Local directory to store backups
            method: 'direct' for git bundle or 'archive' for tar.gz
            work_dir: Working directory for temporary files (default: /var/tmp/repo-backup)
        """
        self.backup_path = Path(backup_path)
        self.method = method
        self.logger = logging.getLogger(self.__class__.__name__)

        # Create backup directory if it doesn't exist
        try:
            self.backup_path.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"[CONFIG] Local backup directory: {self.backup_path}")
        except Exception as e:
            self.logger.error(
                f"[ERROR] Failed to create backup directory {self.backup_path}: {e}"
            )
            raise

        # Create temp directory for operations
        if work_dir:
            # Use explicitly provided work directory
            self.temp_dir = Path(work_dir) / "repo-backup"
        else:
            # Default to tmp/repo-backup under the backup path (keeps everything together)
            self.temp_dir = self.backup_path / "tmp" / "repo-backup"

        try:
            self.temp_dir.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"[CONFIG] Working directory: {self.temp_dir}")
        except Exception as e:
            self.logger.error(
                f"[ERROR] Failed to create working directory {self.temp_dir}: {e}"
            )
            raise

    def backup_repository(self, repo: Repository) -> bool:
        """
        Backup repository to local filesystem
        Args:
            repo: Repository object
        Returns:
            bool: Success status
        """
        try:
            platform_dir = self.backup_path / repo.platform / repo.owner
            platform_dir.mkdir(parents=True, exist_ok=True)

            if self.method == "direct":
                return self._direct_backup(repo, platform_dir)
            else:
                return self._archive_backup(repo, platform_dir)

        except Exception as e:
            self.logger.error(f"Failed to backup {repo.name}: {e}")
            return False

    def _direct_backup(self, repo: Repository, backup_dir: Path) -> bool:
        """Backup repository as git bundle"""
        try:
            # Use timestamp to ensure unique temp directory for parallel operations
            import uuid

            unique_id = uuid.uuid4().hex[:8]
            repo_path = self.temp_dir / f"{repo.name}_{unique_id}_clone"

            # Clone repository
            self.logger.info(f"[BACKUP] Cloning {repo.name}...")
            clone_cmd = ["git", "clone", "--mirror", repo.clone_url, str(repo_path)]

            # Use DEVNULL for stdout to avoid memory buffering of git progress output
            result = subprocess.run(
                clone_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.temp_dir.parent),
            )

            if result.returncode != 0:
                stderr_truncated = result.stderr[:500] if result.stderr else ""
                self.logger.error(
                    f"[ERROR] Clone failed for {repo.name}: {stderr_truncated}"
                )
                return False

            # Get the last commit date from the repository
            last_commit_cmd = [
                "git",
                "log",
                "-1",
                "--format=%cd",
                "--date=format:%Y%m%d_%H%M%S",
            ]
            last_commit_result = subprocess.run(
                last_commit_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                cwd=str(repo_path),
            )

            if last_commit_result.returncode != 0:
                # Check if it's an empty repository
                check_empty = subprocess.run(
                    ["git", "rev-list", "-n", "1", "--all"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=str(repo_path),
                )
                if check_empty.returncode != 0 or not check_empty.stdout.strip():
                    self.logger.info(
                        f"[SKIP] Skipping {repo.name} - repository is empty (no commits)"
                    )
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return True
                else:
                    self.logger.error(
                        f"[ERROR] Failed to get last commit date for {repo.name}"
                    )
                    return False

            last_commit_date = last_commit_result.stdout.strip()
            if not last_commit_date:
                # Empty repo, use current date as fallback
                last_commit_date = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Check if a backup with this commit date already exists
            bundle_path = backup_dir / f"{repo.name}_{last_commit_date}.bundle"

            if bundle_path.exists():
                file_size = bundle_path.stat().st_size
                self.logger.info(
                    f"[SKIP] Backup already exists for {repo.name} with commit date {last_commit_date}: "
                    f"{bundle_path.name} ({file_size / 1024 / 1024:.2f} MB)"
                )
                # Cleanup temp directory
                if repo_path.exists():
                    robust_rmtree(repo_path, self.logger)
                return True

            # Fetch LFS objects if LFS is used in the repository
            # Check .gitattributes for LFS filter patterns (works on bare repos)
            lfs_check = subprocess.run(
                ["git", "--git-dir", str(repo_path), "show", "HEAD:.gitattributes"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            has_lfs = False
            if lfs_check.returncode == 0 and "filter=lfs" in lfs_check.stdout:
                # Verify git-lfs is installed
                lfs_installed = subprocess.run(
                    ["git", "lfs", "version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if lfs_installed.returncode != 0:
                    self.logger.error(
                        f"[ERROR] Repository {repo.name} uses Git LFS but git-lfs is not installed. "
                        "Please install git-lfs to backup this repository."
                    )
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return False

                self.logger.info(f"[LFS] Fetching LFS objects for {repo.name}...")
                # Stream LFS output to DEVNULL - can be very large
                lfs_fetch = subprocess.run(
                    ["git", "--git-dir", str(repo_path), "lfs", "fetch", "--all"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if lfs_fetch.returncode != 0:
                    stderr_truncated = (
                        lfs_fetch.stderr[:500] if lfs_fetch.stderr else ""
                    )
                    self.logger.error(
                        f"[ERROR] LFS fetch failed for {repo.name}: {stderr_truncated}"
                    )
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return False
                has_lfs = True

            # Create git bundle
            self.logger.info(f"[BUNDLE] Creating bundle for {repo.name}...")
            bundle_cmd = ["git", "bundle", "create", str(bundle_path), "--all"]

            result = subprocess.run(
                bundle_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(repo_path),
            )

            if result.returncode != 0:
                # Check if it's an empty repository
                if "Refusing to create empty bundle" in result.stderr:
                    self.logger.info(
                        f"[SKIP] Skipping {repo.name} - repository is empty (no commits)"
                    )
                    # Cleanup temp directory
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return True  # Return True to indicate this was handled successfully
                else:
                    self.logger.error(
                        f"[ERROR] Bundle creation failed: {result.stderr}"
                    )
                    return False

            file_size = bundle_path.stat().st_size
            self.logger.info(
                f"[SUCCESS] Backed up {repo.name} ({file_size / 1024 / 1024:.2f} MB) to {bundle_path}"
            )

            # If repo has LFS, create separate archive of LFS objects
            # Git bundles do NOT include LFS objects, so we must archive them separately
            if has_lfs:
                lfs_objects_path = repo_path / "lfs" / "objects"
                if lfs_objects_path.exists():
                    lfs_archive_path = (
                        backup_dir / f"{repo.name}_{last_commit_date}_lfs.tar.gz"
                    )
                    self.logger.info(
                        f"[LFS] Creating LFS objects archive for {repo.name}..."
                    )
                    with tarfile.open(lfs_archive_path, "w:gz") as tar:
                        tar.add(lfs_objects_path, arcname="lfs/objects")
                    lfs_size = lfs_archive_path.stat().st_size
                    self.logger.info(
                        f"[LFS] Created LFS archive ({lfs_size / 1024 / 1024:.2f} MB) at {lfs_archive_path}"
                    )

            # Cleanup temp directory
            if repo_path.exists():
                robust_rmtree(repo_path, self.logger)

            return True

        except Exception as e:
            self.logger.error(
                f"[ERROR] Direct backup failed for {repo.name}: {type(e).__name__}: {e}"
            )
            # Cleanup temp directory on failure
            if repo_path.exists():
                try:
                    robust_rmtree(repo_path, self.logger)
                    self.logger.debug(
                        f"[CLEANUP] Removed failed temp directory: {repo_path}"
                    )
                except Exception as cleanup_error:
                    self.logger.warning(
                        f"[CLEANUP] Failed to remove temp directory {repo_path}: {cleanup_error}"
                    )
            return False

    def _archive_backup(self, repo: Repository, backup_dir: Path) -> bool:
        """Backup repository as tar.gz archive"""
        try:
            # Use unique ID to ensure unique temp directory for parallel operations
            import uuid

            unique_id = uuid.uuid4().hex[:8]
            repo_path = self.temp_dir / f"{repo.name}_{unique_id}_clone"

            # Clone repository
            self.logger.info(f"[BACKUP] Cloning {repo.name}...")
            clone_cmd = ["git", "clone", "--mirror", repo.clone_url, str(repo_path)]

            # Use DEVNULL for stdout to avoid memory buffering of git progress output
            result = subprocess.run(
                clone_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                cwd=str(self.temp_dir.parent),
            )

            if result.returncode != 0:
                stderr_truncated = result.stderr[:500] if result.stderr else ""
                self.logger.error(
                    f"[ERROR] Clone failed for {repo.name}: {stderr_truncated}"
                )
                return False

            # Get the last commit date from the repository
            last_commit_cmd = [
                "git",
                "log",
                "-1",
                "--format=%cd",
                "--date=format:%Y%m%d_%H%M%S",
            ]
            last_commit_result = subprocess.run(
                last_commit_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                cwd=str(repo_path),
            )

            if last_commit_result.returncode != 0:
                # Check if it's an empty repository
                check_empty = subprocess.run(
                    ["git", "rev-list", "-n", "1", "--all"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    cwd=str(repo_path),
                )
                if check_empty.returncode != 0 or not check_empty.stdout.strip():
                    self.logger.info(
                        f"[SKIP] Skipping {repo.name} - repository is empty (no commits)"
                    )
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return True
                else:
                    self.logger.error(
                        f"[ERROR] Failed to get last commit date for {repo.name}"
                    )
                    return False

            last_commit_date = last_commit_result.stdout.strip()
            if not last_commit_date:
                # Empty repo, use current date as fallback
                last_commit_date = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Check if a backup with this commit date already exists
            archive_path = backup_dir / f"{repo.name}_{last_commit_date}.tar.gz"

            if archive_path.exists():
                file_size = archive_path.stat().st_size
                self.logger.info(
                    f"[SKIP] Backup already exists for {repo.name} with commit date {last_commit_date}: "
                    f"{archive_path.name} ({file_size / 1024 / 1024:.2f} MB)"
                )
                # Cleanup temp directory
                if repo_path.exists():
                    robust_rmtree(repo_path, self.logger)
                return True

            # Fetch LFS objects if LFS is used in the repository
            # Check .gitattributes for LFS filter patterns (works on bare repos)
            lfs_check = subprocess.run(
                ["git", "--git-dir", str(repo_path), "show", "HEAD:.gitattributes"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )

            if lfs_check.returncode == 0 and "filter=lfs" in lfs_check.stdout:
                # Verify git-lfs is installed
                lfs_installed = subprocess.run(
                    ["git", "lfs", "version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                if lfs_installed.returncode != 0:
                    self.logger.error(
                        f"[ERROR] Repository {repo.name} uses Git LFS but git-lfs is not installed. "
                        "Please install git-lfs to backup this repository."
                    )
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return False

                self.logger.info(f"[LFS] Fetching LFS objects for {repo.name}...")
                lfs_fetch = subprocess.run(
                    ["git", "--git-dir", str(repo_path), "lfs", "fetch", "--all"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                if lfs_fetch.returncode != 0:
                    stderr_truncated = (
                        lfs_fetch.stderr[:500] if lfs_fetch.stderr else ""
                    )
                    self.logger.error(
                        f"[ERROR] LFS fetch failed for {repo.name}: {stderr_truncated}"
                    )
                    if repo_path.exists():
                        robust_rmtree(repo_path, self.logger)
                    return False

            # Create tar.gz archive
            self.logger.info(f"[ARCHIVE] Creating archive for {repo.name}...")
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(repo_path, arcname=repo.name)

            file_size = archive_path.stat().st_size
            self.logger.info(
                f"[SUCCESS] Backed up {repo.name} ({file_size / 1024 / 1024:.2f} MB) to {archive_path}"
            )

            # Cleanup temp directory
            if repo_path.exists():
                robust_rmtree(repo_path, self.logger)

            return True

        except Exception as e:
            self.logger.error(
                f"[ERROR] Archive backup failed for {repo.name}: {type(e).__name__}: {e}"
            )
            # Cleanup temp directory on failure
            if repo_path.exists():
                try:
                    robust_rmtree(repo_path, self.logger)
                    self.logger.debug(
                        f"[CLEANUP] Removed failed temp directory: {repo_path}"
                    )
                except Exception as cleanup_error:
                    self.logger.warning(
                        f"[CLEANUP] Failed to remove temp directory {repo_path}: {cleanup_error}"
                    )
            return False

    def list_backups(self, platform: Optional[str] = None) -> list:
        """List all local backups"""
        backups = []

        if platform:
            search_path = self.backup_path / platform
        else:
            search_path = self.backup_path

        if not search_path.exists():
            return backups

        for backup_file in search_path.rglob("*.bundle"):
            backups.append(
                {
                    "path": str(backup_file),
                    "size_mb": backup_file.stat().st_size / 1024 / 1024,
                    "modified": datetime.fromtimestamp(
                        backup_file.stat().st_mtime
                    ).isoformat(),
                    "type": "bundle",
                }
            )

        for backup_file in search_path.rglob("*.tar.gz"):
            backups.append(
                {
                    "path": str(backup_file),
                    "size_mb": backup_file.stat().st_size / 1024 / 1024,
                    "modified": datetime.fromtimestamp(
                        backup_file.stat().st_mtime
                    ).isoformat(),
                    "type": "archive",
                }
            )

        return sorted(backups, key=lambda x: x["modified"], reverse=True)

    def cleanup_stale_temps(self):
        """Clean up any stale temporary directories from previous failed runs"""
        if not self.temp_dir.exists():
            return

        stale_count = 0
        for item in self.temp_dir.iterdir():
            if item.is_dir() and "_clone" in item.name:
                if robust_rmtree(item, self.logger):
                    stale_count += 1
                    self.logger.debug(f"[CLEANUP] Removed stale temp directory: {item}")

        if stale_count > 0:
            self.logger.info(
                f"[CLEANUP] Removed {stale_count} stale temporary directories from previous runs"
            )

    def cleanup_temp(self):
        """Clean up temporary directory completely"""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)
            self.logger.info(f"[CLEANUP] Removed working directory: {self.temp_dir}")

            # Also remove parent tmp directory if empty and we created it
            tmp_parent = self.temp_dir.parent
            if tmp_parent.name == "tmp" and tmp_parent.exists():
                try:
                    tmp_parent.rmdir()  # Only removes if empty
                    self.logger.info(
                        f"[CLEANUP] Removed empty tmp directory: {tmp_parent}"
                    )
                except OSError:
                    # Directory not empty, that's fine
                    pass
