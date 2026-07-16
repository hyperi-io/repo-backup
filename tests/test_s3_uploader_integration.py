"""
Integration tests for S3Uploader against a real S3-compatible store (MinIO).

These exercise the whole backup pipeline for real - clone a local git repo,
bundle it, upload to a live MinIO container over an S3-compatible endpoint,
then download and restore - with no mocks anywhere. They are the regression
guard for two things:

  1. `endpoint_url` support (targeting Cloudflare R2 / MinIO / Ceph). Without
     it the tool could not write to R2 at all.
  2. `_archive_upload` used an undefined `s3_key_base`, so the `--archive`
     path raised NameError on every call and was swallowed into a generic
     "Archive upload failed". The archive test below fails if that regresses.

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

import base64
import subprocess

import boto3
import pytest

from src.base import Repository
from src.s3_uploader import S3Uploader

try:
    from testcontainers.minio import MinioContainer

    _HAS_TESTCONTAINERS = True
except ImportError:  # pragma: no cover
    _HAS_TESTCONTAINERS = False

pytestmark = pytest.mark.skipif(
    not _HAS_TESTCONTAINERS, reason="testcontainers[minio] not installed"
)

# The uploader hardcodes `ServerSideEncryption: AES256` (SSE-S3). MinIO only
# honours that header when it has a KMS backend, so we hand the container a
# single built-in key. A fixed 32-byte key keeps the test deterministic; its
# value is irrelevant for a throwaway store.
_KMS_KEY = "test-key:" + base64.b64encode(b"\x00" * 32).decode()


def _make_git_repo(path, content="hello\n"):
    """Build a real local git repo with one commit and return its path.

    `git clone --mirror` accepts a local filesystem path as a clone URL, so
    this stands in for a remote with no server to run.
    """
    path.mkdir(parents=True, exist_ok=True)
    p = str(path)
    subprocess.run(["git", "init", "-b", "main", p], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", p, "config", "user.email", "ci@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", p, "config", "user.name", "CI"], check=True, capture_output=True
    )
    (path / "README.md").write_text(content)
    subprocess.run(
        ["git", "-C", p, "add", "README.md"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", p, "-c", "commit.gpgsign=false", "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    return p


@pytest.fixture(scope="module")
def minio():
    """A live MinIO container with a KMS key so SSE-S3 uploads are accepted."""
    try:
        import docker

        docker.from_env().ping()
    except Exception as e:  # pragma: no cover - environment guard
        pytest.skip(f"Docker not available: {e}")

    container = MinioContainer(access_key="minioadmin", secret_key="minioadmin")
    container.with_env("MINIO_KMS_SECRET_KEY", _KMS_KEY)
    container.start()
    try:
        yield container
    finally:
        container.stop()


def _endpoint(minio):
    return f"http://{minio.get_config()['endpoint']}"


def _boto_client(minio):
    cfg = minio.get_config()
    return boto3.client(
        "s3",
        endpoint_url=_endpoint(minio),
        region_name="us-east-1",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
    )


def _uploader(minio, bucket, work_dir):
    """Create the bucket (S3Uploader.__init__ head_bucket's it) and return an
    uploader wired to the MinIO endpoint plus a bare boto3 client for asserts."""
    cfg = minio.get_config()
    client = _boto_client(minio)
    try:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.BucketAlreadyOwnedByYou:  # pragma: no cover
        pass

    uploader = S3Uploader(
        bucket_name=bucket,
        region="us-east-1",
        aws_access_key_id=cfg["access_key"],
        aws_secret_access_key=cfg["secret_key"],
        endpoint_url=_endpoint(minio),
        work_dir=str(work_dir),
        prefix="repos",
    )
    return uploader, client


def _list_keys(client, bucket):
    resp = client.list_objects_v2(Bucket=bucket)
    return [obj["Key"] for obj in resp.get("Contents", [])]


def test_endpoint_url_reaches_boto3(minio, tmp_path):
    """The endpoint_url is stored AND actually reaches the boto3 client -
    otherwise the tool silently talks to real AWS instead of R2/MinIO."""
    uploader, _ = _uploader(minio, "backup-endpoint", tmp_path / "work")
    expected = _endpoint(minio)

    assert uploader.endpoint_url == expected
    # boto3 records the resolved endpoint on the client - this is what proves
    # the kwarg was honoured, not just stored on the instance.
    assert uploader.s3_client.meta.endpoint_url == expected


def test_direct_upload_writes_and_restores_bundle(minio, tmp_path):
    """Full round trip: clone -> bundle -> upload -> download -> git clone."""
    uploader, client = _uploader(minio, "backup-direct", tmp_path / "work")
    repo_dir = _make_git_repo(tmp_path / "src-repo")

    repo = Repository(
        name="demo",
        clone_url=repo_dir,
        owner="acme",
        is_private=True,
        is_fork=False,
        is_owned_by_user=False,
        platform="gitlab",
        default_branch="main",
    )

    assert uploader.upload_repository(repo, method="direct") is True

    keys = _list_keys(client, "backup-direct")
    bundle_keys = [
        k
        for k in keys
        if k.startswith("repos/gitlab/acme/demo_") and k.endswith(".bundle")
    ]
    assert bundle_keys, keys

    # The object must be a real, restorable git bundle, not just bytes that
    # happened to land. Download it and clone it back.
    dl = tmp_path / "restored.bundle"
    client.download_file("backup-direct", bundle_keys[0], str(dl))

    verify = subprocess.run(
        ["git", "bundle", "verify", str(dl)], capture_output=True, text=True
    )
    assert verify.returncode == 0, verify.stderr

    clone_dir = tmp_path / "restored"
    clone = subprocess.run(
        ["git", "clone", str(dl), str(clone_dir)], capture_output=True, text=True
    )
    assert clone.returncode == 0, clone.stderr
    assert (clone_dir / "README.md").read_text() == "hello\n"


def test_direct_upload_is_idempotent(minio, tmp_path):
    """Re-running keys off the last commit date, so no duplicate object."""
    uploader, client = _uploader(minio, "backup-idem", tmp_path / "work")
    repo_dir = _make_git_repo(tmp_path / "src-repo")

    repo = Repository(
        name="demo",
        clone_url=repo_dir,
        owner="acme",
        is_private=True,
        is_fork=False,
        is_owned_by_user=False,
        platform="gitlab",
        default_branch="main",
    )

    assert uploader.upload_repository(repo, method="direct") is True
    assert uploader.upload_repository(repo, method="direct") is True

    bundle_keys = [
        k for k in _list_keys(client, "backup-idem") if k.endswith(".bundle")
    ]
    assert len(bundle_keys) == 1, bundle_keys


def test_archive_upload_writes_targz_without_nameerror(minio, tmp_path):
    """Regression: `_archive_upload` used an undefined `s3_key_base` and raised
    NameError on every call. It must now produce a .tar.gz object."""
    uploader, client = _uploader(minio, "backup-archive", tmp_path / "work")
    repo_dir = _make_git_repo(tmp_path / "src-repo")

    repo = Repository(
        name="demo2",
        clone_url=repo_dir,
        owner="acme",
        is_private=True,
        is_fork=False,
        is_owned_by_user=False,
        platform="gitlab",
        default_branch="main",
    )

    assert uploader.upload_repository(repo, method="archive") is True

    keys = _list_keys(client, "backup-archive")
    targz_keys = [
        k
        for k in keys
        if k.startswith("repos/gitlab/acme/demo2_") and k.endswith(".tar.gz")
    ]
    assert targz_keys, keys
