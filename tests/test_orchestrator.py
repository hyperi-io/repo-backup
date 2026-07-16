"""
Tests for RepoBackupOrchestrator

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

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.base import Repository
from src.main import RepoBackupOrchestrator, get_env_default, setup_logging


# Helper to disable all token discovery
def disable_token_discovery(monkeypatch):
    """Disable token auto-discovery by clearing env vars and patching discovery functions"""
    for var in [
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "BITBUCKET_TOKEN",
        "BITBUCKET_USERNAME",
        "BITBUCKET_APP_PASSWORD",
        "AWS_S3_BUCKET",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_PROFILE",
    ]:
        monkeypatch.delenv(var, raising=False)

    # Patch token discovery functions using sys.modules to get the module
    import sys

    main_module = sys.modules["src.main"]
    monkeypatch.setattr(main_module, "get_github_token", lambda: None)
    monkeypatch.setattr(main_module, "get_gitlab_token", lambda *args: None)
    monkeypatch.setattr(main_module, "get_bitbucket_credentials", lambda: (None, None))
    monkeypatch.setattr(main_module, "get_aws_credentials", lambda: (None, None, None))


class TestSetupLogging:
    """Tests for setup_logging function"""

    def test_setup_logging_default(self):
        """Test default logging setup"""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = setup_logging()
                assert result is not None
                assert Path("logs").exists()
            finally:
                os.chdir(old_cwd)

    def test_setup_logging_verbose(self):
        """Test verbose logging setup"""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = setup_logging(verbose=True)
                assert result is not None
            finally:
                os.chdir(old_cwd)

    def test_setup_logging_custom_file(self):
        """Test custom log file name"""
        with tempfile.TemporaryDirectory() as tmpdir:
            import os

            old_cwd = os.getcwd()
            os.chdir(tmpdir)
            try:
                result = setup_logging(log_file="custom.log")
                assert result is not None
                assert Path("logs/custom.log").exists()
            finally:
                os.chdir(old_cwd)


class TestGetEnvDefault:
    """Tests for get_env_default function"""

    def test_get_env_default_exists(self, monkeypatch):
        """Test getting existing environment variable"""
        monkeypatch.setenv("TEST_VAR", "test_value")
        result = get_env_default("TEST_VAR")
        assert result == "test_value"

    def test_get_env_default_missing(self, monkeypatch):
        """Test getting missing environment variable"""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        result = get_env_default("MISSING_VAR")
        assert result is None

    def test_get_env_default_fallback(self, monkeypatch):
        """Test fallback value for missing variable"""
        monkeypatch.delenv("MISSING_VAR", raising=False)
        result = get_env_default("MISSING_VAR", "fallback")
        assert result == "fallback"


class TestRepoBackupOrchestratorInit:
    """Tests for RepoBackupOrchestrator initialisation"""

    def test_init_with_local_path(self, monkeypatch):
        """Test orchestrator with local backup path"""
        disable_token_discovery(monkeypatch)

        with tempfile.TemporaryDirectory() as tmpdir:
            orchestrator = RepoBackupOrchestrator(
                use_env=True, local_backup_path=tmpdir
            )

            assert orchestrator.local_backup is not None
            assert orchestrator.local_backup_path == tmpdir
            assert orchestrator.s3_uploader is None

    def test_init_without_config(self, monkeypatch):
        """Test orchestrator without any configuration"""
        disable_token_discovery(monkeypatch)

        orchestrator = RepoBackupOrchestrator(use_env=True)

        assert orchestrator.managers == []
        assert orchestrator.s3_uploader is None
        assert orchestrator.local_backup is None


class TestParseRepoSpec:
    """Tests for parse_repo_spec method"""

    @pytest.fixture
    def orchestrator(self, monkeypatch):
        """Create orchestrator without platform connections"""
        disable_token_discovery(monkeypatch)
        return RepoBackupOrchestrator(use_env=True)

    def test_parse_simple_repo(self, orchestrator):
        """Test parsing owner/repo format"""
        platform, owner, name = orchestrator.parse_repo_spec("myorg/myrepo")
        assert platform is None
        assert owner == "myorg"
        assert name == "myrepo"

    def test_parse_platform_repo(self, orchestrator):
        """Test parsing platform:owner/repo format"""
        platform, owner, name = orchestrator.parse_repo_spec("github:myorg/myrepo")
        assert platform == "github"
        assert owner == "myorg"
        assert name == "myrepo"

    def test_parse_gitlab_repo(self, orchestrator):
        """Test parsing gitlab platform"""
        platform, owner, name = orchestrator.parse_repo_spec("gitlab:group/project")
        assert platform == "gitlab"
        assert owner == "group"
        assert name == "project"

    def test_parse_nested_path(self, orchestrator):
        """Test parsing repo with nested path"""
        platform, owner, name = orchestrator.parse_repo_spec("github:org/group/repo")
        assert platform == "github"
        assert owner == "org"
        assert name == "group/repo"

    def test_parse_invalid_format(self, orchestrator):
        """Test parsing invalid format raises error"""
        with pytest.raises(ValueError) as exc_info:
            orchestrator.parse_repo_spec("invalid")
        assert "Invalid repository format" in str(exc_info.value)


class TestMatchRepoPattern:
    """Tests for match_repo_pattern method"""

    @pytest.fixture
    def orchestrator(self, monkeypatch):
        """Create orchestrator without platform connections"""
        disable_token_discovery(monkeypatch)
        return RepoBackupOrchestrator(use_env=True)

    @pytest.fixture
    def sample_repo(self):
        """Create sample repository for testing"""
        return Repository(
            name="myrepo",
            clone_url="https://github.com/myorg/myrepo.git",
            owner="myorg",
            is_private=True,
            is_fork=False,
            is_owned_by_user=False,
            platform="github",
        )

    def test_exact_match(self, orchestrator, sample_repo):
        """Test exact match pattern"""
        assert orchestrator.match_repo_pattern("myorg/myrepo", sample_repo) is True
        assert orchestrator.match_repo_pattern("other/repo", sample_repo) is False

    def test_exact_match_case_insensitive(self, orchestrator, sample_repo):
        """Test exact match is case insensitive"""
        assert orchestrator.match_repo_pattern("MYORG/MYREPO", sample_repo) is True
        assert orchestrator.match_repo_pattern("MyOrg/MyRepo", sample_repo) is True

    def test_platform_exact_match(self, orchestrator, sample_repo):
        """Test platform-specific exact match"""
        assert (
            orchestrator.match_repo_pattern("github:myorg/myrepo", sample_repo) is True
        )
        assert (
            orchestrator.match_repo_pattern("gitlab:myorg/myrepo", sample_repo) is False
        )

    def test_glob_pattern_star(self, orchestrator, sample_repo):
        """Test glob pattern with star"""
        assert orchestrator.match_repo_pattern("myorg/*", sample_repo) is True
        assert orchestrator.match_repo_pattern("*/myrepo", sample_repo) is True
        assert orchestrator.match_repo_pattern("other/*", sample_repo) is False

    def test_glob_pattern_question(self, orchestrator, sample_repo):
        """Test glob pattern with question mark"""
        assert orchestrator.match_repo_pattern("myorg/myrep?", sample_repo) is True
        assert orchestrator.match_repo_pattern("myor?/myrepo", sample_repo) is True

    def test_glob_pattern_brackets(self, orchestrator, sample_repo):
        """Test glob pattern with brackets"""
        assert orchestrator.match_repo_pattern("myorg/my[rs]epo", sample_repo) is True

    def test_regex_pattern(self, orchestrator, sample_repo):
        """Test regex pattern with re: prefix"""
        assert orchestrator.match_repo_pattern("re:.*repo$", sample_repo) is True
        assert orchestrator.match_repo_pattern("re:^myorg/", sample_repo) is True
        assert orchestrator.match_repo_pattern("re:^other/", sample_repo) is False

    def test_regex_pattern_case_insensitive(self, orchestrator, sample_repo):
        """Test regex is case insensitive"""
        assert orchestrator.match_repo_pattern("re:MYORG", sample_repo) is True

    def test_regex_invalid_pattern(self, orchestrator, sample_repo):
        """Test invalid regex pattern returns False"""
        assert orchestrator.match_repo_pattern("re:[invalid", sample_repo) is False


class TestLoadReposFromFile:
    """Tests for load_repos_from_file method"""

    @pytest.fixture
    def orchestrator(self, monkeypatch):
        """Create orchestrator without platform connections"""
        disable_token_discovery(monkeypatch)
        return RepoBackupOrchestrator(use_env=True)

    def test_load_simple_file(self, orchestrator):
        """Test loading repos from simple file"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("org1/repo1\n")
            f.write("org2/repo2\n")
            f.write("org3/repo3\n")
            f.flush()

            repos = orchestrator.load_repos_from_file(f.name)

            assert len(repos) == 3
            assert "org1/repo1" in repos
            assert "org2/repo2" in repos
            assert "org3/repo3" in repos

    def test_load_file_with_comments(self, orchestrator):
        """Test loading repos from file with comments"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# This is a comment\n")
            f.write("org1/repo1\n")
            f.write("# Another comment\n")
            f.write("org2/repo2\n")
            f.flush()

            repos = orchestrator.load_repos_from_file(f.name)

            assert len(repos) == 2
            assert "# This is a comment" not in repos

    def test_load_file_with_empty_lines(self, orchestrator):
        """Test loading repos from file with empty lines"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("org1/repo1\n")
            f.write("\n")
            f.write("   \n")
            f.write("org2/repo2\n")
            f.flush()

            repos = orchestrator.load_repos_from_file(f.name)

            assert len(repos) == 2

    def test_load_missing_file(self, orchestrator):
        """Test loading from non-existent file"""
        repos = orchestrator.load_repos_from_file("/nonexistent/file.txt")
        assert repos == []


