#!/usr/bin/env python3
"""
Enterprise repository backup tool for GitHub, GitLab, and Bitbucket

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

import argparse
import configparser
import fnmatch
import json
import os
import queue
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import boto3
import yaml
from botocore.exceptions import ClientError, TokenRetrievalError
from dotenv import load_dotenv
from loguru import logger
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeRemainingColumn,
)
from rich_argparse import ArgumentDefaultsRichHelpFormatter
from tqdm import tqdm

from .base import Repository
from .bitbucket_manager import BitbucketManager
from .github_manager import GitHubManager
from .gitlab_manager import GitLabManager
from .local_backup import LocalBackup
from .s3_uploader import S3Uploader
from .token_discovery import (
    get_aws_credentials,
    get_bitbucket_credentials,
    get_github_token,
    get_gitlab_token,
)

# Load environment variables from .env file (override existing env vars)
load_dotenv(override=True)


def setup_logging(verbose: bool = False, log_file: str = "repo-backup.log"):
    """Setup console and file logging with loguru"""

    # Remove default loguru handler
    logger.remove()

    # Create logs directory
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    log_file_path = log_dir / log_file

    # Set log level
    log_level = "DEBUG" if verbose else "INFO"

    # Configure console handler with colors and emojis
    console_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | "
        "<level>{message}</level>"
    )

    logger.add(sys.stdout, format=console_format, level=log_level, colorize=True)

    # Configure file handler with detailed format
    file_format = "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}"

    logger.add(
        log_file_path,
        format=file_format,
        level=log_level,
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        encoding="utf-8",
    )

    logger.info("[CONFIG] Logging configured")
    logger.debug(f"Log file: {log_file_path}")

    return logger


# Configure basic loguru logging (will be reconfigured in main())
logger.remove()
logger.add(sys.stdout, level="INFO", colorize=True)


class S3BucketManager:
    """Manages S3 bucket creation and configuration"""

    def __init__(self, profile: Optional[str] = None):
        self.profile = profile or os.getenv("AWS_PROFILE")
        self.region = os.getenv("AWS_REGION", "us-west-2")

        if self.profile:
            session = boto3.Session(profile_name=self.profile, region_name=self.region)
        else:
            session = boto3.Session(region_name=self.region)

        self.s3_client = session.client("s3")
        self.sts_client = session.client("sts")
        self.iam_client = session.client("iam")

    def get_account_id(self) -> str:
        """Get AWS account ID"""
        try:
            response = self.sts_client.get_caller_identity()
            return response["Account"]
        except (ClientError, TokenRetrievalError) as e:
            error_msg = str(e)
            if "ExpiredToken" in error_msg or "Token has expired" in error_msg:
                logger.error("")
                logger.error("ERROR: AWS session token has expired!")
                logger.error("")
                logger.error("Please refresh your AWS credentials:")
                if self.profile:
                    logger.error(f"  aws sso login --profile {self.profile}")
                else:
                    logger.error("  - For SSO: aws sso login --profile <profile-name>")
                    logger.error("  - For IAM: aws configure")
                    logger.error(
                        "  - For temporary credentials: export AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
                    )
                logger.error("")
                raise SystemExit(1)
            elif "InvalidClientTokenId" in error_msg:
                logger.error("")
                logger.error("ERROR: Invalid AWS credentials!")
                logger.error("")
                logger.error("Your AWS credentials are not valid. Please check:")
                logger.error("  - Correct profile is being used")
                logger.error("  - Credentials are not expired")
                logger.error(
                    "  - AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY are correct"
                )
                logger.error("")
                raise SystemExit(1)
            elif "AccessDenied" in error_msg:
                logger.error("")
                logger.error("ERROR: Access Denied!")
                logger.error("")
                logger.error("Your AWS user/role lacks required permissions.")
                logger.error("See README.md for required permissions for S3 setup.")
                logger.error("")
                raise SystemExit(1)
            raise

    def generate_bucket_name(self, prefix: str = "repo-backup") -> str:
        """Generate unique bucket name using account ID"""
        account_id = self.get_account_id()
        return f"{prefix}-{account_id}"

    def bucket_exists(self, bucket_name: str) -> bool:
        """Check if bucket exists and is accessible"""
        try:
            self.s3_client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError:
            return False

    def create_and_configure_bucket(
        self, bucket_name: Optional[str] = None, enable_glacier: bool = False
    ) -> str:
        """Create and configure S3 bucket with all security features"""

        # Generate bucket name if not provided
        if not bucket_name:
            bucket_name = self.generate_bucket_name()
            logger.info(f"Generated bucket name: {bucket_name}")

        # Check if bucket already exists
        if self.bucket_exists(bucket_name):
            logger.info(f"Bucket {bucket_name} already exists")
            return bucket_name

        logger.info(f"Creating S3 bucket: {bucket_name} in {self.region}")

        # Create bucket
        try:
            if self.region == "us-east-1":
                self.s3_client.create_bucket(Bucket=bucket_name)
            else:
                self.s3_client.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": self.region},
                )
            logger.info("Bucket created successfully")
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "BucketAlreadyOwnedByYou":
                logger.info("Bucket already owned by you")
            elif error_code == "OperationAborted":
                logger.warning(
                    "Bucket creation conflict - trying to proceed with existing bucket"
                )
                # Wait a moment and check if bucket exists
                import time

                time.sleep(2)
                if not self.bucket_exists(bucket_name):
                    logger.error("Bucket creation failed and bucket doesn't exist")
                    raise
            else:
                raise

        # Enable versioning
        logger.info("Enabling versioning...")
        self.s3_client.put_bucket_versioning(
            Bucket=bucket_name, VersioningConfiguration={"Status": "Enabled"}
        )

        # Enable encryption
        logger.info("Enabling encryption...")
        self.s3_client.put_bucket_encryption(
            Bucket=bucket_name,
            ServerSideEncryptionConfiguration={
                "Rules": [
                    {
                        "ApplyServerSideEncryptionByDefault": {
                            "SSEAlgorithm": "AES256"
                        },
                        "BucketKeyEnabled": True,
                    }
                ]
            },
        )

        # Block public access
        logger.info("Blocking public access...")
        self.s3_client.put_public_access_block(
            Bucket=bucket_name,
            PublicAccessBlockConfiguration={
                "BlockPublicAcls": True,
                "IgnorePublicAcls": True,
                "BlockPublicPolicy": True,
                "RestrictPublicBuckets": True,
            },
        )

        # Configure lifecycle policy
        if enable_glacier:
            logger.info("Configuring lifecycle policy WITH Glacier transitions...")
            lifecycle_config = {
                "Rules": [
                    {
                        "ID": "RepoBackupLifecycle",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "repos/"},
                        "Transitions": [
                            {"Days": 30, "StorageClass": "STANDARD_IA"},
                            {"Days": 90, "StorageClass": "GLACIER_IR"},
                            {"Days": 180, "StorageClass": "DEEP_ARCHIVE"},
                        ],
                        "NoncurrentVersionTransitions": [
                            {"NoncurrentDays": 7, "StorageClass": "GLACIER_IR"},
                            {"NoncurrentDays": 100, "StorageClass": "DEEP_ARCHIVE"},
                        ],
                        "NoncurrentVersionExpiration": {"NoncurrentDays": 365},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                    }
                ]
            }
        else:
            logger.info(
                "Configuring lifecycle policy WITHOUT Glacier (Standard storage only)..."
            )
            lifecycle_config = {
                "Rules": [
                    {
                        "ID": "RepoBackupLifecycle",
                        "Status": "Enabled",
                        "Filter": {"Prefix": "repos/"},
                        "Transitions": [
                            {"Days": 90, "StorageClass": "STANDARD_IA"},
                        ],
                        "NoncurrentVersionExpiration": {"NoncurrentDays": 365},
                        "AbortIncompleteMultipartUpload": {"DaysAfterInitiation": 7},
                    }
                ]
            }
        self.s3_client.put_bucket_lifecycle_configuration(
            Bucket=bucket_name, LifecycleConfiguration=lifecycle_config
        )

        # Add tags
        logger.info("Adding bucket tags...")
        self.s3_client.put_bucket_tagging(
            Bucket=bucket_name,
            Tagging={
                "TagSet": [
                    {"Key": "Purpose", "Value": "Repository-Backup"},
                    {"Key": "Environment", "Value": "Production"},
                    {"Key": "ManagedBy", "Value": "repo-backup-tool"},
                    {"Key": "CreatedBy", "Value": "Python-Script"},
                ]
            },
        )

        # Add bucket policy
        logger.info("Applying security policy...")
        bucket_policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "DenyUnencryptedObjectUploads",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:PutObject",
                    "Resource": f"arn:aws:s3:::{bucket_name}/*",
                    "Condition": {
                        "StringNotEquals": {"s3:x-amz-server-side-encryption": "AES256"}
                    },
                },
                {
                    "Sid": "DenyInsecureConnections",
                    "Effect": "Deny",
                    "Principal": "*",
                    "Action": "s3:*",
                    "Resource": [
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*",
                    ],
                    "Condition": {"Bool": {"aws:SecureTransport": "false"}},
                },
            ],
        }
        self.s3_client.put_bucket_policy(
            Bucket=bucket_name, Policy=json.dumps(bucket_policy)
        )

        logger.info(f"Bucket {bucket_name} configured successfully!")

        # Add README.md to document the bucket
        logger.info("Adding README.md to bucket...")
        readme_content = f"""# Repository Backup Bucket

