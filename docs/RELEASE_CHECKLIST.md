# GitHub Release Checklist

Use `code/` as the repository root. Do not upload the parent MDT directory: it contains old source copies, archives, and reference PDFs that are outside the prepared release.

## 1. Choose repository settings

Recommended repository name: `mdt-closed-loop`.

Suggested description:

> Event-driven research prototype for personalized EDA/HRV closed-loop music digital therapeutics.

Suggested topics:

```text
digital-therapeutics closed-loop-control music-therapy eda hrv
physiological-signal-processing python research-prototype
```

Choose visibility deliberately:

- **Private** while the study protocol, intellectual property, authorship, and third-party integrations are still under review.
- **Public** only after the license, privacy review, and all publication rights are confirmed.

## 2. Choose a license

No open-source license is included because licensing is a legal authorization that the code owner must select.

Common options:

- **Apache License 2.0**: permissive and includes an explicit patent license and patent-termination terms.
- **MIT License**: short, permissive, and widely used, but has less explicit patent language.
- **Proprietary/no license**: others may view public source but do not receive broad permission to copy, modify, or redistribute it.

Confirm ownership, employer/institution rights, co-author approval, and any patent strategy before adding a license. GitHub can generate the selected standard text when creating or editing `LICENSE`.

## 3. Review identity and privacy

- Replace any placeholder repository URL after creating the GitHub repository.
- Add release and comparison links to `CHANGELOG.md` after the final repository URL is known.
- Decide the public author name and contact channel.
- Do not publish a personal email unless intentional.
- Confirm that `user_id` examples are pseudonymous.
- Ensure no patient data, API keys, credentials, `.env` files, local data, or logs are present.
- Confirm that the reference PDFs and the old `files/` copy are not in the upload directory.

## 4. Local preflight

From this directory:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
ruff check mdt_core tests demo.py
mypy --no-site-packages --ignore-missing-imports mdt_core tests demo.py
python -W error -m unittest discover -s tests -v
python demo.py
```

Check the exact upload set:

```bash
find . -type f \
  -not -path './.venv/*' \
  -not -path './.git/*' \
  | sort
```

## 5. Create and upload

### GitHub web upload

1. Create an empty repository without auto-generating a README or license.
2. Open **Add file → Upload files**.
3. Upload the contents of this directory, not the directory's parent.
4. Preserve `.github/workflows/ci.yml`; hidden directories may require using Git or GitHub Desktop rather than drag-and-drop from Finder.
5. Commit with a message such as `Initial research prototype release`.
6. Verify the Actions tab and README rendering.

### Git command line

```bash
git init
git branch -M main
git add .
git status
git commit -m "Initial research prototype release"
git remote add origin https://github.com/<account>/mdt-closed-loop.git
git push -u origin main
```

Inspect `git status` before committing. Do not use `git add` from the parent MDT directory.

## 6. GitHub settings after upload

- Confirm the default branch is `main`.
- Add the description and topics above.
- Enable branch protection when collaborators are added.
- Require all `CI` matrix checks before merge.
- Enable Dependabot alerts and secret scanning if available.
- Add the selected `LICENSE`.
- Create a `v0.1.0` release only after CI passes.

## 7. Final public-release review

- README and Mermaid diagrams render correctly.
- English and Chinese documentation links work.
- The medical/research disclaimer remains prominent.
- CI passes on all configured Python versions.
- `MubertEngine` is still described as unimplemented.
- Synthetic validation is not presented as clinical efficacy.
- Security contact details in `SECURITY.md` have been customized.
- A license has been selected or the absence of redistribution permission is intentional.
