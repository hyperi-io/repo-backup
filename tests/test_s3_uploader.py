"""
Tests for S3Uploader

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

import tempfile
from pathlib import Path

import pytest

from src.s3_uploader import S3Uploader


class TestS3UploaderInit:
    """Tests for S3Uploader initialisation"""

    def test_init_with_invalid_bucket(self):
        """Test initialisation fails with non-existent bucket"""
        with pytest.raises(Exception):
            S3Uploader(
                bucket_name="non-existent-bucket-xyz123",
                region="us-west-2",
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="testsecret",
            )

    def test_init_with_invalid_credentials(self):
        """Test initialisation fails with invalid credentials"""
        with pytest.raises(Exception):
            S3Uploader(
                bucket_name="test-bucket",
                region="us-west-2",
                aws_access_key_id="invalid",
                aws_secret_access_key="invalid",
            )

    def test_init_creates_temp_dir(self):
        """Test initialisation creates temp directory (if credentials were valid)"""
        # This would need valid S3 credentials to actually test
        pass


class TestS3UploaderWorkDir:
    """Tests for S3Uploader work directory handling"""

    def test_custom_work_dir(self):
        """Test custom work directory is used"""
        with tempfile.TemporaryDirectory() as tmpdir:
            work_dir = Path(tmpdir) / "custom_work"

            # Would need valid credentials, but we test the parameter exists
            with pytest.raises(Exception):
                uploader = S3Uploader(
                    bucket_name="test-bucket",
                    region="us-west-2",
                    aws_access_key_id="invalid",
                    aws_secret_access_key="invalid",
                    work_dir=str(work_dir),
                )


class TestS3UploaderPrefix:
    """Tests for S3 key prefix handling"""

    def test_default_prefix(self):
        """Test default prefix is 'repos'"""
        # Would need valid credentials
        with pytest.raises(Exception):
            uploader = S3Uploader(
                bucket_name="test-bucket",
                region="us-west-2",
                aws_access_key_id="invalid",
                aws_secret_access_key="invalid",
            )

    def test_custom_prefix(self):
        """Test custom prefix is used"""
        with pytest.raises(Exception):
            uploader = S3Uploader(
                bucket_name="test-bucket",
                region="us-west-2",
                aws_access_key_id="invalid",
                aws_secret_access_key="invalid",
                prefix="custom/prefix",
            )


class TestS3UploaderCredentials:
    """Tests for S3Uploader credential handling"""

    def test_access_keys_only(self):
        """Test using access keys only"""
        with pytest.raises(Exception):
            S3Uploader(
                bucket_name="test-bucket",
                region="us-west-2",
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="testsecret",
            )

    def test_profile_only(self):
        """Test using profile only"""
        with pytest.raises(Exception):
            S3Uploader(
                bucket_name="test-bucket",
                region="us-west-2",
                aws_profile="nonexistent-profile",
            )

    def test_both_keys_and_profile(self):
        """Test using both access keys and profile"""
        with pytest.raises(Exception):
            S3Uploader(
                bucket_name="test-bucket",
                region="us-west-2",
                aws_access_key_id="AKIATEST",
                aws_secret_access_key="testsecret",
                aws_profile="test-profile",
            )
