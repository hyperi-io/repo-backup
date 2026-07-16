"""
Auto-discovery of authentication tokens from standard locations

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

import configparser
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


def get_github_token() -> Optional[str]:
    """
    Discover GitHub token from standard locations.

    Priority:
    1. GITHUB_TOKEN environment variable
    2. GH_TOKEN environment variable
    3. gh CLI auth token (via `gh auth token` command)

    Returns:
        GitHub token or None if not found
    """
    # Check environment variables first
    token = os.getenv("GITHUB_TOKEN")
    if token:
        logger.debug("[TOKEN] GitHub token found in GITHUB_TOKEN env var")
        return token

    token = os.getenv("GH_TOKEN")
    if token:
        logger.debug("[TOKEN] GitHub token found in GH_TOKEN env var")
        return token

    # Try gh CLI
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            logger.info("[TOKEN] GitHub token discovered from gh CLI")
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def get_gitlab_token(gitlab_url: str = "https://gitlab.com") -> Optional[str]:
    """
    Discover GitLab token from standard locations.

    Priority:
    1. GITLAB_TOKEN environment variable
    2. ~/.config/glab-cli/config.yml

    Args:
        gitlab_url: GitLab instance URL to look up token for

    Returns:
        GitLab token or None if not found
    """
    # Check environment variable first
    token = os.getenv("GITLAB_TOKEN")
    if token:
        logger.debug("[TOKEN] GitLab token found in GITLAB_TOKEN env var")
        return token

    # Try glab CLI config
    config_paths = [
        Path.home() / ".config" / "glab-cli" / "config.yml",
        Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "glab-cli"
        / "config.yml",
    ]

    # Extract hostname from URL
    from urllib.parse import urlparse

    hostname = urlparse(gitlab_url).netloc or "gitlab.com"

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                    if config and "hosts" in config:
                        host_config = config["hosts"].get(hostname, {})
                        token = host_config.get("token")
                        if token:
                            logger.info(
                                f"[TOKEN] GitLab token discovered from {config_path}"
                            )
                            return token
            except Exception as e:
                logger.debug(f"[TOKEN] Failed to read glab config: {e}")

    return None


def get_bitbucket_credentials() -> Tuple[Optional[str], Optional[str]]:
    """
    Discover Bitbucket credentials from standard locations.

    Priority:
    1. BITBUCKET_TOKEN environment variable (for workspace/repo access tokens)
    2. BITBUCKET_USERNAME + BITBUCKET_APP_PASSWORD environment variables
    3. ~/.netrc file

    Returns:
        Tuple of (token_or_password, username) - username may be None for tokens
    """
    # Check for workspace/repo access token
    token = os.getenv("BITBUCKET_TOKEN")
    if token:
        logger.debug("[TOKEN] Bitbucket token found in BITBUCKET_TOKEN env var")
        return token, None

    # Check for app password with username
    username = os.getenv("BITBUCKET_USERNAME")
    app_password = os.getenv("BITBUCKET_APP_PASSWORD")
    if username and app_password:
        logger.debug(
            "[TOKEN] Bitbucket credentials found in BITBUCKET_USERNAME/APP_PASSWORD env vars"
        )
        return app_password, username

    # Try .netrc
    netrc_path = Path.home() / ".netrc"
    if netrc_path.exists():
        try:
            import netrc

            auth = netrc.netrc(str(netrc_path))
            for host in ["bitbucket.org", "api.bitbucket.org"]:
                creds = auth.authenticators(host)
                if creds:
                    login, _, password = creds
                    logger.info(f"[TOKEN] Bitbucket credentials discovered from .netrc")
                    return password, login
        except Exception as e:
            logger.debug(f"[TOKEN] Failed to read .netrc: {e}")

    return None, None


def get_aws_credentials(
    profile: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Discover AWS credentials from standard locations.

    Priority:
    1. AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY environment variables
    2. AWS_PROFILE environment variable (returns profile name)
    3. ~/.aws/credentials file (default profile or specified profile)

    Args:
        profile: Specific profile to look for

    Returns:
        Tuple of (access_key_id, secret_access_key, profile_name)
        - If using env vars: (key_id, secret_key, None)
        - If using profile: (None, None, profile_name)
        - If using credentials file: (key_id, secret_key, None)
    """
    # Check environment variables first
    access_key = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    if access_key and secret_key:
        logger.debug("[TOKEN] AWS credentials found in environment variables")
        return access_key, secret_key, None

    # Check for profile in environment
    env_profile = os.getenv("AWS_PROFILE")
    if env_profile:
        logger.debug(
            f"[TOKEN] AWS profile '{env_profile}' found in AWS_PROFILE env var"
        )
        return None, None, env_profile

    # Try credentials file
    credentials_path = Path.home() / ".aws" / "credentials"
    if credentials_path.exists():
        try:
            config = configparser.ConfigParser()
            config.read(credentials_path)

            # Use specified profile, or default
            profile_name = profile or "default"
            if profile_name in config:
                access_key = config[profile_name].get("aws_access_key_id")
                secret_key = config[profile_name].get("aws_secret_access_key")
                if access_key and secret_key:
                    logger.info(
                        f"[TOKEN] AWS credentials discovered from ~/.aws/credentials [{profile_name}]"
                    )
                    return access_key, secret_key, None
        except Exception as e:
            logger.debug(f"[TOKEN] Failed to read AWS credentials file: {e}")

    return None, None, None


def discover_all_tokens() -> dict:
    """
    Discover all available tokens from standard locations.

    Returns:
        Dictionary with discovered credentials
    """
    discovered = {}

    # GitHub
    github_token = get_github_token()
    if github_token:
        discovered["github_token"] = github_token

    # GitLab
    gitlab_token = get_gitlab_token()
    if gitlab_token:
        discovered["gitlab_token"] = gitlab_token

    # Bitbucket
    bb_token, bb_username = get_bitbucket_credentials()
    if bb_token:
        discovered["bitbucket_token"] = bb_token
        if bb_username:
            discovered["bitbucket_username"] = bb_username

    # AWS
    aws_key, aws_secret, aws_profile = get_aws_credentials()
    if aws_key and aws_secret:
        discovered["aws_access_key_id"] = aws_key
        discovered["aws_secret_access_key"] = aws_secret
    elif aws_profile:
        discovered["aws_profile"] = aws_profile

    return discovered
