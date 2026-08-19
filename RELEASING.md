# Releasing

Publishing runs on [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/): GitHub Actions
mints a short-lived OIDC token that PyPI verifies against a registered publisher. There is no API token
in the repository, in secrets, or on anyone's laptop.

## One-time setup on PyPI

`ai-blackbox-recorder` does not exist on PyPI yet, so it needs a **pending publisher** — a publisher
registered for a project that has not been created. Go to
[PyPI → Your account → Publishing](https://pypi.org/manage/account/publishing/) and add a GitHub publisher
with exactly these values:

| Field | Value |
| :--- | :--- |
| PyPI project name | `ai-blackbox-recorder` |
| Owner | `amyotoff` |
| Repository name | `blackbox-recorder` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

The workflow filename and the environment name must match
[`.github/workflows/publish.yml`](.github/workflows/publish.yml) exactly, or PyPI rejects the upload.

> A pending publisher **does not reserve the name**. `ai-blackbox-recorder` is only claimed on the first
> successful publish, so until then someone else can still take it.

## Cutting a release

1. Bump the version in `pyproject.toml`, `ai_blackbox_recorder/__init__.py` and the README badge — all three.
2. Merge to `main` with CI green.
3. Publish a GitHub release tagged `vX.Y.Z`.

The workflow builds the sdist and wheel, refuses to continue if the tag disagrees with the version in
`pyproject.toml`, and uploads to PyPI. Attach the same artifacts to the GitHub release if you want them
downloadable without pip.

## The first publish

The v0.7.0 tag was cut before this workflow existed, so it cannot run from that tag. Publish it once by hand:
**Actions → Publish to PyPI → Run workflow → branch `main`** (which is at 0.7.0). The tag check is skipped for
manual runs. Every release after that publishes on its own.

## Trying it against TestPyPI first

Register a second pending publisher with the same values on [TestPyPI](https://test.pypi.org/manage/account/publishing/),
then add `with: {repository-url: https://test.pypi.org/legacy/}` to the `pypa/gh-action-pypi-publish` step
on a scratch branch. Worth doing once, since the first real upload is what claims the name.