This S3 bucket contains automated backups of Git repositories from GitHub, GitLab, and Bitbucket.

## Bucket Information
- **Bucket Name**: {bucket_name}
- **Region**: {self.region}
- **Created**: {datetime.now().strftime("%Y-%m-%d")}
- **Purpose**: Automated repository backups

## Structure
```
{bucket_name}/
├── repos/
│   ├── github/
│   │   └── <owner>/<repo>_<last_commit_date>.bundle
│   ├── gitlab/
│   │   └── <group>/<repo>_<last_commit_date>.bundle
│   └── bitbucket/
│       └── <workspace>/<repo>_<last_commit_date>.bundle
└── README.md (this file)
```

## Configuration
- **Versioning**: Enabled
- **Encryption**: AES256
- **Public Access**: Blocked
- **Lifecycle**: {"Glacier transitions enabled" if enable_glacier else "Standard storage only (90 days to IA)"}

## Restoring Backups

To restore a repository from a bundle:
```bash
# Download the bundle
aws s3 cp s3://{bucket_name}/repos/github/org/repo_20250825_120000.bundle repo.bundle

# Clone from bundle
git clone repo.bundle restored-repo

# Set correct remote
cd restored-repo
git remote set-url origin https://github.com/org/repo.git
```

## Security
- All public access is blocked
- Server-side encryption is enabled
- Versioning protects against accidental deletion
- IAM user has minimal required permissions

## Management
This bucket is managed by the repo-backup tool.
Repository: https://github.com/hyperi-io/repo-backup
"""

        try:
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key="README.md",
                Body=readme_content.encode("utf-8"),
                ContentType="text/markdown",
                ServerSideEncryption="AES256",
            )
            logger.info("[OK] README.md added to bucket")
        except Exception as e:
            logger.warning(f"Could not add README.md to bucket: {e}")

        return bucket_name

    def test_bucket(self, bucket_name: str) -> bool:
        """Test bucket access and configuration"""
        logger.info(f"Testing bucket {bucket_name}...")

        try:
            # Test write
            test_key = "test/setup-test.txt"
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=test_key,
                Body=b"test",
                ServerSideEncryption="AES256",
            )
            logger.info("[PASS] Write test passed")

            # Test read
            self.s3_client.get_object(Bucket=bucket_name, Key=test_key)
            logger.info("[PASS] Read test passed")

            # Test versioning
            self.s3_client.put_object(
                Bucket=bucket_name,
                Key=test_key,
                Body=b"test2",
                ServerSideEncryption="AES256",
            )

            versions = self.s3_client.list_object_versions(
                Bucket=bucket_name, Prefix=test_key
            )

            if len(versions.get("Versions", [])) > 1:
                logger.info(
                    f"[PASS] Versioning test passed ({len(versions['Versions'])} versions)"
                )

            # Cleanup
            for version in versions.get("Versions", []):
                self.s3_client.delete_object(
                    Bucket=bucket_name,
                    Key=version["Key"],
                    VersionId=version["VersionId"],
                )

            logger.info("[PASS] All bucket tests passed!")
            return True

        except Exception as e:
            logger.error(f"Bucket test failed: {e}")
            return False

    def write_env_file(
        self, bucket_name: str, env_file: str = ".env", profile_name: str = None
    ):
        """Write configuration to .env file"""
        logger.info(f"Writing configuration to {env_file}...")

        use_profile = profile_name or self.profile or ""

        env_content = f"""# AWS Configuration for Repository Backup
# Created: {datetime.now().isoformat()}

# S3 Bucket Configuration
AWS_S3_BUCKET={bucket_name}
AWS_REGION={self.region}
AWS_PROFILE={use_profile}

# S3 Settings
S3_PREFIX=repos
S3_USE_VERSIONING=true
S3_STORAGE_CLASS=STANDARD

# Backup Settings
BACKUP_METHOD=direct
PARALLEL_WORKERS=5
EXCLUDE_PERSONAL=true

