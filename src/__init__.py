"""
repo-backup - Enterprise repository backup tool

Syncs corporate Git repositories from GitHub, GitLab, and Bitbucket
to local storage or AWS S3 with professional security and lifecycle management.

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

__version__ = "2.0.2"
__author__ = "Derek"
__license__ = "Apache-2.0"
__description__ = "Enterprise repository backup tool that syncs corporate Git repositories to local storage or AWS S3"

from .base import Repository, RepositoryManager
from .bitbucket_manager import BitbucketManager
from .github_manager import GitHubManager
from .gitlab_manager import GitLabManager
from .main import main
from .s3_uploader import S3Uploader

__all__ = [
    "Repository",
    "RepositoryManager",
    "GitHubManager",
    "GitLabManager",
    "BitbucketManager",
    "S3Uploader",
    "main",
]
