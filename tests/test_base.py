"""
Tests for base module

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

from src.base import Repository


class TestRepository:
    """Tests for Repository dataclass"""

    def test_repository_creation(self):
        """Test basic repository creation"""
        repo = Repository(
            name="test-repo",
            clone_url="https://github.com/test/test-repo.git",
            owner="test-org",
            is_private=True,
            is_fork=False,
            is_owned_by_user=False,
            platform="github",
        )

        assert repo.name == "test-repo"
        assert repo.owner == "test-org"
        assert repo.platform == "github"
        assert repo.is_private is True
        assert repo.is_fork is False
        assert repo.is_owned_by_user is False
        assert repo.size_kb is None
        assert repo.default_branch is None

    def test_repository_with_optional_fields(self):
        """Test repository creation with optional fields"""
        repo = Repository(
            name="test-repo",
            clone_url="https://gitlab.com/test/test-repo.git",
            owner="test-group",
            is_private=False,
            is_fork=True,
            is_owned_by_user=True,
            platform="gitlab",
            size_kb=1024,
            default_branch="main",
        )

        assert repo.size_kb == 1024
        assert repo.default_branch == "main"
        assert repo.platform == "gitlab"
        assert repo.is_fork is True

    def test_repository_equality(self):
        """Test that two repositories with same values are equal"""
        repo1 = Repository(
            name="test-repo",
            clone_url="https://github.com/test/test-repo.git",
            owner="test-org",
            is_private=True,
            is_fork=False,
            is_owned_by_user=False,
            platform="github",
        )
        repo2 = Repository(
            name="test-repo",
            clone_url="https://github.com/test/test-repo.git",
            owner="test-org",
            is_private=True,
            is_fork=False,
            is_owned_by_user=False,
            platform="github",
        )

        assert repo1 == repo2

    def test_repository_platforms(self):
        """Test repository works with all supported platforms"""
        platforms = ["github", "gitlab", "bitbucket"]

        for platform in platforms:
            repo = Repository(
                name="test-repo",
                clone_url=f"https://{platform}.com/test/test-repo.git",
                owner="test-org",
                is_private=True,
                is_fork=False,
                is_owned_by_user=False,
                platform=platform,
            )
            assert repo.platform == platform
