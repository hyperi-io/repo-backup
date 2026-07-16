"""
Tests for local_backup module

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

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.base import Repository
from src.local_backup import LocalBackup


class TestLocalBackupInit:
    """Tests for LocalBackup initialisation"""

    def test_creates_backup_directory(self):
        """Test that backup directory is created if it doesn't exist"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = Path(tmpdir) / "new_backup_dir"
            assert not backup_path.exists()

            backup = LocalBackup(str(backup_path))

            assert backup_path.exists()
            assert backup_path.is_dir()

    def test_creates_temp_directory(self):
        """Test that temp directory is created"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)

            # Default temp dir is under backup path
            assert backup.temp_dir.exists()
            assert "repo-backup" in str(backup.temp_dir)

    def test_custom_work_dir(self):
        """Test custom work directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "custom_work"
            backup_path = Path(tmpdir) / "backups"

            backup = LocalBackup(str(backup_path), work_dir=str(work_dir))

            assert "custom_work" in str(backup.temp_dir)
            assert backup.temp_dir.exists()

    def test_backup_method_default(self):
        """Test default backup method is direct"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)
            assert backup.method == "direct"

    def test_backup_method_archive(self):
        """Test archive backup method"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir, method="archive")
            assert backup.method == "archive"


class TestLocalBackupListBackups:
    """Tests for listing backups"""

    def test_list_empty_backups(self):
        """Test listing backups when directory is empty"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)
            backups = backup.list_backups()
            assert backups == []

    def test_list_backups_with_bundles(self):
        """Test listing backups finds bundle files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)

            # Create fake bundle files
            bundle_dir = Path(tmpdir) / "github" / "test-org"
            bundle_dir.mkdir(parents=True)
            (bundle_dir / "test-repo_20250101_120000.bundle").touch()
            (bundle_dir / "other-repo_20250102_120000.bundle").touch()

            backups = backup.list_backups()

            assert len(backups) == 2
            assert all(b["type"] == "bundle" for b in backups)

    def test_list_backups_with_archives(self):
        """Test listing backups finds tar.gz files"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)

            # Create fake archive files
            archive_dir = Path(tmpdir) / "gitlab" / "test-group"
            archive_dir.mkdir(parents=True)
            (archive_dir / "test-repo_20250101_120000.tar.gz").touch()

            backups = backup.list_backups()

            assert len(backups) == 1
            assert backups[0]["type"] == "archive"

    def test_list_backups_platform_filter(self):
        """Test listing backups with platform filter"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)

            # Create bundles for multiple platforms
            (Path(tmpdir) / "github" / "org").mkdir(parents=True)
            (Path(tmpdir) / "gitlab" / "group").mkdir(parents=True)
            (Path(tmpdir) / "github" / "org" / "repo1.bundle").touch()
            (Path(tmpdir) / "gitlab" / "group" / "repo2.bundle").touch()

            # Filter by platform
            github_backups = backup.list_backups(platform="github")
            gitlab_backups = backup.list_backups(platform="gitlab")

            assert len(github_backups) == 1
            assert "github" in github_backups[0]["path"]
            assert len(gitlab_backups) == 1
            assert "gitlab" in gitlab_backups[0]["path"]


class TestLocalBackupCleanup:
    """Tests for cleanup functionality"""

    def test_cleanup_temp(self):
        """Test cleanup removes temp directory"""
        with tempfile.TemporaryDirectory() as tmpdir:
            backup = LocalBackup(tmpdir)

            # Verify temp dir exists
            assert backup.temp_dir.exists()

            # Create some temp files
            (backup.temp_dir / "test_file.txt").touch()

            backup.cleanup_temp()

            assert not backup.temp_dir.exists()


class TestLocalBackupRepository:
    """Integration tests for backing up repositories"""

    @pytest.fixture
    def local_git_repo(self):
        """Create a local git repository for testing"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_path = Path(tmpdir) / "test-repo"
            repo_path.mkdir()

            # Initialise git repo
            subprocess.run(
                ["git", "init"], cwd=repo_path, capture_output=True, check=True
            )
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test User"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            # Create a file and commit
            test_file = repo_path / "README.md"
            test_file.write_text("# Test Repository\n\nThis is a test.")
            subprocess.run(
                ["git", "add", "README.md"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "commit", "-m", "Initial commit"],
                cwd=repo_path,
                capture_output=True,
                check=True,
            )

            yield str(repo_path)

    def test_backup_local_repository_direct(self, local_git_repo):
        """Test backing up a local repository using direct method (git bundle)"""
        with tempfile.TemporaryDirectory() as backup_dir:
            backup = LocalBackup(backup_dir, method="direct")

            repo = Repository(
                name="test-repo",
                clone_url=local_git_repo,
                owner="local",
                is_private=False,
                is_fork=False,
                is_owned_by_user=True,
                platform="local",
            )

            result = backup.backup_repository(repo)
            assert result is True

            # Check bundle was created
            backups = backup.list_backups()
            assert len(backups) == 1
            assert backups[0]["type"] == "bundle"
            assert backups[0]["size_mb"] > 0

            backup.cleanup_temp()

    def test_backup_local_repository_archive(self, local_git_repo):
        """Test backing up a local repository using archive method (tar.gz)"""
        with tempfile.TemporaryDirectory() as backup_dir:
            backup = LocalBackup(backup_dir, method="archive")

            repo = Repository(
                name="test-repo",
                clone_url=local_git_repo,
                owner="local",
                is_private=False,
                is_fork=False,
                is_owned_by_user=True,
                platform="local",
            )

            result = backup.backup_repository(repo)
            assert result is True

            # Check archive was created
            backups = backup.list_backups()
            assert len(backups) == 1
            assert backups[0]["type"] == "archive"
            assert backups[0]["size_mb"] > 0

            backup.cleanup_temp()

    def test_backup_skips_existing(self, local_git_repo):
        """Test that backup skips if backup with same commit already exists"""
        with tempfile.TemporaryDirectory() as backup_dir:
            backup = LocalBackup(backup_dir, method="direct")

            repo = Repository(
                name="test-repo",
                clone_url=local_git_repo,
                owner="local",
                is_private=False,
                is_fork=False,
                is_owned_by_user=True,
                platform="local",
            )

            # First backup
            result1 = backup.backup_repository(repo)
            assert result1 is True

            backups_after_first = backup.list_backups()
            assert len(backups_after_first) == 1

            # Second backup should skip (same commit)
            result2 = backup.backup_repository(repo)
            assert result2 is True

            backups_after_second = backup.list_backups()
            # Should still be just one backup
            assert len(backups_after_second) == 1

            backup.cleanup_temp()
