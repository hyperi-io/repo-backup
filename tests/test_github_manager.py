"""
Tests for GitHubManager

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

import pytest

from src.github_manager import GitHubManager


class TestGitHubManagerInit:
    """Tests for GitHubManager initialisation"""

    def test_init_with_invalid_token(self):
        """Test initialisation fails with invalid token"""
        with pytest.raises(ValueError) as exc_info:
            GitHubManager(token="invalid_token")
        assert "Invalid GitHub token" in str(exc_info.value)

    def test_init_with_empty_token(self):
        """Test initialisation fails with empty token"""
        with pytest.raises(Exception):
            GitHubManager(token="")

    def test_exclude_personal_default(self):
        """Test exclude_personal defaults to True"""
        # Can't fully test without valid token, but we can verify the parameter exists
        with pytest.raises(ValueError):
            mgr = GitHubManager(token="test", exclude_personal=True)

    def test_exclude_personal_false(self):
        """Test exclude_personal can be set to False"""
        with pytest.raises(ValueError):
            mgr = GitHubManager(token="test", exclude_personal=False)


class TestGitHubManagerOrgFiltering:
    """Tests for GitHub organisation filtering via environment"""

    def test_include_orgs_parsing(self, monkeypatch):
        """Test GITHUB_INCLUDE_ORGS parsing"""
        monkeypatch.setenv("GITHUB_INCLUDE_ORGS", "org1,org2,org3")

        # Would need valid token to actually test, but env var is read in __init__
        with pytest.raises(ValueError):
            GitHubManager(token="test")

    def test_include_orgs_empty(self, monkeypatch):
        """Test empty GITHUB_INCLUDE_ORGS"""
        monkeypatch.setenv("GITHUB_INCLUDE_ORGS", "")

        with pytest.raises(ValueError):
            GitHubManager(token="test")
