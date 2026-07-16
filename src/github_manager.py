"""
GitHub repository manager

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

from github import Auth, Github

from .base import Repository, RepositoryManager


class GitHubManager(RepositoryManager):
    def __init__(self, token: str, exclude_personal: bool = True):
        super().__init__(token, exclude_personal)
        # PyGithub 2.x deprecated the positional login_or_token in favour of
        # an explicit Auth object.
        self.client = Github(auth=Auth.Token(token))

        # Validate authentication immediately by accessing user data
        try:
            self.user = self.client.get_user()
            # Force authentication check by accessing a property
            _ = self.user.login  # This will fail if token is invalid
            self.logger.debug(
                f"GitHub authentication successful for user: {self.user.login}"
            )
        except Exception as e:
            error_msg = str(e)
            if "401" in error_msg or "Bad credentials" in error_msg:
                self.logger.error(
                    "GitHub authentication failed: Invalid or expired token"
                )
                self.logger.error("Please check your GITHUB_TOKEN in the .env file")
            else:
                self.logger.error(f"GitHub authentication failed: {e}")
            raise ValueError(f"Invalid GitHub token: {e}") from e

        # Get organization filters from environment
        include_orgs_env = os.getenv("GITHUB_INCLUDE_ORGS", "")
        self.include_orgs = (
            [o.strip() for o in include_orgs_env.split(",") if o.strip()]
            if include_orgs_env
            else None
        )

        if self.include_orgs:
            self.logger.info(
                f"Filtering to GitHub organizations: {', '.join(self.include_orgs)}"
            )
        else:
            self.logger.info(
                f"[CONFIG] Including all accessible GitHub repositories (user + all organizations)"
            )

    def get_repositories(self) -> List[Repository]:
        repos = []

        # Check if we should include user repos
        include_user_repos = not self.include_orgs or "user" in self.include_orgs

        # Get user's own repositories
        if include_user_repos:
            self.logger.info(f"[CONFIG] Fetching user's own GitHub repositories")
            for repo in self.client.get_user().get_repos():
                if self.exclude_personal and self.is_personal_repo(repo):
                    continue

                repos.append(
                    Repository(
                        name=repo.name,
                        clone_url=repo.clone_url.replace(
                            "https://", f"https://{self.token}@"
                        ),
                        owner=repo.owner.login,
                        is_private=repo.private,
                        is_fork=repo.fork,
                        is_owned_by_user=(repo.owner.login == self.user.login),
                        platform="github",
                        size_kb=repo.size,
                        default_branch=repo.default_branch,
                    )
                )

        # Get organization repositories
        if self.include_orgs:
            # If specific orgs are listed, try to access them directly
            for org_name in self.include_orgs:
                if org_name == "user":
                    continue  # Already handled above

                try:
                    org = self.client.get_organization(org_name)
                    self.logger.info(
                        f"[CONFIG] Fetching repositories from GitHub organization: {org.login}"
                    )

                    for repo in org.get_repos():
                        repos.append(
                            Repository(
                                name=repo.name,
                                clone_url=repo.clone_url.replace(
                                    "https://", f"https://{self.token}@"
                                ),
                                owner=repo.owner.login,
                                is_private=repo.private,
                                is_fork=repo.fork,
                                is_owned_by_user=False,
                                platform="github",
                                size_kb=repo.size,
                                default_branch=repo.default_branch,
                            )
                        )
                except Exception as e:
                    self.logger.warning(
                        f"Could not access organization {org_name}: {e}"
                    )

        # If no specific orgs configured, get all organizations the user is a member of
        if not self.include_orgs:
            for org in self.user.get_orgs():
                self.logger.info(
                    f"[CONFIG] Fetching repositories from GitHub organization: {org.login}"
                )

                for repo in org.get_repos():
                    repos.append(
                        Repository(
                            name=repo.name,
                            clone_url=repo.clone_url.replace(
                                "https://", f"https://{self.token}@"
                            ),
                            owner=repo.owner.login,
                            is_private=repo.private,
                            is_fork=repo.fork,
                            is_owned_by_user=False,
                            platform="github",
                            size_kb=repo.size,
                            default_branch=repo.default_branch,
                        )
                    )

        # Remove duplicates based on owner/name
        seen = set()
        unique_repos = []
        for repo in repos:
            key = f"{repo.owner}/{repo.name}"
            if key not in seen:
                seen.add(key)
                unique_repos.append(repo)

        return self.filter_corporate_repos(unique_repos)

    def is_personal_repo(self, repo) -> bool:
        return repo.owner.login == self.user.login

    def is_corporate_repo(self, repo_data) -> bool:
        return repo_data.owner.type == "Organization"
