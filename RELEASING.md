# Releases

## Current status

The declared version is **0.1.0**, the initial release. It has not been published;
there is no `v0.1.0` tag or GitHub Release. Keep 0.1.0 for this first release—no
version bump is required just to publish it.

Releases are downloadable GitHub Release assets (a wheel, source distribution, and
checksums), not publication to PyPI or another Python package index. Once a release
is available, download its wheel or source distribution from
[GitHub Releases](https://github.com/SerhatBasesgioglu/maestro/releases) and install
the downloaded file with pip. Select any needed optional extras when installing.

The release workflow is not active until `.github/workflows/release.yml` is merged
to the default branch. A workflow file on a feature branch does not enable its
Actions run button or publish a release.

## Version policy and preparation

Use Semantic Versioning:

- A compatible bug fix gets a patch version.
- A feature or incompatible API change before 1.0 gets a minor version.
- 1.0.0 marks the stable API. After 1.0, incompatible API changes get a major
  version; compatible fixes and additions use patch and minor versions respectively.

Version bumps are prepared in a pull request to `main`; ordinary development
commits do not publish releases or automatically bump the version. The version is
currently declared in both `pyproject.toml` and `src/maestro/__init__.py`; update
both literals together. Run `uv lock` after changing the version. Keep unreleased
change notes in the `Unreleased` section of [CHANGELOG.md](CHANGELOG.md). Merge the
version/changelog PR after its normal CI checks pass before starting a release.

## Publish a release

After the release workflow is on the default branch:

1. Confirm the intended version is declared on `main` and its CI checks have passed
   on Python 3.11 and 3.14.
2. In GitHub, open **Actions → Release → Run workflow** and run it against `main`.
   This is a deliberate, manual release—not a publication on every commit to
   `main`. The workflow uses the declared version and creates the matching
   `v<version>` tag on the tested commit.
3. The release waits for the CI gates, then uses the wheel and source distribution
   artifact from that same CI run (built by the Python 3.11 job; the release job
   does not rebuild them). It prepares a draft GitHub Release, uploads the
   distributions and SHA-256 checksums, and publishes it with generated release
   notes. Alpha, beta, and release-candidate versions are marked as prereleases.

No local artifact download, GitHub CLI login, or separately supplied publishing
token is required for the manual workflow; it uses GitHub's provided token.

Alternatively, push an annotated `v<version>` tag on a `main` commit that contains
the release workflow. The tag must exactly match the source version, and the
workflow accepts only a tag pointing to a commit on `main`.

Published versions and tags cannot be overwritten. If a failed publication leaves
an unpublished draft, inspect it and delete only that draft before retrying. Never
move or delete a published tag or release asset.

## After publication

After the GitHub Release is actually published, replace the released version's
`Unreleased` heading in `CHANGELOG.md` with a dated release heading using the
actual publication date, preserve its notes, and add a fresh `Unreleased` section
for subsequent work. Do not record a release date or published tag before
publication.
