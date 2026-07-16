"""
GitLab repository manager

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
from typing import List, Optional

import gitlab

from .base import Repository, RepositoryManager


class GitLabManager(RepositoryManager):
    def __init__(self, url: str, token: str, exclude_personal: bool = True):
        super().__init__(token, exclude_personal)
        self.url = url
        self.client = gitlab.Gitlab(url, private_token=token)
        self.client.auth()
        self.user = self.client.user

        # Get group filters from environment
        include_groups_env = os.getenv("GITLAB_INCLUDE_GROUPS", "")
        exclude_groups_env = os.getenv("GITLAB_EXCLUDE_GROUPS", "")

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
        projects = []

        # If specific groups are specified, only get projects from those groups
        if self.include_groups:
            self.logger.info(
                f"FILTER: Filtering to specific GitLab groups: {', '.join(self.include_groups)}"
            )
            for group_path in self.include_groups:
                try:
                    # Get the group by path
                    group = self.client.groups.get(group_path, lazy=True)
                    self.logger.debug(f"Accessing group: {group_path}")

                    # Get all projects in this group (including subgroups)
                    group_projects = group.projects.list(
                        all=True, get_all=True, include_subgroups=True
                    )
                    self.logger.info(
                        f"Found {len(group_projects)} projects in group {group_path}"
                    )

                    for project_ref in group_projects:
                        # Get full project details
                        try:
                            project = self.client.projects.get(project_ref.id)
                            projects.append(project)
                        except Exception as e:
                            self.logger.debug(
                                f"Could not access project {project_ref.id}: {e}"
                            )
                except Exception as e:
                    self.logger.warning(f"Could not access group {group_path}: {e}")
        else:
            # Get all projects the user has access to (original behavior)
            self.logger.info("DISCOVER: Discovering all accessible GitLab projects")
            projects = self.client.projects.list(
                membership=True, all=True, get_all=True
            )

            # Also get projects from groups the user is a member of
            try:
                groups = self.client.groups.list(all=True, get_all=True)
                for group in groups:
                    # Skip if this group is in exclude list
                    if group.full_path in self.exclude_groups:
                        self.logger.debug(f"Skipping excluded group: {group.full_path}")
                        continue

                    try:
                        group_projects = group.projects.list(all=True, get_all=True)
                        for project_ref in group_projects:
                            # Get full project details
                            project = self.client.projects.get(project_ref.id)
                            projects.append(project)
                    except Exception as e:
                        self.logger.debug(f"Could not access group {group.name}: {e}")
            except Exception as e:
                self.logger.debug(f"Could not list groups: {e}")

        # Remove duplicates based on project ID
        seen_projects = set()
        unique_projects = []
        for project in projects:
            if project.id not in seen_projects:
                seen_projects.add(project.id)
                unique_projects.append(project)

        for project in unique_projects:
            clone_url = project.http_url_to_repo.replace(
                "https://", f"https://oauth2:{self.token}@"
            )

            is_personal = project.namespace["kind"] == "user"
            is_owned_by_user = (
                project.namespace["id"] == self.user.id if is_personal else False
            )

            # For corporate repos, we want group/organization projects
            is_corporate = (
                project.namespace["kind"] in ["group", "project"] or not is_personal
            )

            if self.exclude_personal and is_owned_by_user:
                continue

            # Include if it's corporate (group/org) or if we're not excluding personal
            if is_corporate or not self.exclude_personal:
                repos.append(
                    Repository(
                        # Use the URL-safe path slug, not the mutable display
                        # name (project.name can contain spaces/caps). Keeps
                        # --repos matching and bundle filenames on stable slugs.
                        name=project.path,
                        clone_url=clone_url,
                        owner=project.namespace["full_path"],
                        is_private=project.visibility == "private",
                        is_fork=hasattr(project, "forked_from_project")
                        and project.forked_from_project is not None,
                        is_owned_by_user=is_owned_by_user,
                        platform="gitlab",
                        size_kb=None,
                        default_branch=project.default_branch,
                    )
                )

        return self.filter_corporate_repos(repos)

    def is_corporate_repo(self, repo_data) -> bool:
        return repo_data.namespace["kind"] in ["group", "project"]