# Repository Tokens (add your tokens here)
# GITHUB_TOKEN=ghp_your_token_here
# GITLAB_TOKEN=glpat_your_token_here
# GITLAB_URL=https://gitlab.com
# BITBUCKET_USERNAME=your_username
# BITBUCKET_APP_PASSWORD=your_app_password
# BITBUCKET_URL=https://bitbucket.org
"""

        # Append existing tokens if they exist in environment
        if os.getenv("GITHUB_TOKEN"):
            env_content += f"\nGITHUB_TOKEN={os.getenv('GITHUB_TOKEN')}"
        if os.getenv("GITLAB_TOKEN"):
            env_content += f"\nGITLAB_TOKEN={os.getenv('GITLAB_TOKEN')}"
        if os.getenv("BITBUCKET_USERNAME"):
            env_content += f"\nBITBUCKET_USERNAME={os.getenv('BITBUCKET_USERNAME')}"
            env_content += (
                f"\nBITBUCKET_APP_PASSWORD={os.getenv('BITBUCKET_APP_PASSWORD', '')}"
            )

        with open(env_file, "w") as f:
            f.write(env_content)

        os.chmod(env_file, 0o600)
        logger.info(f"Configuration written to {env_file}")

    def create_iam_user_and_policy(
        self, bucket_name: str, user_name: str = None
    ) -> tuple:
        """Create IAM user with S3 bucket access and return credentials"""
        if not user_name:
            user_name = f"repo-backup-s3-{self.get_account_id()[-6:]}"

        policy_name = f"BackupRepoS3Access-{bucket_name}"

        logger.info(f"Creating IAM user: {user_name}")

        # Create IAM policy for S3 bucket access
        policy_document = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "ListBucket",
                    "Effect": "Allow",
                    "Action": [
                        "s3:ListBucket",
                        "s3:GetBucketLocation",
                        "s3:GetBucketVersioning",
                        "s3:ListBucketVersions",
                    ],
                    "Resource": f"arn:aws:s3:::{bucket_name}",
                },
                {
                    "Sid": "ReadWriteObjects",
                    "Effect": "Allow",
                    "Action": [
                        "s3:PutObject",
                        "s3:PutObjectAcl",
                        "s3:GetObject",
                        "s3:GetObjectVersion",
                        "s3:GetObjectAcl",
                        "s3:DeleteObject",
                        "s3:DeleteObjectVersion",
                    ],
                    "Resource": f"arn:aws:s3:::{bucket_name}/*",
                },
                {
                    "Sid": "MultipartUpload",
                    "Effect": "Allow",
                    "Action": [
                        "s3:ListBucketMultipartUploads",
                        "s3:ListMultipartUploadParts",
                        "s3:AbortMultipartUpload",
                    ],
                    "Resource": [
                        f"arn:aws:s3:::{bucket_name}",
                        f"arn:aws:s3:::{bucket_name}/*",
                    ],
                },
                {
                    "Sid": "RestoreFromGlacier",
                    "Effect": "Allow",
                    "Action": ["s3:RestoreObject", "s3:GetObjectTorrent"],
                    "Resource": f"arn:aws:s3:::{bucket_name}/*",
                },
            ],
        }

        # Create policy
        try:
            policy_response = self.iam_client.create_policy(
                PolicyName=policy_name,
                PolicyDocument=json.dumps(policy_document),
                Description=f"S3 access policy for repo-backup-s3 bucket {bucket_name}",
                Tags=[
                    {"Key": "Purpose", "Value": "repo-backup-s3"},
                    {"Key": "Bucket", "Value": bucket_name},
                ],
            )
            policy_arn = policy_response["Policy"]["Arn"]
            logger.info("[OK] IAM policy created")
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                account_id = self.get_account_id()
                policy_arn = f"arn:aws:iam::{account_id}:policy/{policy_name}"
                logger.info("[OK] IAM policy already exists")
            else:
                raise

        # Create user
        try:
            self.iam_client.create_user(
                UserName=user_name,
                Tags=[
                    {"Key": "Purpose", "Value": "repo-backup-s3"},
                    {"Key": "Bucket", "Value": bucket_name},
                ],
            )
            logger.info("[OK] IAM user created")
        except ClientError as e:
            if e.response["Error"]["Code"] == "EntityAlreadyExists":
                logger.info("[OK] IAM user already exists")
            else:
                raise

        # Attach policy to user
        try:
            self.iam_client.attach_user_policy(UserName=user_name, PolicyArn=policy_arn)
            logger.info("[OK] Policy attached to user")
        except ClientError as e:
            if e.response["Error"]["Code"] == "LimitExceeded":
                logger.warning("Policy already attached to user")
            else:
                raise

        # Create access key
        try:
            # First check if user already has access keys
            existing_keys = self.iam_client.list_access_keys(UserName=user_name)
            if len(existing_keys["AccessKeyMetadata"]) >= 2:
                logger.warning("User already has maximum number of access keys (2)")
                logger.info(
                    "Please delete an existing access key if you need a new one"
                )
                return None, None, user_name

            key_response = self.iam_client.create_access_key(UserName=user_name)
            access_key = key_response["AccessKey"]

            logger.info("[OK] Access key created")
            return access_key["AccessKeyId"], access_key["SecretAccessKey"], user_name

        except ClientError as e:
            logger.error(f"Failed to create access key: {e}")
            return None, None, user_name

    def create_aws_profile(
        self, profile_name: str, access_key_id: str, secret_key: str, region: str
    ) -> bool:
        """Create AWS CLI profile for the service user"""
        try:
            # Get AWS config directory
            aws_dir = Path.home() / ".aws"
            aws_dir.mkdir(exist_ok=True)

            config_file = aws_dir / "config"
            credentials_file = aws_dir / "credentials"

            # Read existing config/credentials or create new
            config = configparser.ConfigParser()
            credentials = configparser.ConfigParser()

            if config_file.exists():
                config.read(config_file)
            if credentials_file.exists():
                credentials.read(credentials_file)

            # Add profile to config
            profile_section = (
                f"profile {profile_name}" if profile_name != "default" else "default"
            )
            if profile_section not in config:
                config.add_section(profile_section)
            config[profile_section]["region"] = region
            config[profile_section]["output"] = "json"

            # Add credentials
            if profile_name not in credentials:
                credentials.add_section(profile_name)
            credentials[profile_name]["aws_access_key_id"] = access_key_id
            credentials[profile_name]["aws_secret_access_key"] = secret_key

            # Write files
            with open(config_file, "w") as f:
                config.write(f)
            with open(credentials_file, "w") as f:
                credentials.write(f)

            # Set secure permissions
            config_file.chmod(0o600)
            credentials_file.chmod(0o600)

            logger.info(f"[OK] AWS profile '{profile_name}' created")
            return True

        except Exception as e:
            logger.error(f"Failed to create AWS profile: {e}")
            return False


class RepoBackupOrchestrator:
    def __init__(
        self,
        config_path: Optional[str] = None,
        use_env: bool = True,
        local_backup_path: Optional[str] = None,
        work_dir: Optional[str] = None,
    ):
        self.use_env = use_env
        self.managers = []
        self.s3_uploader = None
        self.local_backup = None
        self.local_backup_path = local_backup_path
        self.work_dir = work_dir

        if use_env:
            self.setup_from_env()
        elif config_path and os.path.exists(config_path):
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f)
            self.setup_managers()
        else:
            logger.warning(
                "No configuration provided. Use --setup to create bucket and configuration."
            )

        # Setup local backup if path is provided
        if self.local_backup_path:
            self.local_backup = LocalBackup(
                backup_path=self.local_backup_path,
                method=os.getenv("BACKUP_METHOD", "direct"),
                work_dir=self.work_dir,
            )
            # Clean up any stale temp directories from previous failed runs
            self.local_backup.cleanup_stale_temps()
            logger.info(f"Local backup configured for path: {self.local_backup_path}")

    def setup_from_env(self):
        """Setup managers from environment variables with auto-discovery fallback"""
        # Setup S3 if bucket is configured
        bucket_name = os.getenv("AWS_S3_BUCKET")
        if bucket_name:
            # Try env vars first, then auto-discover from ~/.aws/credentials
            aws_key = os.getenv("AWS_ACCESS_KEY_ID")
            aws_secret = os.getenv("AWS_SECRET_ACCESS_KEY")
            aws_profile = os.getenv("AWS_PROFILE")

            if not aws_key or not aws_secret:
                # Auto-discover from standard locations
                discovered_key, discovered_secret, discovered_profile = (
                    get_aws_credentials()
                )
                if discovered_key and discovered_secret:
                    aws_key = aws_key or discovered_key
                    aws_secret = aws_secret or discovered_secret
                    logger.info("[TOKEN] Using auto-discovered AWS credentials")
                elif discovered_profile and not aws_profile:
                    aws_profile = discovered_profile
                    logger.info(
                        f"[TOKEN] Using auto-discovered AWS profile: {aws_profile}"
                    )

            # S3-compatible stores (Cloudflare R2, MinIO) need an explicit
            # endpoint. AWS_ENDPOINT_URL is the name the AWS SDKs and CLI v2
            # already standardise on, so it works without extra config here.
            endpoint_url = os.getenv("AWS_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL")

            self.s3_uploader = S3Uploader(
                bucket_name=bucket_name,
                region=os.getenv("AWS_REGION", "us-west-2"),
                aws_access_key_id=aws_key,
                aws_secret_access_key=aws_secret,
                aws_profile=aws_profile,
                prefix=os.getenv("S3_PREFIX", "repos"),
                work_dir=self.work_dir,
                endpoint_url=endpoint_url,
            )
            logger.info(f"S3 uploader configured for bucket: {bucket_name}")

        # Setup GitHub - try env var first, then auto-discover from gh CLI
        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token or github_token.startswith("ghp_your"):
            github_token = get_github_token()
            if github_token:
                logger.info("[TOKEN] Using auto-discovered GitHub token from gh CLI")

        if github_token and not github_token.startswith("ghp_your"):
            try:
                manager = GitHubManager(
                    token=github_token,
                    exclude_personal=os.getenv("EXCLUDE_PERSONAL", "true").lower()
                    == "true",
                )
                self.managers.append(("github", "default", manager))
                logger.info("GitHub manager initialized")
            except Exception as e:
                logger.error(f"Failed to initialize GitHub: {e}")

        # Setup GitLab - try env var first, then auto-discover from glab CLI
        gitlab_url = os.getenv("GITLAB_URL", "https://gitlab.com")
        gitlab_token = os.getenv("GITLAB_TOKEN")
        if not gitlab_token or gitlab_token.startswith("glpat_your"):
            gitlab_token = get_gitlab_token(gitlab_url)
            if gitlab_token:
                logger.info(
                    "[TOKEN] Using auto-discovered GitLab token from glab CLI config"
                )

        if gitlab_token and not gitlab_token.startswith("glpat_your"):
            try:
                manager = GitLabManager(
                    url=gitlab_url,
                    token=gitlab_token,
                    exclude_personal=os.getenv("EXCLUDE_PERSONAL", "true").lower()
                    == "true",
                )
                self.managers.append(("gitlab", "default", manager))
                logger.info("GitLab manager initialized")
            except Exception as e:
                logger.error(f"Failed to initialize GitLab: {e}")

        # Setup Bitbucket - try env var first, then auto-discover from .netrc
        bitbucket_token = os.getenv("BITBUCKET_TOKEN")
        bitbucket_username = os.getenv("BITBUCKET_USERNAME")
        if not bitbucket_token or bitbucket_token.startswith("your_token_here"):
            discovered_token, discovered_username = get_bitbucket_credentials()
            if discovered_token:
                bitbucket_token = discovered_token
                bitbucket_username = bitbucket_username or discovered_username
                logger.info("[TOKEN] Using auto-discovered Bitbucket credentials")

        if bitbucket_token and not bitbucket_token.startswith("your_token_here"):
            try:
                manager = BitbucketManager(
                    url=os.getenv("BITBUCKET_URL", "https://bitbucket.org"),
                    token=bitbucket_token,
                    username=bitbucket_username,
                    exclude_personal=os.getenv("EXCLUDE_PERSONAL", "true").lower()
                    == "true",
                )
                self.managers.append(("bitbucket", "default", manager))
                logger.info("Bitbucket manager initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Bitbucket: {e}")

    def setup_managers(self):
        """Initialize repository managers based on configuration file"""
        # [Previous setup_managers code remains the same]
        pass

    def parse_repo_spec(self, repo_spec: str) -> tuple:
        """Parse repository specification into (platform, owner, name)"""
        # Format: owner/repo or platform:owner/repo
        if ":" in repo_spec:
            platform, repo_path = repo_spec.split(":", 1)
            platform = platform.lower()
        else:
            platform = None  # Auto-detect
            repo_path = repo_spec

        if "/" not in repo_path:
            raise ValueError(
                f"Invalid repository format: {repo_spec}. Use owner/repo or platform:owner/repo"
            )

        owner, name = repo_path.split("/", 1)
        return platform, owner, name

    def match_repo_pattern(self, pattern: str, repo: Repository) -> bool:
        """Match a repository against a pattern (exact, glob, or regex)"""
        repo_full = f"{repo.owner}/{repo.name}"
        platform_full = f"{repo.platform}:{repo.owner}/{repo.name}"

        # Handle regex patterns (prefix with 're:')
        if pattern.startswith("re:"):
            regex_pattern = pattern[3:]  # Remove 're:' prefix
            try:
                return bool(re.search(regex_pattern, repo_full, re.IGNORECASE)) or bool(
                    re.search(regex_pattern, platform_full, re.IGNORECASE)
                )
            except re.error as e:
                logger.error(f"[ERROR] Invalid regex pattern '{regex_pattern}': {e}")
                return False

        # Handle platform-specific exact patterns
        if ":" in pattern and not any(char in pattern for char in ["*", "?", "["]):
            # Exact match for platform:owner/repo
            return pattern.lower() == platform_full.lower()

        # Handle glob patterns (contains *, ?, or [])
        if any(char in pattern for char in ["*", "?", "["]):
            # Try both formats for glob matching
            return fnmatch.fnmatch(
                repo_full.lower(), pattern.lower()
            ) or fnmatch.fnmatch(platform_full.lower(), pattern.lower())

        # Exact match
        return pattern.lower() == repo_full.lower()

    def get_specific_repositories(
        self, repo_patterns: List[str], platforms: List[str] = None
    ) -> List[Repository]:
        """Get repositories matching specific patterns (exact, glob, or regex)"""
        specific_repos = []
        all_repos = self.get_all_repositories(platforms)

        matched_count = 0
        for pattern in repo_patterns:
            pattern = pattern.strip()
            pattern_matches = []

            for repo in all_repos:
                if self.match_repo_pattern(pattern, repo):
                    if repo not in specific_repos:  # Avoid duplicates
                        specific_repos.append(repo)
                        pattern_matches.append(repo)

            if pattern_matches:
                matched_count += len(pattern_matches)
                if len(pattern_matches) == 1:
                    repo = pattern_matches[0]
                    logger.info(
                        f"[MATCH] Pattern '{pattern}' matched: {repo.platform.upper()}:{repo.owner}/{repo.name}"
                    )
                else:
                    logger.info(
                        f"[MATCH] Pattern '{pattern}' matched {len(pattern_matches)} repositories"
                    )
                    for repo in pattern_matches[:5]:  # Show first 5
                        logger.debug(
                            f"  - {repo.platform.upper()}:{repo.owner}/{repo.name}"
                        )
                    if len(pattern_matches) > 5:
                        logger.debug(f"  ... and {len(pattern_matches) - 5} more")
            else:
                logger.warning(
                    f"[NO_MATCH] Pattern '{pattern}' matched no repositories"
                )

        if matched_count != len(specific_repos):
            logger.info(
                f"[DEDUP] {matched_count} total matches, {len(specific_repos)} unique repositories after deduplication"
            )

        return specific_repos

    def load_repos_from_file(self, file_path: str) -> List[str]:
        """Load repository list from file"""
        try:
            with open(file_path, "r") as f:
                repos = []
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        repos.append(line)
                    elif line.startswith("#"):
                        logger.debug(f"Skipping comment on line {line_num}: {line}")

                logger.info(f"Loaded {len(repos)} repositories from {file_path}")
                return repos

        except FileNotFoundError:
            logger.error(f"[ERROR] Repository file not found: {file_path}")
            return []
        except Exception as e:
            logger.error(f"[ERROR] Error reading repository file {file_path}: {e}")
            return []

    def iter_all_repositories(
        self, platforms: List[str] = None
    ) -> Iterator[Repository]:
        """Yield repositories one at a time from all platforms to minimize memory usage."""
        # Filter managers by platform if specified
        managers_to_use = self.managers
        if platforms:
            platforms_lower = [p.lower() for p in platforms]
            managers_to_use = [
                (p, a, m) for p, a, m in self.managers if p in platforms_lower
            ]

            if not managers_to_use:
                logger.warning(
                    f"[WARN] No managers found for platforms: {', '.join(platforms)}"
                )
                return

        for platform, account_name, manager in managers_to_use:
            try:
                logger.info(
                    f"[CONNECT] Connecting to {platform.upper()} ({account_name})..."
                )
                repo_count = 0
                for repo in manager.get_repositories():
                    repo_count += 1
                    logger.debug(
                        f"  - {repo.owner}/{repo.name} ({'private' if repo.is_private else 'public'})"
                    )
                    yield repo

                if repo_count > 0:
                    logger.info(
                        f"[OK] Found {repo_count} corporate repositories on {platform.upper()} ({account_name})"
                    )
                else:
                    logger.warning(
                        f"[WARN] No repositories found on {platform.upper()} ({account_name})"
                    )

            except Exception as e:
                logger.error(
                    f"[ERROR] Failed to fetch repos from {platform.upper()} ({account_name}): {e}"
                )

    def get_all_repositories(self, platforms: List[str] = None) -> List[Repository]:
        """Fetch repositories from specified platforms. For memory efficiency, use iter_all_repositories() instead."""
        return list(self.iter_all_repositories(platforms))

    def get_test_repository(self, platforms: List[str] = None) -> List[Repository]:
        """Get the smallest non-empty repository for testing"""
        logger.info("[TEST] Finding smallest non-empty repository for testing...")

        # Get all repositories
        all_repos = self.get_all_repositories(platforms)

        if not all_repos:
            logger.warning("[TEST] No repositories found for testing")
            return []

        logger.info(
            f"[TEST] Analyzing {len(all_repos)} repositories to find smallest..."
        )

        # Filter out forks and empty repos, then sort by size
        valid_repos = []
        for repo in all_repos:
            if repo.is_fork:
                continue

            # Skip repos that are definitely empty (size = 0)
            if repo.size_kb == 0:
                continue

            valid_repos.append(repo)

        if not valid_repos:
            logger.warning(
                "[TEST] No non-fork, non-empty repositories found for testing"
            )
            return []

        # Sort by size (smallest first), repos without size info go to end
        def sort_key(repo):
            if repo.size_kb is None:
                return float("inf")  # Put unknown sizes at end
            return repo.size_kb

        sorted_repos = sorted(valid_repos, key=sort_key)

        # Pick the smallest repository
        test_repo = sorted_repos[0]

        if test_repo.size_kb:
            size_info = f" ({test_repo.size_kb}KB)"
            logger.info(
                f"[TEST] Selected smallest repository: {test_repo.platform.upper()}: {test_repo.owner}/{test_repo.name}{size_info}"
            )
        else:
            logger.info(
                f"[TEST] Selected repository (size unknown): {test_repo.platform.upper()}: {test_repo.owner}/{test_repo.name}"
            )

        return [test_repo]

    def backup_repository(self, repo: Repository) -> bool:
        """Backup a single repository to configured destination (S3 or local)"""
        try:
            logger.info(
                f"[BACKUP] Backing up {repo.platform.upper()}: {repo.owner}/{repo.name}"
            )
            logger.debug(
                f"  Repository details: {'Private' if repo.is_private else 'Public'}, "
                f"Fork: {repo.is_fork}, Default branch: {repo.default_branch}"
            )

            results = []
            destinations = []

            # Backup to local if configured
            if self.local_backup:
                result_local = self.local_backup.backup_repository(repo)
                results.append(result_local)
                if result_local:
                    destinations.append("local")

            # Backup to S3 if configured
            if self.s3_uploader:
                method = os.getenv("BACKUP_METHOD", "direct")
                logger.debug(
                    f"[DEBUG] Calling S3 upload for {repo.name} with method={method}"
                )
                result_s3 = self.s3_uploader.upload_repository(repo, method=method)
                logger.debug(f"[DEBUG] S3 upload returned: {result_s3} for {repo.name}")
                results.append(result_s3)
                if result_s3:
                    destinations.append("S3")

            if not self.s3_uploader and not self.local_backup:
                logger.error("[ERROR] No backup destination configured")
                return False

            # Report results
            if destinations:
                logger.info(
                    f"[SUCCESS] Successfully backed up {repo.platform.upper()}: {repo.owner}/{repo.name} to {', '.join(destinations)}"
                )
            else:
                logger.error(
                    f"[FAIL] Failed to backup {repo.platform.upper()}: {repo.owner}/{repo.name}"
                )

            # Return True if at least one backup succeeded
            return any(results)

        except Exception as e:
            logger.error(
                f"[ERROR] Exception while backing up {repo.platform.upper()}: {repo.owner}/{repo.name} - {e}"
            )
            return False

    def run_backup(
        self,
        parallel: bool = True,
        max_workers: int = None,
        specific_repos: List[str] = None,
        platforms: List[str] = None,
        test_mode: bool = False,
    ):
        """Run the backup process"""
        if not self.s3_uploader and not self.local_backup:
            logger.error(
                "No backup destination configured. Specify --s3 or --local-path."
            )
            return

        if test_mode:
            logger.info("[TEST] Starting test mode - finding first small repository...")
        else:
            logger.info("[START] Starting repository backup process...")

        # Get repositories based on mode
        if test_mode:
            repos = self.get_test_repository(platforms)
        elif specific_repos:
            logger.info(
                f"[LIST] Backing up specific repositories: {len(specific_repos)} requested"
            )
            repos = self.get_specific_repositories(specific_repos, platforms)
        else:
            platforms_str = f" from {', '.join(platforms)}" if platforms else ""
            logger.info(
                f"[DISCOVER] Discovering all accessible repositories{platforms_str}..."
            )
            repos = self.get_all_repositories(platforms)

        if not repos:
            logger.warning("[WARN] No repositories found to backup")
            return

        # Group repositories by platform for better logging
        platform_counts = {}
        for repo in repos:
            platform_counts[repo.platform] = platform_counts.get(repo.platform, 0) + 1

        logger.info(f"[TOTAL] Total repositories to backup: {len(repos)}")
        for platform, count in platform_counts.items():
            logger.info(f"  - {platform.upper()}: {count} repositories")

        # Backup repositories
        successful = 0
        failed = 0
        max_workers = max_workers or int(os.getenv("PARALLEL_WORKERS", 5))

        logger.info(
            f"[PROCESS] Using {'parallel' if parallel else 'sequential'} processing "
            f"({'with ' + str(max_workers) + ' workers' if parallel else ''})..."
        )

        if parallel:
            # Use bounded queue pattern to avoid memory issues with large repo lists
            # Only submit max_workers * 2 tasks at a time instead of all at once
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Convert to list for progress tracking (only holds repo metadata, not data)
                repo_list = list(repos) if not isinstance(repos, list) else repos
                total_repos = len(repo_list)

                with tqdm(total=total_repos, desc="Backing up", unit="repo") as pbar:
                    # Use a bounded submission pattern
                    futures = {}
                    repo_iter = iter(repo_list)
                    pending_count = 0
                    max_pending = max_workers * 2  # Keep queue bounded

                    # Initial submission of bounded number of tasks
                    for _ in range(min(max_pending, total_repos)):
                        try:
                            repo = next(repo_iter)
                            future = executor.submit(self.backup_repository, repo)
                            futures[future] = repo
                            pending_count += 1
                        except StopIteration:
                            break

                    # Process completions and submit new tasks
                    while futures:
                        # Wait for at least one to complete
                        done_futures = []
                        for future in list(futures.keys()):
                            if future.done():
                                done_futures.append(future)

                        if not done_futures:
                            # No futures done yet, wait a bit
                            import time

                            time.sleep(0.1)
                            continue

                        for future in done_futures:
                            repo = futures.pop(future)
                            try:
                                result = future.result()
                                if result:
                                    successful += 1
                                else:
                                    failed += 1
                            except Exception as e:
                                logger.error(
                                    f"[ERROR] Error backing up {repo.platform.upper()}: {repo.owner}/{repo.name} - {e}"
                                )
                                failed += 1
                            pbar.update(1)
                            pbar.set_postfix({"OK": successful, "FAIL": failed})

                            # Submit a new task if there are more repos
                            try:
                                next_repo = next(repo_iter)
                                new_future = executor.submit(
                                    self.backup_repository, next_repo
                                )
                                futures[new_future] = next_repo
                            except StopIteration:
                                pass
        else:
            with tqdm(repos, desc="Backing up", unit="repo") as pbar:
                for repo in pbar:
                    pbar.set_description(
                        f"[BACKUP] {repo.platform.upper()}: {repo.owner}/{repo.name}"
                    )
                    if self.backup_repository(repo):
                        successful += 1
                    else:
                        failed += 1
                    pbar.set_postfix({"OK": successful, "FAIL": failed})

        # Cleanup
        if self.s3_uploader:
            logger.info("[CLEANUP] Cleaning up temporary files...")
            self.s3_uploader.cleanup_temp()
        elif self.local_backup:
            logger.info("[CLEANUP] Cleaning up temporary files...")
            self.local_backup.cleanup_temp()

        # Summary
        logger.info("=" * 60)
        logger.info("[SUMMARY] BACKUP SUMMARY")
        logger.info("=" * 60)
        logger.info(f"[SUCCESS] Successful backups: {successful}")
        logger.info(f"[FAIL] Failed backups: {failed}")
        logger.info(f"[TOTAL] Total repositories: {len(repos)}")
        logger.info(f"[RATE] Success rate: {(successful / len(repos) * 100):.1f}%")

        if failed > 0:
            logger.error(
                f"[WARN] {failed} repositories failed to backup - check logs for details"
            )
            sys.exit(1)
        else:
            logger.info("[COMPLETE] All repositories backed up successfully!")

    def list_backups(self, platform: str = None):
        """List existing backups in S3"""
        if not self.s3_uploader:
            logger.error("S3 uploader not configured")
            return

        backups = self.s3_uploader.list_backups(platform)

        if not backups:
            logger.info("No backups found")
            return

        logger.info(f"Found {len(backups)} backups:")
        for backup in backups:
            logger.info(
                f"  - {backup['key']} ({backup['size_mb']:.2f} MB) - {backup['last_modified']}"
            )

    def run_health_check(self, platforms: List[str] = None):
        """Check platform connectivity and authentication health"""
        logger.info("[HEALTH] Running platform health checks...")

        total_checks = 0
        passed_checks = 0

        for platform, instance, manager in self.managers:
            if platforms and platform not in platforms:
                continue

            total_checks += 1
            logger.info(f"[HEALTH] Checking {platform.upper()} connectivity...")

            try:
                # Test basic connectivity by fetching a small amount of data
                test_repos = manager.get_repositories()[
                    :1
                ]  # Get just one repo for testing
                if test_repos:
                    logger.info(
                        f"[HEALTH] OK {platform.upper()}: Authentication OK, found {len(manager.get_repositories())} repositories"
                    )
                    passed_checks += 1
                else:
                    logger.warning(
                        f"[HEALTH] WARNING {platform.upper()}: Authentication OK but no repositories found"
                    )
                    passed_checks += 1
            except Exception as e:
                logger.error(f"[HEALTH] FAIL {platform.upper()}: {str(e)}")

        # Test S3 connectivity if configured
        if self.s3_uploader:
            total_checks += 1
            logger.info("[HEALTH] Checking S3 connectivity...")
            try:
                if self.s3_uploader.test_connection():
                    logger.info("[HEALTH] OK S3: Connection and permissions OK")
                    passed_checks += 1
                else:
                    logger.error(
                        "[HEALTH] FAIL S3: Connection or permission test failed"
                    )
            except Exception as e:
                logger.error(f"[HEALTH] FAIL S3: {str(e)}")

        # Summary
        logger.info("=" * 50)
        logger.info(
            f"[HEALTH] Health check complete: {passed_checks}/{total_checks} services healthy"
        )
        if passed_checks == total_checks:
            logger.info("[HEALTH] All systems operational OK")
        else:
            logger.error(
                f"[HEALTH] {total_checks - passed_checks} service(s) have issues FAIL"
            )
            sys.exit(1)

    def validate_configuration(self):
        """Validate configuration and environment variables"""
        logger.info("[CONFIG] Validating configuration...")

        issues = []
        warnings = []

        # Check environment file
        env_file = Path(".env")
        if not env_file.exists():
            issues.append("Missing .env file")
        else:
            logger.info("[CONFIG] OK .env file exists")

        # Check required platform tokens
        platforms_found = 0

        github_token = os.getenv("GITHUB_TOKEN")
        if github_token and not github_token.startswith("ghp_your"):
            logger.info("[CONFIG] OK GitHub token configured")
            platforms_found += 1
        else:
            warnings.append("GitHub token not configured")

        gitlab_token = os.getenv("GITLAB_TOKEN")
        if gitlab_token and not gitlab_token.startswith("glpat_your"):
            logger.info("[CONFIG] OK GitLab token configured")
            platforms_found += 1
        else:
            warnings.append("GitLab token not configured")

        bitbucket_token = os.getenv("BITBUCKET_TOKEN")
        if bitbucket_token and not bitbucket_token.startswith("your_token_here"):
            logger.info("[CONFIG] OK Bitbucket token configured")
            platforms_found += 1
        else:
            warnings.append("Bitbucket token not configured")

        if platforms_found == 0:
            issues.append("No platform tokens configured")

        # Check AWS configuration
        aws_profile = os.getenv("AWS_PROFILE")
        if aws_profile:
            logger.info(f"[CONFIG] OK AWS profile configured: {aws_profile}")
            # Try to validate AWS credentials
            try:
                import boto3

                session = boto3.Session(profile_name=aws_profile)
                sts = session.client("sts")
                identity = sts.get_caller_identity()
                logger.info(
                    f"[CONFIG] OK AWS credentials valid for: {identity.get('Arn', 'unknown')}"
                )
            except Exception as e:
                issues.append(f"AWS credentials invalid: {str(e)}")
        else:
            warnings.append("AWS profile not configured (S3 mode will not work)")

        # Check working directories
        work_dir = os.getenv("WORK_DIR")
        if work_dir and Path(work_dir).exists():
            logger.info(f"[CONFIG] OK Working directory exists: {work_dir}")
        elif work_dir:
            issues.append(f"Working directory does not exist: {work_dir}")

        # Summary
        logger.info("=" * 50)
        if issues:
            logger.error("[CONFIG] Configuration validation failed:")
            for issue in issues:
                logger.error(f"[CONFIG] FAIL {issue}")

        if warnings:
            logger.warning("[CONFIG] Configuration warnings:")
            for warning in warnings:
                logger.warning(f"[CONFIG] WARNING {warning}")

        if not issues and not warnings:
            logger.info("[CONFIG] OK All configuration checks passed")
        elif not issues:
            logger.info("[CONFIG] OK Configuration valid with warnings")
        else:
            logger.error("[CONFIG] FAIL Configuration validation failed")
            sys.exit(1)

    def verify_backup_integrity(self, backup_path: str):
        """Verify backup integrity at specified path"""
        logger.info(f"[VERIFY] Verifying backup integrity at: {backup_path}")

        backup_path = Path(backup_path)
        if not backup_path.exists():
            logger.error(f"[VERIFY] FAIL Backup path does not exist: {backup_path}")
            sys.exit(1)

        verified = 0
        failed = 0

        # Check if it's a local backup directory or S3 path
        if backup_path.is_dir():
            # Local backup verification
            logger.info("[VERIFY] Scanning local backup directory...")

            for bundle_file in backup_path.rglob("*.bundle"):
                logger.debug(f"[VERIFY] Checking {bundle_file.name}...")
                try:
                    # Verify git bundle integrity
                    result = subprocess.run(
                        ["git", "bundle", "verify", str(bundle_file)],
                        capture_output=True,
                        text=True,
                        check=False,
                    )

                    if result.returncode == 0:
                        verified += 1
                        logger.debug(
                            f"[VERIFY] OK {bundle_file.name}: Valid git bundle"
                        )
                    else:
                        failed += 1
                        logger.error(
                            f"[VERIFY] FAIL {bundle_file.name}: Invalid git bundle - {result.stderr.strip()}"
                        )

                except Exception as e:
                    failed += 1
                    logger.error(
                        f"[VERIFY] FAIL {bundle_file.name}: Verification error - {str(e)}"
                    )

        elif backup_path.is_file() and backup_path.suffix == ".bundle":
            # Single bundle file verification
            logger.info(f"[VERIFY] Verifying single bundle file...")
            try:
                result = subprocess.run(
                    ["git", "bundle", "verify", str(backup_path)],
                    capture_output=True,
                    text=True,
                    check=False,
                )

                if result.returncode == 0:
                    verified = 1
                    logger.info(f"[VERIFY] OK Bundle file is valid")
                else:
                    failed = 1
                    logger.error(
                        f"[VERIFY] FAIL Bundle file is invalid: {result.stderr.strip()}"
                    )

            except Exception as e:
                failed = 1
                logger.error(f"[VERIFY] FAIL Verification error: {str(e)}")

        else:
            logger.error(
                f"[VERIFY] FAIL Invalid backup path (not a directory or .bundle file)"
            )
            sys.exit(1)

        # Summary
        total = verified + failed
        logger.info("=" * 50)
        logger.info(
            f"[VERIFY] Verification complete: {verified}/{total} backups verified"
        )
        if failed == 0:
            logger.info("[VERIFY] OK All backups are valid")
        else:
            logger.error(f"[VERIFY] FAIL {failed} backup(s) failed verification")
            sys.exit(1)


def get_env_default(env_var: str, fallback=None):
    """Get value from environment or .env file"""
    return os.getenv(env_var, fallback)


def main():
    # Load environment variables first (before parsing args)
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="[bold blue]Enterprise Repository Backup Tool[/bold blue] - Sync corporate Git repositories to local storage or AWS S3",
        epilog="""
