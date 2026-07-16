"""
Bitbucket repository manager

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
from typing import List
from urllib.parse import urlparse

import requests
from atlassian import Bitbucket

from .base import Repository, RepositoryManager


class BitbucketManager(RepositoryManager):
    """
    Bitbucket repository manager with workspace-scoped token support.

    IMPORTANT LIMITATION: Unlike GitHub/GitLab, Bitbucket uses workspace-scoped tokens.
    This means:
    - One token can only access ONE workspace (not multiple like GitHub orgs)
    - Cannot discover all workspaces with a single token
    - Must specify BITBUCKET_WORKSPACE for workspace tokens
    - Different architecture from GitHub (read:org) and GitLab (group discovery)

    This is a Bitbucket platform limitation, not a tool limitation.
    """

    def __init__(
        self, url: str, token: str, exclude_personal: bool = True, username: str = None
    ):
        super().__init__(token, exclude_personal)
        self.url = url
        self.token = token
        self.username = username  # Optional for legacy app passwords
        # Use proper URL parsing to check if host is bitbucket.org (prevents URL substring attacks)
        parsed_url = urlparse(url)
        self.is_cloud = (
            parsed_url.netloc == "bitbucket.org"
            or parsed_url.netloc.endswith(".bitbucket.org")
        )

        # Bitbucket Cloud authentication types (as of August 2025):
        # 1. App Passwords (legacy, being deprecated) - use Basic auth with username
        # 2. Workspace Access Tokens (current, ATCTT prefix) - use Bearer auth, workspace-scoped
        # Note: ATATT tokens are for Jira/Confluence, NOT Bitbucket
        self.is_workspace_token = token.startswith("ATCTT") if token else False
        self.is_app_password = (
            not self.is_workspace_token
        )  # If not workspace token, assume app password

        # CRITICAL: Workspace tokens require explicit workspace configuration
        # Unlike GitHub/GitLab where we can discover accessible organizations/groups,
        # Bitbucket workspace tokens are scoped to a SINGLE workspace only
        self.workspace = (
            os.getenv("BITBUCKET_WORKSPACE") if self.is_workspace_token else None
        )

        # Get group filters from environment
        include_groups_env = os.getenv("BITBUCKET_INCLUDE_GROUPS", "")
        exclude_groups_env = os.getenv("BITBUCKET_EXCLUDE_GROUPS", "")

        self.include_groups = (
            [g.strip() for g in include_groups_env.split(",") if g.strip()]
            if include_groups_env
            else None
        )
        self.exclude_groups = (
            [g.strip() for g in exclude_groups_env.split(",") if g.strip()]
            if exclude_groups_env
            else []
        )

    def get_repositories(self) -> List[Repository]:
        repos = []

        # Test authentication first
        # Workspace tokens might not have access to /user endpoint, so test with repositories
        if self.is_workspace_token:
            # For workspace tokens, test with the workspace's repositories endpoint
            if not self.workspace:
                self.logger.error(
                    "[ERROR] Workspace access token requires BITBUCKET_WORKSPACE to be set"
                )
                return repos
            test_url = f"https://api.bitbucket.org/2.0/repositories/{self.workspace}"
        else:
            # For app passwords, test with user endpoint
            test_url = "https://api.bitbucket.org/2.0/user"

        test_response = self._make_request(test_url)
        if test_response is None or test_response.status_code == 401:
            self.logger.error("[ERROR] Failed to authenticate with Bitbucket API")
            return repos

        if self.is_cloud:
            # WORKSPACE TOKEN LIMITATION: Can only access ONE workspace per token
            # This is different from GitHub (can access user + all orgs with read:org)
            # and GitLab (can discover all accessible groups with read_api)
            if self.is_workspace_token:
                if not self.workspace:
                    self.logger.error(
                        "[ERROR] Workspace access token requires BITBUCKET_WORKSPACE to be set"
                    )
                    self.logger.error(
                        "[ERROR] Unlike GitHub/GitLab, Bitbucket tokens are workspace-scoped"
                    )
                    return repos

                self.logger.info(
                    f"Using workspace access token for workspace: {self.workspace}"
                )
                self.logger.info(
                    f"[LIMITATION] This token can ONLY access workspace '{self.workspace}'"
                )
                workspace_repos = self._get_workspace_repos(self.workspace)
                self.logger.info(
                    f"Found {len(workspace_repos)} repositories in workspace {self.workspace}"
                )

                for repo in workspace_repos:
                    clone_url = self._get_clone_url(repo)
                    is_owned_by_user = repo["owner"]["type"] == "user"
                    is_corporate = (
                        repo["owner"]["type"] in ["team"] or not is_owned_by_user
                    )

                    if self.exclude_personal and is_owned_by_user:
                        continue

                    if is_corporate or not self.exclude_personal:
                        repos.append(
                            Repository(
                                name=repo["name"],
                                clone_url=clone_url,
                                owner=repo["workspace"]["slug"],
                                is_private=repo.get("is_private", True),
                                is_fork=repo.get("parent") is not None,
                                is_owned_by_user=is_owned_by_user,
                                platform="bitbucket",
                                size_kb=(
                                    repo.get("size", 0) // 1024
                                    if repo.get("size")
                                    else None
                                ),
                                default_branch=repo.get("mainbranch", {}).get(
                                    "name", "main"
                                ),
                            )
                        )
            elif self.include_groups:
                # For app passwords, can filter to specific workspaces
                self.logger.info(
                    f"Filtering to specific Bitbucket workspaces: {', '.join(self.include_groups)}"
                )
                for workspace_slug in self.include_groups:
                    try:
                        workspace_repos = self._get_workspace_repos(workspace_slug)
                        self.logger.info(
                            f"Found {len(workspace_repos)} repositories in workspace {workspace_slug}"
                        )

                        for repo in workspace_repos:
                            clone_url = self._get_clone_url(repo)

                            # For workspaces, repositories are typically organization/team owned
                            is_owned_by_user = repo["owner"]["type"] == "user"
                            is_corporate = (
                                repo["owner"]["type"] in ["team"]
                                or not is_owned_by_user
                            )

                            if self.exclude_personal and is_owned_by_user:
                                continue

                            if is_corporate or not self.exclude_personal:
                                repos.append(
                                    Repository(
                                        name=repo["name"],
                                        clone_url=clone_url,
                                        owner=repo["workspace"]["slug"],
                                        is_private=repo.get("is_private", True),
                                        is_fork=repo.get("parent") is not None,
                                        is_owned_by_user=is_owned_by_user,
                                        platform="bitbucket",
                                        size_kb=(
                                            repo.get("size", 0) // 1024
                                            if repo.get("size")
                                            else None
                                        ),
                                        default_branch=repo.get("mainbranch", {}).get(
                                            "name", "main"
                                        ),
                                    )
                                )
                    except Exception as e:
                        self.logger.warning(
                            f"Could not access workspace {workspace_slug}: {e}"
                        )
            else:
                # Get all accessible workspaces
                self.logger.info("Discovering all accessible Bitbucket workspaces")
                workspaces = self._get_workspaces()
                for workspace in workspaces:
                    # Skip if this workspace is in exclude list
                    if workspace["slug"] in self.exclude_groups:
                        self.logger.debug(
                            f"Skipping excluded workspace: {workspace['slug']}"
                        )
                        continue

                    workspace_repos = self._get_workspace_repos(workspace["slug"])
                    for repo in workspace_repos:
                        clone_url = self._get_clone_url(repo)

                        is_owned_by_user = repo["owner"]["type"] == "user"
                        is_corporate = (
                            repo["owner"]["type"] in ["team"] or not is_owned_by_user
                        )

                        if self.exclude_personal and is_owned_by_user:
                            continue

                        if is_corporate or not self.exclude_personal:
                            repos.append(
                                Repository(
                                    name=repo["name"],
                                    clone_url=clone_url,
                                    owner=repo["workspace"]["slug"],
                                    is_private=repo.get("is_private", True),
                                    is_fork=repo.get("parent") is not None,
                                    is_owned_by_user=is_owned_by_user,
                                    platform="bitbucket",
                                    size_kb=(
                                        repo.get("size", 0) // 1024
                                        if repo.get("size")
                                        else None
                                    ),
                                    default_branch=repo.get("mainbranch", {}).get(
                                        "name", "main"
                                    ),
                                )
                            )
        else:
            # Bitbucket Server/Data Center handling
            projects = self.client.project_list()
            for project in projects:
                project_repos = self.client.repo_list(project["key"])
                for repo in project_repos:
                    clone_url = self._get_server_clone_url(repo, project["key"])

                    repos.append(
                        Repository(
                            name=repo["name"],
                            clone_url=clone_url,
                            owner=project["key"],
                            is_private=not repo.get("public", False),
                            is_fork=repo.get("origin") is not None,
                            is_owned_by_user=False,
                            platform="bitbucket",
                            size_kb=None,
                            default_branch="main",
                        )
                    )

        return self.filter_corporate_repos(repos)

    def _make_request(self, url):
        """Make authenticated request for Bitbucket Cloud"""
        if self.is_app_password:
            # App Password requires username with Basic auth
            if not self.username:
                self.logger.error(
                    f"[ERROR] App passwords require BITBUCKET_USERNAME in .env file"
                )
                return None
            response = requests.get(url, auth=(self.username, self.token))
        else:
            # Workspace Access Token uses Bearer auth (no username needed)
            headers = {"Authorization": f"Bearer {self.token}"}
            response = requests.get(url, headers=headers)

        # Check if authentication failed
        if response.status_code == 401:
            self.logger.error(f"[ERROR] Bitbucket authentication failed.")
            if self.is_app_password:
                self.logger.error(
                    f"[ERROR] App password authentication failed. Required permissions:"
                )
                self.logger.error(f"[ERROR]   - Account: Read")
                self.logger.error(f"[ERROR]   - Workspace membership: Read")
                self.logger.error(f"[ERROR]   - Repositories: Read")
                self.logger.error(
                    f"[ERROR] Create at: https://bitbucket.org/account/settings/app-passwords/"
                )
            else:
                self.logger.error(
                    f"[ERROR] Workspace Access Token authentication failed."
                )
                self.logger.error(f"[ERROR] To create a Workspace Access Token:")
                self.logger.error(f"[ERROR]   1. Go to your workspace settings")
                self.logger.error(
                    f"[ERROR]   2. Click 'Access tokens' under 'Security'"
                )
                self.logger.error(
                    f"[ERROR]   3. Create token with 'Repositories: Read' permission"
                )
                self.logger.error(
                    f"[ERROR] Note: Workspace tokens are a Premium feature"
                )
            return None
        return response

    def _get_workspaces(self):
        workspaces = []
        url = "https://api.bitbucket.org/2.0/workspaces"

        while url:
            response = self._make_request(url)

            if response is None:
                break

            if response.status_code != 200:
                self.logger.error(f"Failed to get workspaces: {response.status_code}")
                self.logger.debug(f"Response: {response.text[:500]}")
                break

            data = response.json()
            workspaces.extend(data.get("values", []))
            url = data.get("next")

        return workspaces

    def _get_workspace_repos(self, workspace):
        repos = []
        url = f"https://api.bitbucket.org/2.0/repositories/{workspace}"

        while url:
            response = self._make_request(url)

            if response is None:
                break

            if response.status_code != 200:
                self.logger.error(
                    f"Failed to get repos for workspace {workspace}: {response.status_code}"
                )
                self.logger.debug(f"Response: {response.text[:500]}")
                break

            data = response.json()
            repos.extend(data.get("values", []))
            url = data.get("next")

        return repos

    def _get_clone_url(self, repo):
        for link in repo.get("links", {}).get("clone", []):
            if link["name"] == "https":
                url = link["href"]
                # Bitbucket Cloud returns URLs with embedded UUID like:
                # https://s0p9o9kp7vrajrkncobaklgtq4okpl@bitbucket.org/workspace/repo.git
                # We need to replace the UUID part with our authentication

                if "@bitbucket.org" in url:
                    # Remove existing auth (UUID) from URL
                    import re

                    url = re.sub(r"https://[^@]+@", "https://", url)

                if self.is_app_password and self.username:
                    # App password uses username:token
                    return url.replace(
                        "https://", f"https://{self.username}:{self.token}@"
                    )
                elif self.is_workspace_token:
                    # Workspace tokens use x-token-auth as username with the token as password
                    return url.replace(
                        "https://", f"https://x-token-auth:{self.token}@"
                    )
                else:
                    # Default fallback
                    return url.replace(
                        "https://", f"https://x-token-auth:{self.token}@"
                    )
        return None

    def _get_server_clone_url(self, repo, project_key):
        for link in repo.get("links", {}).get("clone", []):
            if link["name"] == "http":
                url = link["href"]
                parts = url.split("//")
                return f"{parts[0]}//x-token-auth:{self.token}@{parts[1]}"

        base_url = self.url.rstrip("/")
        return f'{base_url}/scm/{project_key}/{repo["slug"]}.git'.replace(
            "https://", f"https://x-token-auth:{self.token}@"
        )

    def is_corporate_repo(self, repo_data) -> bool:
        if self.is_cloud:
            return repo_data.get("owner", {}).get("type") == "team"
        return True