class TestGetAllRepositories:
    """Tests for get_all_repositories method"""

    @pytest.fixture
    def orchestrator(self, monkeypatch):
        """Create orchestrator without platform connections"""
        disable_token_discovery(monkeypatch)
        return RepoBackupOrchestrator(use_env=True)

    def test_no_managers(self, orchestrator):
        """Test getting repos with no managers configured"""
        repos = orchestrator.get_all_repositories()
        assert repos == []

    def test_platform_filter_no_match(self, orchestrator):
        """Test platform filter with no matching managers"""
        repos = orchestrator.get_all_repositories(platforms=["github"])
        assert repos == []


class TestBackupRepository:
    """Tests for backup_repository method"""

    @pytest.fixture
    def orchestrator_with_local(self, monkeypatch):
        """Create orchestrator with local backup configured"""
        disable_token_discovery(monkeypatch)
        tmpdir = tempfile.mkdtemp()
        return RepoBackupOrchestrator(use_env=True, local_backup_path=tmpdir), tmpdir

    def test_backup_no_destination(self, monkeypatch):
        """Test backup fails when no destination configured"""
        disable_token_discovery(monkeypatch)

        orchestrator = RepoBackupOrchestrator(use_env=True)
        repo = Repository(
            name="test",
            clone_url="https://github.com/test/test.git",
            owner="test",
            is_private=False,
            is_fork=False,
            is_owned_by_user=False,
            platform="github",
        )

        result = orchestrator.backup_repository(repo)
        assert result is False
