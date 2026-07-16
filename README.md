# Repository Backup Tool

A straightforward enterprise tool for backing up Git repositories from GitHub, GitLab, and Bitbucket

[![semantic-release: conventional](https://img.shields.io/badge/semantic--release-conventional-e10079?logo=semantic-release)](https://github.com/semantic-release/semantic-release)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

We built this tool because we needed a reliable way to backup all our repositories across different platforms. It handles the heavy lifting of discovering repos, cloning them efficiently, and storing them either locally or in S3. Nothing fancy, just solid backup automation that works.

## What It Does

- Backs up repositories from GitHub, GitLab, and Bitbucket
- Works with corporate/organization repositories by default
- Uploads directly to S3 or saves locally
- Creates git bundles (preserves complete history) or tar archives
- Processes multiple repos in parallel for speed
- Filters repos by name patterns if needed
- Handles multiple accounts per platform
- Shows progress bars so you know what's happening

## What You'll Need

- Python 3.11 or newer
- Git installed on your system
- Access tokens for the platforms you want to backup (or use auto-discovery from CLI tools)
- AWS account with S3 (optional, for cloud backups)

## Getting Started

### Installation

Install with uv (recommended - isolated environment):

```bash
# Install uv first if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install repo-backup system-wide (isolated venv)
uv tool install git+https://github.com/hyperi-io/repo-backup

# Or from PyPI (when published)
# uv tool install repo-backup

# Check it worked
repo-backup --help
```

This installs `repo-backup` in its own isolated virtual environment with the executable available on your PATH.

Or run without installing:

```bash
# One-off execution
uvx --from git+https://github.com/hyperi-io/repo-backup repo-backup --help
```

### Developer Setup

If you're planning to contribute or modify the code:

```bash
# Get the code
git clone https://github.com/hyperi-io/repo-backup.git
cd repo-backup

# Install with dev and test dependencies
uv sync --extra dev --extra test

# Run the local CI checks
./scripts/ci

# Try it out
uv run repo-backup local /tmp/test-backup --test
```

### Upgrading

```bash
# Upgrade to latest version
uv tool upgrade repo-backup

# Or upgrade all tools
uv tool upgrade --all
```

### Uninstalling

```bash
uv tool uninstall repo-backup
```

## Setting Things Up

### Step 1: S3 Setup (If You Want Cloud Backups)

**Note:** Check out [AWS.md](AWS.md) for the full AWS configuration guide if you need it.

The tool can set up your S3 bucket automatically. You'll need AWS permissions to create S3 buckets and IAM users - not full admin access, just those specific permissions.

Quick setup:

```bash
# Basic setup using your current AWS profile
repo-backup s3 --setup

# Use a specific AWS profile for setup
repo-backup s3 --setup --profile admin-profile

# Enable Glacier for cheaper long-term storage
repo-backup s3 --setup --enable-glacier

# Use your own bucket name
repo-backup s3 --setup --bucket-name my-backups --region us-east-1
```

This creates everything you need:
- S3 bucket with a unique name (or your chosen name)
- Versioning enabled for backup history
- Encryption turned on
- Public access blocked
- Lifecycle policies configured
- Dedicated IAM user with minimal permissions
- AWS CLI profile set up
- `.env` file with all the settings
- Quick test to make sure it works

After setup, you'll see the bucket name and profile info. The `.env` file will have your S3 config ready to go.

### Step 2: Add Your Git Platform Tokens

First, copy the example config (skip this if S3 setup already created one):

```bash
cp .env.example .env
```

Then add your tokens to `.env`:

```bash
# Platform tokens - get these from your platforms (see below)
GITHUB_TOKEN=ghp_your_token_here
GITLAB_TOKEN=glpat_your_token_here
BITBUCKET_TOKEN=ATCTT_your_token_here
BITBUCKET_WORKSPACE=your-workspace

# Where to save backups
LOCAL_BACKUP_PATH=/mnt/backups/repo-backup
AWS_S3_BUCKET=repo-backup-123456789  # Set by S3 setup

# AWS settings (filled by S3 setup)
AWS_PROFILE=repo-backup-profile
AWS_REGION=us-west-2

# Optional tweaks
PARALLEL_WORKERS=5
BACKUP_METHOD=direct
```

### Auto-Discovery of Tokens

The tool can automatically discover tokens from standard CLI tool configurations if they're not set in `.env`:

- **GitHub**: Reads from `gh` CLI (`gh auth token`) or `GH_TOKEN`/`GITHUB_TOKEN` env vars
- **GitLab**: Reads from `glab` CLI config (`~/.config/glab-cli/config.yml`) or `GITLAB_TOKEN` env var
- **Bitbucket**: Reads from `~/.netrc` file or `BITBUCKET_TOKEN` env var
- **AWS**: Reads from `~/.aws/credentials` file or standard AWS env vars

This means if you're already authenticated with `gh`, `glab`, or have AWS credentials configured, the tool will use them automatically.

### Getting Your Access Tokens

*Quick note: These instructions are current as of late 2025. Platform UIs change, so check their docs if something looks different.*

#### GitHub Tokens

**Classic Token (Still Works Great):**
1. Go to Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token (classic)
3. Set expiration (90 days is reasonable)
4. Check these scopes:
   - `repo` - Access to private repositories
   - `read:org` - See organization repos
5. Generate and copy the token (starts with `ghp_`)
6. Save it somewhere safe - you won't see it again

**Fine-grained Token (GitHub's New Way):**
1. Go to Settings → Developer settings → Personal access tokens → Fine-grained tokens
2. Generate new token
3. Name it something like "repo-backup"
4. Pick your repositories or "All repositories"
5. Set permissions:
   - Contents: Read
   - Metadata: Read (automatic)
   - Actions: Read (if you backup workflows)
6. Generate and copy (starts with `github_pat_`)

#### GitLab Tokens

Pretty straightforward:
1. Go to User Settings → Access Tokens
2. Name it "repo-backup"
3. Set an expiration date
4. Check these scopes:
   - `read_repository`
   - `read_api`
5. Create and copy the token (starts with `glpat-`)

#### Bitbucket Tokens

**Heads up:** Bitbucket works differently than the others. Each token only works for one workspace, so you'll need separate tokens for different workspaces.

**Workspace Tokens (Current Method):**
1. Go to your workspace settings
2. Find Access tokens under Security
3. Create token with Repositories: Read permission
4. Copy the token (starts with `ATCTT`)
5. Remember to set both token and workspace in `.env`:
   ```bash
   BITBUCKET_TOKEN=ATCTT_your_token
   BITBUCKET_WORKSPACE=your-workspace
   ```

**App Passwords (Old Method, Still Works):**
1. Go to Personal settings → App passwords
2. Create app password named "repo-backup"
3. Give it these permissions:
   - Account: Read
   - Workspace membership: Read
   - Repositories: Read
4. Use with your username in `.env`

## Using the Tool

### Basic Commands

```bash
# Backup to local directory
repo-backup local /path/to/backups

# Backup to S3
repo-backup s3

# Do both
repo-backup both /path/to/local/backup
```

### Filtering What to Backup

```bash
# Just GitHub repos
repo-backup local /backup/dir --platform github

# Multiple platforms
repo-backup local /backup/dir --platform gitlab,bitbucket

# Specific repositories
repo-backup local /backup/dir --repos owner/repo1,owner/repo2

# Pattern matching
repo-backup local /backup/dir --pattern "frontend-*"
repo-backup local /backup/dir --pattern-type regex --pattern ".*-service$"

# Include forks (normally skipped)
repo-backup local /backup/dir --include-forks
```

### Checking Your Backups

```bash
# See what's backed up locally
repo-backup local /backup/dir --list

# Check S3 backups
repo-backup s3 --list

# Filter by platform
repo-backup local /backup/dir --list --platform github
```

### Advanced Stuff

```bash
# Speed things up with more workers
repo-backup local /backup/dir --workers 10

# Or slow down for limited bandwidth
repo-backup local /backup/dir --workers 2

# Test mode - just backs up smallest repo
repo-backup local /backup/dir --test

# Use tar archives instead of git bundles
repo-backup local /backup/dir --archive

# Force re-backup everything
repo-backup local /backup/dir --force

# See what's happening
repo-backup local /backup/dir --verbose

# Check if everything's configured right
repo-backup --health-check
repo-backup --validate-config
```

## How It Actually Works

1. **Discovery**: Connects to each platform and finds all your repos
2. **Filtering**: Skips personal repos, forks, and applies your patterns
3. **Backup**: For each repository:
   - Clones with full history using `git clone --mirror`
   - Creates a git bundle or tar.gz file
   - Uploads to S3 or saves locally
   - Cleans up temp files
4. **Report**: Shows you what worked and what didn't

## Backup File Formats

### Git Bundles (Default)

These are like portable git repositories:
- Path: `repos/{platform}/{owner}/{repo_name}_{timestamp}.bundle`
- Contains complete history and all branches
- Restore with: `git clone repo.bundle restored-repo`

### Git LFS Support

For repositories using Git LFS, the tool creates an additional archive:

- Bundle: `repos/{platform}/{owner}/{repo_name}_{timestamp}.bundle` (git history)
- LFS: `repos/{platform}/{owner}/{repo_name}_{timestamp}_lfs.tar.gz` (large files)

Both files are needed for a complete restore of LFS repositories.

### Tar Archives

Traditional compressed archives (use `--backup-method archive`):
- Path: `repos/{platform}/{owner}/{repo_name}_{timestamp}.tar.gz`
- Contains the bare git repository including LFS objects
- Restore with: `tar -xzf repo.tar.gz`

## Restoring Your Backups

### From a Git Bundle

The easy way:

```bash
# Get the bundle from S3
aws s3 cp s3://your-bucket/repos/github/org/repo.bundle repo.bundle

# Clone it
git clone repo.bundle restored-repo
cd restored-repo

# Point it back to GitHub (optional)
git remote set-url origin https://github.com/org/repo.git

# Check everything's there
git log --oneline -5
git branch -a
git tag -l
```

### From a Git Bundle with LFS

For repositories that use Git LFS:

```bash
# Get both files from S3
aws s3 cp s3://your-bucket/repos/github/org/repo.bundle repo.bundle
aws s3 cp s3://your-bucket/repos/github/org/repo_lfs.tar.gz repo_lfs.tar.gz

# Clone the bundle
git clone repo.bundle restored-repo
cd restored-repo

# Restore LFS objects
mkdir -p .git/lfs
tar -xzf ../repo_lfs.tar.gz -C .git/lfs/

# Checkout LFS files (no network needed)
git lfs checkout

# Verify LFS files are restored
git lfs ls-files
```

### From an Archive

```bash
# Download and extract
aws s3 cp s3://your-bucket/repos/github/org/repo.tar.gz repo.tar.gz
tar -xzf repo.tar.gz

# Convert bare repo to normal repo
git clone repo.git restored-repo
```

## Automating Backups

### Linux/Mac (using cron)

Add to your crontab (runs daily at 2 AM):
```bash
0 2 * * * repo-backup s3 >> /var/log/repo-backup.log 2>&1
```

### Windows (using Task Scheduler)

Create a batch file:
```batch
repo-backup s3
```
Then schedule it in Task Scheduler.

### GitHub Actions

```yaml
name: Backup Repos
on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM UTC
  workflow_dispatch:     # Manual trigger

jobs:
  backup:
    runs-on: ubuntu-latest
    steps:
      - uses: astral-sh/setup-uv@v4
      - run: |
          uv tool install git+https://github.com/hyperi-io/repo-backup
          repo-backup s3
        env:
          AWS_ACCESS_KEY_ID: ${{ secrets.AWS_ACCESS_KEY_ID }}
          AWS_SECRET_ACCESS_KEY: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          GITHUB_TOKEN: ${{ secrets.BACKUP_GITHUB_TOKEN }}
```

## Security Notes

### Keep Your Tokens Safe

- Never commit tokens to git (seriously, don't)
- Use environment variables for sensitive stuff:
  ```bash
  export GITHUB_TOKEN=ghp_...
  export AWS_ACCESS_KEY_ID=...
  ```
- Rotate tokens regularly
- Give tokens minimal permissions needed

### AWS Security

Check out [AWS.md](AWS.md) for the full security guide, including IAM roles, encryption, and cost optimization.

## Troubleshooting Common Issues

### Authentication Problems
- Double-check your tokens have the right permissions
- Make sure tokens haven't expired
- Verify you can reach the git platforms from your network

### S3 Upload Issues
- See [AWS.md - Troubleshooting](AWS.md#troubleshooting) for AWS-specific problems
- Check AWS credentials are set correctly
- Verify the bucket exists and you can access it

### Large Repository Problems
- Try fewer parallel workers
- Make sure you have enough disk space (2x the largest repo)
- Consider backing up huge repos separately

### Running Out of Space
- The tool uses `./.tmp` for temporary files
- These get cleaned up automatically
- Make sure you have enough space for the largest repo × 2

## Performance Tips

### Worker Count
- Default is 5 (good for most cases)
- Fast connection? Try 10-20
- Limited bandwidth? Use 2-3

### Smart Filtering
- Skip test/demo repositories
- Focus on production code
- Use patterns to exclude unnecessary repos

### Timing
- Run during off-peak hours
- Stagger backups if you have many repos

## Contributing

We welcome contributions! Check out [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Release Process

We use semantic-release for automated versioning:
- Commits to `main` trigger automatic releases
- Version numbers are determined from commit messages
- CHANGELOG.md is generated automatically
- Never manually create tags or edit the changelog

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details

## Security Reminder

**About Those Tokens:**
- Never put real tokens in code you commit
- Use `.env.local` for your actual credentials (it's git-ignored)
- The `.env` file in the repo has only examples
- Rotate tokens regularly
- Use separate, limited tokens for CI/CD

## Need Help?

If something's not working:
1. Check the logs - they're pretty detailed
2. Verify your `.env` configuration
3. Make sure you have all prerequisites installed
4. Review the token setup instructions above
5. Check our [issues](https://github.com/hyperi-io/repo-backup/issues) page

---

Built with practicality in mind. We needed reliable repository backups, so we made this. Hope it helps you too!