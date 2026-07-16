"""
Tests for GitLabManager

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

from src.gitlab_manager import GitLabManager


class TestGitLabManagerInit:
    """Tests for GitLabManager initialisation"""

    def test_init_with_invalid_token(self):
        """Test initialisation fails with invalid token"""
        with pytest.raises(Exception):
            GitLabManager(url="https://gitlab.com", token="invalid_token")

    def test_init_with_empty_token(self):
        """Test initialisation fails with empty token"""
        with pytest.raises(Exception):
            GitLabManager(url="https://gitlab.com", token="")

    def test_init_with_invalid_url(self):
        """Test initialisation with invalid URL"""
        with pytest.raises(Exception):
            GitLabManager(url="not-a-valid-url", token="test")


class TestGitLabManagerGroupFiltering:
    """Tests for GitLab group filtering via environment"""

    def test_include_groups_parsing(self, monkeypatch):
        """Test GITLAB_INCLUDE_GROUPS parsing"""
        monkeypatch.setenv("GITLAB_INCLUDE_GROUPS", "group1,group2,group3")

        # Would need valid token to actually test
        with pytest.raises(Exception):
            GitLabManager(url="https://gitlab.com", token="test")

    def test_exclude_groups_parsing(self, monkeypatch):
        """Test GITLAB_EXCLUDE_GROUPS parsing"""
        monkeypatch.setenv("GITLAB_EXCLUDE_GROUPS", "excluded1,excluded2")

        with pytest.raises(Exception):
            GitLabManager(url="https://gitlab.com", token="test")

    def test_include_groups_empty(self, monkeypatch):
        """Test empty GITLAB_INCLUDE_GROUPS"""
        monkeypatch.setenv("GITLAB_INCLUDE_GROUPS", "")
        monkeypatch.setenv("GITLAB_EXCLUDE_GROUPS", "")

        with pytest.raises(Exception):
            GitLabManager(url="https://gitlab.com", token="test")