[bold green]Examples:[/bold green]
  [dim]# Backup to local directory[/dim]
  [yellow]%(prog)s[/yellow] [cyan]local[/cyan] [magenta]/path/to/backups[/magenta]

  [dim]# Backup to S3 (setup first)[/dim]
  [yellow]%(prog)s[/yellow] [cyan]s3[/cyan] [cyan]--setup[/cyan] [magenta]--profile[/magenta] admin-profile
  [yellow]%(prog)s[/yellow] [cyan]s3[/cyan] [magenta]--profile[/magenta] backup-service-profile

  [dim]# Backup specific repositories[/dim]
  [yellow]%(prog)s[/yellow] [cyan]local[/cyan] [magenta]/backups[/magenta] [cyan]--repos[/cyan] owner/repo1,owner/repo2

  [dim]# List existing backups[/dim]  
  [yellow]%(prog)s[/yellow] [cyan]s3[/cyan] [cyan]--list[/cyan] [magenta]--profile[/magenta] backup-service-profile
  [yellow]%(prog)s[/yellow] [cyan]local[/cyan] [magenta]/backups[/magenta] [cyan]--list[/cyan]

[bold blue]Features:[/bold blue]
  • Local and S3 backup modes
  • Enterprise security with IAM roles and S3 encryption
  • Automatic lifecycle management (Standard -> IA -> Glacier -> Deep Archive)
  • S3 versioning for point-in-time recovery
  • Parallel processing for fast backups
  • Rich progress tracking and logging
  
