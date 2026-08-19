# Releasing

Publishing runs on [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/): GitHub Actions
mints a short-lived OIDC token that PyPI verifies against a registered publisher. There is no API token
in the repository, in secrets, or on anyone's laptop.

## The publisher

[`ai-blackbox-recorder`](https://pypi.org/project/ai-blackbox-recorder/) is registered on PyPI with a
GitHub publisher, configured under
[PyPI → Your account → Publishing](https://pypi.org/manage/account/publishing/):

| Field | Value |
| :--- | :--- |
| PyPI project name | `ai-blackbox-recorder` |
| Owner | `amyotoff` |
| Repository name | `blackbox-recorder` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The workflow filename and the environment name must keep matching
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) exactly — rename either one and PyPI
rejects the upload. Nothing else authenticates the release: there is no token to rotate or leak.

## Cutting a release

1. Bump the version in `pyproject.toml` and `ai_blackbox_recorder/__init__.py` — both. (The README badge
   reads the current version from PyPI, so it needs no bump.)
2. Merge to `main` with CI green.
3. Publish a GitHub release tagged `vX.Y.Z`.

That is the whole process. The workflow builds the sdist and wheel, refuses to continue if the tag
disagrees with the version in `pyproject.toml`, and uploads to PyPI with digital attestations. Attach the
same artifacts to the GitHub release if you want them downloadable without pip.

A version can never be re-uploaded to PyPI: if a release is wrong, yank it there and ship a new patch
version.

## Publishing without a release

**Actions → Publish to PyPI → Run workflow** publishes whatever ref you pick. The tag check is skipped for
manual runs, so the version in `pyproject.toml` is what gets published — check it first. This is how 0.7.0
went out, since its tag predates the workflow.
