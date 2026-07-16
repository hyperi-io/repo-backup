# Contributing to Repository Backup Tool

Hey there! We're really excited that you're interested in contributing to this project. Whether you're fixing bugs, adding features, or improving documentation, every contribution helps make this tool better for everyone.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Environment](#development-environment)
- [How to Contribute](#how-to-contribute)
- [Coding Guidelines](#coding-guidelines)
- [Testing Your Changes](#testing-your-changes)
- [Submitting Changes](#submitting-changes)
- [Release Process](#release-process)
- [Community and Support](#community-and-support)

## Getting Started

First off, thanks for taking the time to contribute! This project is all about making repository backups simple and reliable for enterprise teams. We built it because we needed a solid way to back up our repos across multiple platforms, and we hope it helps you too.

### Quick Setup

Get your development environment running in just a few steps:

```bash
# Grab the code
git clone https://github.com/hyperi-io/repo-backup.git
cd repo-backup

# Install with dev and test dependencies
uv sync --extra dev --extra test

# Run the local CI checks (format, lint, security, tests, build)
./scripts/ci

# Or just run the tests
uv run pytest tests/ -v
```

That's it! You're ready to start contributing.

## Development Environment

### What You'll Need

- Python 3.11 or newer
- Git (obviously!)
- uv for dependency management
- AWS CLI (if you're working on S3 features)

### Setting Up Your Environment

After cloning the repo, you'll want to set up your local config:

```bash
# Copy the example config
cp .env.example .env

# Edit it with your test credentials
# (Don't worry, .env is git-ignored)
vim .env
```

Pro tip: Use test repositories and sandboxed environments when developing. Nobody wants to accidentally mess with production repos!

## How to Contribute

### Found a Bug?

Bugs happen! If you've found one:

1. Check if someone's already reported it in our [issues](https://github.com/hyperi-io/repo-backup/issues)
2. If not, open a new issue with:
   - What you were trying to do
   - What happened instead
   - Steps to reproduce it
   - Your environment details (OS, Python version, etc.)

### Want to Add a Feature?

Awesome! Here's how to go about it:

1. Open an issue first to discuss the idea
2. Fork the repository
3. Create a feature branch (`git checkout -b feature/amazing-new-thing`)
4. Write your code (and tests!)
5. Make sure all tests pass
6. Submit a pull request

### Improving Documentation?

Documentation improvements are always welcome! Whether it's fixing typos, adding examples, or clarifying confusing parts, good docs make everyone's life easier.

## Coding Guidelines

We try to keep things clean and consistent. Here's what we aim for:

### Python Style

We follow PEP 8 with a few preferences:

- Line length: 88 characters (black default)
- Use type hints when it makes the code clearer
- Write docstrings that actually help future developers (including yourself)
- Code is formatted with black, isort, and checked with ruff

### Code Philosophy

- Keep it simple - clever code is hard to maintain
- Handle errors gracefully - users should understand what went wrong
- Log useful information - but don't spam the logs
- Test the important stuff - 100% coverage isn't the goal, confidence is

### Commit Messages

We use conventional commits for automated versioning. It's pretty straightforward:

```bash
# Adding a feature
feat: add support for GitHub Enterprise

# Fixing a bug
fix: handle empty repositories in git bundle creation

# Breaking change (bumps major version)
feat!: change config file format to YAML

# With more context
fix(s3): retry uploads on temporary network failures

The S3 uploader now retries failed uploads up to 3 times
with exponential backoff. This handles temporary network
issues without failing the entire backup job.
```

Types you'll use most often:

- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation updates
- `test`: Adding or updating tests
- `refactor`: Code cleanup that doesn't change functionality
- `perf`: Performance improvements

## Testing Your Changes

We've got a decent test suite that helps catch issues early:

```bash
# Run all tests
uv run pytest tests/ -v

# Test a specific module
uv run pytest tests/test_github_manager.py -v

# Check test coverage
uv run pytest tests/ --cov=src --cov-report=html

# Run the tool in test mode (uses small test repos)
uv run repo-backup local /tmp/test --test
```

### Writing Tests

When adding new features:

- Write tests that cover the main use cases
- Test error conditions too
- Avoid mocks where possible - use real git repos and actual operations
- Use descriptive test names that explain what's being tested

Example:

```python
def test_github_manager_handles_rate_limiting():
    """Verify that GitHub API rate limit responses trigger appropriate backoff"""
    # Your test here
```

## Submitting Changes

### Pull Request Process

1. Fork the repo and create your branch from `master`
2. Make your changes
3. Run `./scripts/ci` to ensure all checks pass
4. Update documentation if needed
5. Push your branch and open a PR

### What Happens Next?

We'll review your PR as soon as we can. We might ask for some changes or have questions - it's all part of the process. Once everything looks good, we'll merge it!

### PR Guidelines

A good PR:

- Has a clear description of what it does and why
- Includes tests for new functionality
- Updates documentation if needed
- Follows the existing code style
- Has commits that tell a story

## Release Process

We use semantic-release to automate our versioning and releases. Here's how it works:

### Automated Releases

When changes are merged to `master`, our CI automatically:

1. Analyzes commit messages to determine the version bump
2. Updates the version number in all the right places
3. Generates/updates CHANGELOG.md
4. Creates a git tag
5. Publishes a GitHub release

### Version Bumps

Based on your commit messages:

- `fix:` commits trigger patch releases (1.2.3 → 1.2.4)
- `feat:` commits trigger minor releases (1.2.3 → 1.3.0)
- Breaking changes trigger major releases (1.2.3 → 2.0.0)

### Manual Release (Emergency Only)

If you absolutely need to create a release manually:

```bash
# See what would be released
uv run semantic-release version --noop

# Actually create the release
uv run semantic-release version
```

But really, just let the automation handle it. That's what it's there for!

### Important Note About Releases

**Never manually create tags or edit CHANGELOG.md** - semantic-release handles all of this automatically. If you manually create tags or changelog entries, it can break the automation.

## Community and Support

### Code of Conduct

We're committed to providing a welcoming and inclusive environment. Be kind, be respectful, and remember that we're all here to make something useful.

### Getting Help

Stuck on something? No worries!

- Check the existing [documentation](README.md)
- Look through [closed issues](https://github.com/hyperi-io/repo-backup/issues?q=is%3Aissue+is%3Aclosed) - someone might have had the same question
- Open a new issue if you're still stuck
- Be patient - we're a small team and sometimes life gets busy

### Communication Channels

Most of our discussion happens in GitHub issues. It keeps everything public and searchable, which helps future contributors.

### Recognition

We appreciate every contribution, big or small. Contributors are recognized in our release notes and your commits are forever part of the project's history.

## License

By contributing to this project, you agree that your contributions will be licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

## Final Thoughts

Open source is awesome because people like you make it better. Whether you're fixing a typo or adding a major feature, you're helping make this tool more useful for everyone. Thanks for being part of it!

---

Questions? Issues? Just want to say hi? Open an issue and let's chat!
