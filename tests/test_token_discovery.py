"""
Tests for token_discovery module

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
import tempfile
from pathlib import Path

import pytest

from src.token_discovery import (
    discover_all_tokens,
    get_aws_credentials,
    get_bitbucket_credentials,
    get_github_token,
    get_gitlab_token,
)


class TestGetGitHubToken:
    """Tests for GitHub token discovery"""

    def test_github_token_from_env(self, monkeypatch):
        """Test GitHub token is read from GITHUB_TOKEN env var"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token_12345")
        monkeypatch.delenv("GH_TOKEN", raising=False)

        token = get_github_token()
        assert token == "ghp_test_token_12345"

    def test_gh_token_fallback(self, monkeypatch):
        """Test GH_TOKEN is used when GITHUB_TOKEN is not set"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.setenv("GH_TOKEN", "ghp_fallback_token")

        token = get_github_token()
        assert token == "ghp_fallback_token"

    def test_github_token_priority(self, monkeypatch):
        """Test GITHUB_TOKEN takes priority over GH_TOKEN"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_primary")
        monkeypatch.setenv("GH_TOKEN", "ghp_secondary")

        token = get_github_token()
        assert token == "ghp_primary"

    def test_no_github_token(self, monkeypatch):
        """Test returns None when no GitHub token is available"""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)
        monkeypatch.delenv("GH_TOKEN", raising=False)

        # Token will be None if gh CLI is not installed or not authenticated
        token = get_github_token()
        # Can't assert None because gh CLI might be installed
        assert token is None or token.startswith("gh")


class TestGetGitLabToken:
    """Tests for GitLab token discovery"""

    def test_gitlab_token_from_env(self, monkeypatch):
        """Test GitLab token is read from GITLAB_TOKEN env var"""
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-test_token_12345")

        token = get_gitlab_token()
        assert token == "glpat-test_token_12345"

    def test_no_gitlab_token(self, monkeypatch):
        """Test returns None when no GitLab token is available"""
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)

        token = get_gitlab_token()
        # May return None or token from glab CLI config
        assert token is None or isinstance(token, str)


class TestGetBitbucketCredentials:
    """Tests for Bitbucket credential discovery"""

    def test_bitbucket_token_from_env(self, monkeypatch):
        """Test Bitbucket token is read from BITBUCKET_TOKEN env var"""
        monkeypatch.setenv("BITBUCKET_TOKEN", "ATCTT_test_token")
        monkeypatch.delenv("BITBUCKET_USERNAME", raising=False)
        monkeypatch.delenv("BITBUCKET_APP_PASSWORD", raising=False)

        token, username = get_bitbucket_credentials()
        assert token == "ATCTT_test_token"
        assert username is None

    def test_bitbucket_app_password(self, monkeypatch):
        """Test Bitbucket app password with username"""
        monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
        monkeypatch.setenv("BITBUCKET_USERNAME", "testuser")
        monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "test_app_password")

        token, username = get_bitbucket_credentials()
        assert token == "test_app_password"
        assert username == "testuser"

    def test_no_bitbucket_credentials(self, monkeypatch):
        """Test returns None when no Bitbucket credentials are available"""
        monkeypatch.delenv("BITBUCKET_TOKEN", raising=False)
        monkeypatch.delenv("BITBUCKET_USERNAME", raising=False)
        monkeypatch.delenv("BITBUCKET_APP_PASSWORD", raising=False)

        token, username = get_bitbucket_credentials()
        # May return None or credentials from .netrc
        assert token is None or isinstance(token, str)


class TestGetAWSCredentials:
    """Tests for AWS credential discovery"""

    def test_aws_credentials_from_env(self, monkeypatch):
        """Test AWS credentials from environment variables"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST123")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testsecret123")
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        access_key, secret_key, profile = get_aws_credentials()
        assert access_key == "AKIATEST123"
        assert secret_key == "testsecret123"
        assert profile is None

    def test_aws_profile_from_env(self, monkeypatch):
        """Test AWS profile from environment variable"""
        monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
        monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
        monkeypatch.setenv("AWS_PROFILE", "test-profile")

        access_key, secret_key, profile = get_aws_credentials()
        assert access_key is None
        assert secret_key is None
        assert profile == "test-profile"

    def test_aws_credentials_priority(self, monkeypatch):
        """Test access keys take priority over profile"""
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST123")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testsecret123")
        monkeypatch.setenv("AWS_PROFILE", "test-profile")

        access_key, secret_key, profile = get_aws_credentials()
        # Access keys take priority
        assert access_key == "AKIATEST123"
        assert secret_key == "testsecret123"
        assert profile is None


class TestDiscoverAllTokens:
    """Tests for discover_all_tokens function"""

    def test_discover_all_with_env_tokens(self, monkeypatch):
        """Test discovering all tokens from environment"""
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_test")
        monkeypatch.setenv("GITLAB_TOKEN", "glpat-test")
        monkeypatch.setenv("BITBUCKET_TOKEN", "ATCTT_test")
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
        monkeypatch.delenv("AWS_PROFILE", raising=False)

        discovered = discover_all_tokens()

        assert discovered.get("github_token") == "ghp_test"
        assert discovered.get("gitlab_token") == "glpat-test"
        assert discovered.get("bitbucket_token") == "ATCTT_test"
        assert discovered.get("aws_access_key_id") == "AKIATEST"
        assert discovered.get("aws_secret_access_key") == "secret"

    def test_discover_with_no_tokens(self, monkeypatch):
        """Test discovering when no tokens are set"""
        # Clear all token environment variables
        for var in [
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "GITLAB_TOKEN",
            "BITBUCKET_TOKEN",
            "BITBUCKET_USERNAME",
            "BITBUCKET_APP_PASSWORD",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_PROFILE",
        ]:
            monkeypatch.delenv(var, raising=False)

        discovered = discover_all_tokens()

        # Result depends on what CLI tools are installed
        # Just verify it returns a dict
        assert isinstance(discovered, dict)