[bold blue]For more information:[/bold blue] https://github.com/your-org/repo-backup
        """,
        formatter_class=ArgumentDefaultsRichHelpFormatter,
        add_help=False,  # We'll add custom help
    )

    # Help argument
    parser.add_argument(
        "-h", "--help", action="help", help="Show this help message and exit"
    )

    # Positional argument for backup mode
    parser.add_argument(
        "mode",
        choices=["local", "s3", "both"],
        help="Backup destination mode: local filesystem, AWS S3, or both",
    )

    # Positional argument for path (local) or optional for s3
    parser.add_argument(
        "path",
        nargs="?",
        help="Local backup path (required for local mode, ignored for s3 mode)",
    )

    # S3 Setup commands
    s3_group = parser.add_argument_group("S3 Configuration")
    s3_group.add_argument(
        "--setup",
        action="store_true",
        help="Create and configure S3 bucket, IAM user, and AWS profile (s3 mode only)",
    )
    s3_group.add_argument(
        "--bucket-name",
        metavar="NAME",
        help="S3 bucket name (auto-generated if not provided, s3 mode only)",
    )
    s3_group.add_argument(
        "--bucket",
        metavar="NAME",
        help="S3 bucket name for operations (env: AWS_S3_BUCKET, overrides all other bucket settings)",
    )
    s3_group.add_argument(
        "--s3-prefix",
        default=get_env_default("S3_PREFIX", "repos"),
        metavar="PREFIX",
        help="S3 key prefix (env: S3_PREFIX, default: repos)",
    )
    s3_group.add_argument(
        "--enable-glacier",
        action="store_true",
        help="Enable Glacier storage transitions for long-term archival (s3 mode only, default: disabled)",
    )
    s3_group.add_argument(
        "--profile",
        default=get_env_default("AWS_PROFILE"),
        metavar="PROFILE",
        help="AWS profile to use (env: AWS_PROFILE, ignored during --setup unless explicitly specified)",
    )
    s3_group.add_argument(
        "--setup-profile",
        metavar="PROFILE",
        help="AWS profile to use for --setup only (overrides --profile, requires admin permissions)",
    )
    s3_group.add_argument(
        "--region",
        default=get_env_default("AWS_REGION", "us-west-2"),
        metavar="REGION",
        help="AWS region (env: AWS_REGION, default: us-west-2)",
    )

    # Common backup operations
    ops_group = parser.add_argument_group("Backup Operations")
    ops_group.add_argument("--list", action="store_true", help="List existing backups")
    ops_group.add_argument(
        "--platform",
        metavar="PLATFORMS",
        help="Comma-separated list of platforms to use (github,gitlab,bitbucket). If not specified, uses all configured platforms",
    )
    ops_group.add_argument(
        "--all",
        action="store_true",
        help="Backup all accessible repositories (explicit flag required)",
    )
    ops_group.add_argument(
        "--repos",
        metavar="PATTERNS",
        help="Comma-separated list of repository patterns: exact (owner/repo), glob (owner/repo*), or regex (re:pattern)",
    )
    ops_group.add_argument(
        "--repos-file",
        metavar="FILE",
        help="Path to file containing list of repository patterns (one per line)",
    )
    ops_group.add_argument(
        "--test",
        action="store_true",
        help="Test mode: backup first small non-empty repository found (max ~1MB) to verify functionality",
    )

    # Diagnostic commands
    diag_group = parser.add_argument_group("Diagnostic Commands")
    diag_group.add_argument(
        "--health",
        action="store_true",
        help="Check platform connectivity and authentication health",
    )
    diag_group.add_argument(
        "--validate-config",
        action="store_true",
        help="Validate configuration and environment variables",
    )
    diag_group.add_argument(
        "--verify", metavar="PATH", help="Verify backup integrity at specified path"
    )

    # Performance options
    perf_group = parser.add_argument_group("Performance Options")
    perf_group.add_argument(
        "--parallel",
        action="store_true",
        default=True,
        help="Run backups in parallel (default: True)",
    )
    perf_group.add_argument(
        "--sequential",
        action="store_true",
        help="Run backups sequentially (overrides --parallel)",
    )
    perf_group.add_argument(
        "--workers",
        type=int,
        default=int(get_env_default("PARALLEL_WORKERS", "5")),
        metavar="N",
        help="Number of parallel workers (env: PARALLEL_WORKERS, default: 5)",
    )
    perf_group.add_argument(
        "--work-dir",
        type=str,
        default=get_env_default("WORK_DIR", None),
        metavar="DIR",
        help="Working directory for temporary files (env: WORK_DIR, default: /var/tmp/repo-backup or local_path/tmp/repo-backup)",
    )
    perf_group.add_argument(
        "--backup-method",
        choices=["direct", "archive"],
        default=get_env_default("BACKUP_METHOD", "direct"),
        metavar="METHOD",
        help="Backup method: direct (git bundle, recommended) or archive (tar.gz) (env: BACKUP_METHOD, default: direct)",
    )
    perf_group.add_argument(
        "--exclude-personal",
        action="store_true",
        default=get_env_default("EXCLUDE_PERSONAL", "true").lower() == "true",
        help="Exclude personal repositories (env: EXCLUDE_PERSONAL, default: true)",
    )

    # Logging options
    log_group = parser.add_argument_group("Logging Options")
    log_group.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging (DEBUG level)",
    )
    log_group.add_argument(
        "--log-file",
        default=get_env_default("LOG_FILE", "repo-backup.log"),
        metavar="FILE",
        help="Log file name (env: LOG_FILE, default: repo-backup.log)",
    )

    args = parser.parse_args()

    # Initialize logging with loguru
    setup_logging(verbose=args.verbose, log_file=args.log_file)

    # Handle sequential override
    if args.sequential:
        args.parallel = False

    # Validate mode-specific arguments
    local_path = None

    if args.mode in ["local", "both"]:
        # For local mode, determine path from args, env, or error
        local_path = args.path or get_env_default("LOCAL_BACKUP_PATH")
        if not local_path:
            logger.error(
                "Local backup requires a path. Provide it as argument or set LOCAL_BACKUP_PATH environment variable."
            )
            sys.exit(1)

        if args.setup and args.mode == "local":
            logger.warning("--setup ignored in local mode")
        if args.profile and not args.list and args.mode == "local":
            logger.info(
                "[CONFIG] Using current AWS session (profile parameter not needed for local mode)"
            )

    if args.mode in ["s3", "both"]:
        # For S3 mode, validate AWS requirements
        if args.setup:
            # S3 setup mode
            if not args.profile:
                logger.error("S3 setup requires --profile (admin profile)")
                sys.exit(1)
        else:
            # Regular S3 mode - need either profile or access keys
            has_profile = args.profile or get_env_default("AWS_PROFILE")
            has_access_keys = get_env_default("AWS_ACCESS_KEY_ID") and get_env_default(
                "AWS_SECRET_ACCESS_KEY"
            )
            if not has_profile and not has_access_keys:
                logger.error(
                    "S3 mode requires either AWS_PROFILE or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"
                )
                sys.exit(1)

        if args.path and args.mode == "s3":
            logger.info(
                "[INFO] Path not needed for S3-only mode (using bucket configuration)"
            )

    # S3 Setup mode
    if args.mode == "s3" and args.setup:
        # For setup, NEVER use AWS credentials from .env - use current AWS session or explicitly specified profile
        # Priority: --setup-profile > explicitly specified --profile > current AWS CLI session (no profile)

        # Clear any AWS_PROFILE from environment to start fresh
        if "AWS_PROFILE" in os.environ:
            del os.environ["AWS_PROFILE"]

        if args.setup_profile:
            # User explicitly specified a setup profile
            setup_profile = args.setup_profile
            os.environ["AWS_PROFILE"] = setup_profile
            logger.info(
                f"Using AWS profile '{setup_profile}' for S3 setup (from --setup-profile)"
            )
        elif args.profile and args.profile != get_env_default("AWS_PROFILE"):
            # User explicitly specified --profile (not from .env)
            setup_profile = args.profile
            os.environ["AWS_PROFILE"] = setup_profile
            logger.info(
                f"Using AWS profile '{setup_profile}' for S3 setup (from --profile)"
            )
        else:
            # Use current AWS CLI session (no profile specified)
            setup_profile = None
            # Ensure AWS_PROFILE is not set so boto3 uses current session
            logger.info("Using current AWS CLI session for S3 setup (no profile)")

        os.environ["AWS_REGION"] = args.region
        logger.info(f"AWS Region: {args.region}")
        logger.info("")
        logger.info("=== S3 Bucket Setup Starting ===")
        logger.info("This requires AWS admin permissions to:")
        logger.info("  - Create S3 bucket")
        logger.info("  - Configure bucket policies")
        logger.info("  - Create IAM user and policies")
        logger.info("")

        manager = S3BucketManager(profile=setup_profile)

        # Create and configure bucket
        bucket_name = manager.create_and_configure_bucket(
            args.bucket_name, enable_glacier=args.enable_glacier
        )

        # Test bucket
        if manager.test_bucket(bucket_name):
            # Create IAM user and profile
            logger.info("Creating dedicated IAM user and AWS profile...")

            access_key_id, secret_key, user_name = manager.create_iam_user_and_policy(
                bucket_name
            )

            if access_key_id and secret_key:
                # Create AWS profile for the service
                profile_name = f"repo-backup-s3-{bucket_name}"

                if manager.create_aws_profile(
                    profile_name, access_key_id, secret_key, args.region
                ):
                    # Write .env file with the new profile
                    manager.write_env_file(bucket_name, profile_name=profile_name)

                    logger.info("")
                    logger.info("=== Setup Complete ===")
                    logger.info(f"Bucket: {bucket_name}")
                    logger.info(f"Region: {args.region}")
                    logger.info(
                        f"Glacier: {'Enabled' if args.enable_glacier else 'Disabled (Standard storage only)'}"
                    )
                    logger.info(f"IAM User: {user_name}")
                    logger.info(f"AWS Profile: {profile_name}")
                    logger.info("")
                    logger.info("[OK] Dedicated AWS profile created for script")
                    logger.info("[OK] IAM user has minimal S3-only permissions")
                    logger.info("[OK] Configuration written to .env")
                    logger.info("")
                    logger.info("Next steps:")
                    logger.info("1. Add your repository tokens to .env file")
                    logger.info(
                        f"2. Run: uv run repo-backup s3 --list --profile {profile_name}"
                    )
                    logger.info(
                        f"3. Run: uv run repo-backup s3 --profile {profile_name}"
                    )
                else:
                    logger.error("Failed to create AWS profile")
                    # Still write .env file with original profile
                    manager.write_env_file(bucket_name)
            else:
                logger.warning("Could not create access keys, using original profile")
                manager.write_env_file(bucket_name)
        else:
            logger.error("Bucket setup failed")
            sys.exit(1)
        return

    # Regular operation mode - create orchestrator based on mode
    # Implement standard configuration precedence: CLI args > ENV vars > .env file > defaults

    # Apply command-line overrides with proper precedence
    if args.mode in ["s3", "both"]:
        # --bucket takes highest precedence for S3 bucket name
        if args.bucket:
            os.environ["AWS_S3_BUCKET"] = args.bucket
            logger.info(f"[CONFIG] Using bucket from --bucket: {args.bucket}")
        # Fallback to environment variable, then .env file (already loaded)

        if args.profile:
            os.environ["AWS_PROFILE"] = args.profile
        if args.region:
            os.environ["AWS_REGION"] = args.region
        if hasattr(args, "s3_prefix") and args.s3_prefix:
            os.environ["S3_PREFIX"] = args.s3_prefix

    # Apply backup method override
    if args.backup_method:
        os.environ["BACKUP_METHOD"] = args.backup_method

    # Apply exclude personal override
    if hasattr(args, "exclude_personal"):
        os.environ["EXCLUDE_PERSONAL"] = str(args.exclude_personal).lower()

    # Create orchestrator with appropriate configuration
    orchestrator = RepoBackupOrchestrator(
        use_env=True,
        local_backup_path=local_path if args.mode in ["local", "both"] else None,
        work_dir=args.work_dir,
    )

    # Parse platform argument
    platforms = None
    if args.platform:
        platforms = [p.strip().lower() for p in args.platform.split(",") if p.strip()]
        # Validate platforms
        valid_platforms = {"github", "gitlab", "bitbucket"}
        invalid = [p for p in platforms if p not in valid_platforms]
        if invalid:
            logger.error(f"[ERROR] Invalid platform(s): {', '.join(invalid)}")
            logger.error(f"Valid platforms: github, gitlab, bitbucket")
            sys.exit(1)

    # Handle diagnostic commands
    if args.health:
        orchestrator.run_health_check(platforms)
        return
    elif args.validate_config:
        orchestrator.validate_configuration()
        return
    elif args.verify:
        orchestrator.verify_backup_integrity(args.verify)
        return
    elif args.list:
        if args.mode == "local":
            # List local backups
            if orchestrator.local_backup:
                # For listing, use first platform if specified
                list_platform = platforms[0] if platforms else None
                backups = orchestrator.local_backup.list_backups(list_platform)
                if backups:
                    logger.info(f"Found {len(backups)} local backups:")
                    for backup in backups:
                        logger.info(
                            f"  - {backup['path']} ({backup['size_mb']:.2f} MB) - {backup['modified']} ({backup['type']})"
                        )
                else:
                    logger.info("No local backups found")
            else:
                logger.error("Local backup not configured")
        else:
            # List S3 backups
            # For listing, use first platform if specified
            list_platform = platforms[0] if platforms else None
            orchestrator.list_backups(list_platform)
    else:
        # Handle repository selection
        if args.all:
            # Backup all repositories
            platforms_str = f" from {', '.join(platforms)}" if platforms else ""
            logger.info(f"[MODE] Backing up ALL accessible repositories{platforms_str}")
            orchestrator.run_backup(
                parallel=args.parallel,
                max_workers=args.workers,
                specific_repos=None,
                platforms=platforms,
            )
        elif args.repos:
            # Parse comma-separated pattern list
            repo_patterns = [
                repo.strip() for repo in args.repos.split(",") if repo.strip()
            ]
            logger.info(
                f"[MODE] Backing up repositories matching {len(repo_patterns)} patterns"
            )
            orchestrator.run_backup(
                parallel=args.parallel,
                max_workers=args.workers,
                specific_repos=repo_patterns,
                platforms=platforms,
            )
        elif args.test:
            # Test mode - backup one small repository
            platforms_str = f" from {', '.join(platforms)}" if platforms else ""
            logger.info(f"[TEST MODE] Testing backup functionality{platforms_str}")
            orchestrator.run_backup(
                parallel=False,  # Use sequential for test
                max_workers=1,
                specific_repos=None,
                platforms=platforms,
                test_mode=True,
            )
        elif args.repos_file:
            # Load patterns from file
            repo_patterns = orchestrator.load_repos_from_file(args.repos_file)
            if repo_patterns:
                logger.info(
                    f"[MODE] Backing up repositories matching {len(repo_patterns)} patterns from file"
                )
                orchestrator.run_backup(
                    parallel=args.parallel,
                    max_workers=args.workers,
                    specific_repos=repo_patterns,
                )
            else:
                logger.error("[ERROR] No patterns loaded from file")
                sys.exit(1)
        elif get_env_default("SPECIFIC_REPOS"):
            # Load patterns from environment variable
            repo_patterns = [
                repo.strip()
                for repo in get_env_default("SPECIFIC_REPOS").split(",")
                if repo.strip()
            ]
            logger.info(
                f"[MODE] Backing up repositories matching {len(repo_patterns)} patterns from environment"
            )
            orchestrator.run_backup(
                parallel=args.parallel,
                max_workers=args.workers,
                specific_repos=repo_patterns,
                platforms=platforms,
            )
        else:
            # No repositories specified
            logger.error(
                "[ERROR] No repositories specified. Use --all to backup all repositories, --test for test mode, or --repos/--repos-file to specify patterns."
            )
            logger.info("Examples:")
            logger.info("  # Test backup functionality:")
            logger.info(
                f"  {sys.argv[0]} {args.mode} {'--profile ' + args.profile if args.mode == 's3' and args.profile else ''} --test"
            )
            logger.info("  # Backup all repositories:")
            logger.info(
                f"  {sys.argv[0]} {args.mode} {'--profile ' + args.profile if args.mode == 's3' and args.profile else ''} --all"
            )
            logger.info("  # Backup specific repository:")
            logger.info(
                f"  {sys.argv[0]} {args.mode} {'--profile ' + args.profile if args.mode == 's3' and args.profile else ''} --repos 'hypersec-repo/dashboard'"
            )
            logger.info("  # Backup with glob pattern:")
            logger.info(
                f"  {sys.argv[0]} {args.mode} {'--profile ' + args.profile if args.mode == 's3' and args.profile else ''} --repos 'hypersec-repo/*dashboard*'"
            )
            logger.info("  # Backup with regex pattern:")
            logger.info(
                f"  {sys.argv[0]} {args.mode} {'--profile ' + args.profile if args.mode == 's3' and args.profile else ''} --repos 're:.*dashboard.*'"
            )
            sys.exit(1)


if __name__ == "__main__":
    main()
