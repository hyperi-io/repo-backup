"""
Tests for BitbucketManager

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

from src.bitbucket_manager import BitbucketManager


class TestBitbucketManagerInit:
    """Tests for BitbucketManager initialisation"""

    def test_init_with_token(self, monkeypatch):
        """Test initialisation stores token correctly"""
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(
            url="https://bitbucket.org", token="test_token", username="testuser"
        )
        assert mgr.token == "test_token"
        assert mgr.username == "testuser"
        assert mgr.is_cloud is True

    def test_init_workspace_token(self, monkeypatch):
        """Test workspace token detection"""
        monkeypatch.setenv("BITBUCKET_WORKSPACE", "test-workspace")
        mgr = BitbucketManager(
            url="https://bitbucket.org", token="ATCTT_workspace_token"
        )
        assert mgr.is_workspace_token is True
        assert mgr.workspace == "test-workspace"

    def test_init_app_password(self, monkeypatch):
        """Test app password detection"""
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(
            url="https://bitbucket.org", token="app_password_token", username="user"
        )
        assert mgr.is_workspace_token is False
        assert mgr.is_app_password is True

    def test_init_server_url(self, monkeypatch):
        """Test Bitbucket Server detection"""
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(
            url="https://bitbucket.mycompany.com", token="test_token"
        )
        assert mgr.is_cloud is False


class TestBitbucketManagerWorkspaceFiltering:
    """Tests for Bitbucket workspace filtering via environment"""

    def test_workspace_from_env(self, monkeypatch):
        """Test BITBUCKET_WORKSPACE from environment"""
        monkeypatch.setenv("BITBUCKET_WORKSPACE", "my-workspace")
        mgr = BitbucketManager(url="https://bitbucket.org", token="ATCTT_token")
        assert mgr.workspace == "my-workspace"

    def test_include_groups_parsing(self, monkeypatch):
        """Test BITBUCKET_INCLUDE_GROUPS parsing"""
        monkeypatch.setenv("BITBUCKET_INCLUDE_GROUPS", "group1,group2,group3")
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(url="https://bitbucket.org", token="test_token")
        assert mgr.include_groups == ["group1", "group2", "group3"]

    def test_exclude_groups_parsing(self, monkeypatch):
        """Test BITBUCKET_EXCLUDE_GROUPS parsing"""
        monkeypatch.setenv("BITBUCKET_EXCLUDE_GROUPS", "excluded1,excluded2")
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(url="https://bitbucket.org", token="test_token")
        assert mgr.exclude_groups == ["excluded1", "excluded2"]

    def test_include_groups_empty(self, monkeypatch):
        """Test empty BITBUCKET_INCLUDE_GROUPS"""
        monkeypatch.setenv("BITBUCKET_INCLUDE_GROUPS", "")
        monkeypatch.setenv("BITBUCKET_EXCLUDE_GROUPS", "")
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(url="https://bitbucket.org", token="test_token")
        assert mgr.include_groups is None
        assert mgr.exclude_groups == []

    def test_exclude_personal_default(self, monkeypatch):
        """Test exclude_personal defaults to True"""
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(url="https://bitbucket.org", token="test_token")
        assert mgr.exclude_personal is True

    def test_exclude_personal_false(self, monkeypatch):
        """Test exclude_personal can be set to False"""
        monkeypatch.delenv("BITBUCKET_WORKSPACE", raising=False)
        mgr = BitbucketManager(
            url="https://bitbucket.org", token="test_token", exclude_personal=False
        )
        assert mgr.exclude_personal is False
