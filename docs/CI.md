# Continuous integration

Alfred keeps pull-request CI focused on the surface that changed. This matters
in a high-activity repository because GitHub rounds every hosted job up to a
full billed minute.

## Pull-request checks

`CI` always runs one core-quality job. It performs the public-repository scrub
and PR metadata checks, then runs Python and shell static checks when source
files changed. The same job identifies whether the PR needs:

- Python runtime tests
- desktop type checks, tests, protocol tests, Rust checks, and a native build
- the dependency-free site proof-emitter tests

The `CI / Required` job is the stable aggregate result. Add that single context
to the main-branch ruleset rather than requiring conditional jobs individually.
Conditional jobs report `skipped` for unrelated changes and the aggregate gate
accepts only `success` or `skipped`.

Pull requests test Python 3.13, the current runtime. A weekly run tests 3.11,
3.12, and 3.13. This keeps the supported range checked without repeating three
nearly identical jobs after every pushed commit.

Desktop web and protocol checks run for desktop or local-server contract
changes. Rust setup, native dependencies, native tests, and the Tauri build run
only when the native shell or desktop dependency manifests change.

CodeQL runs as one database cluster for supported source and workflow changes.
Alfred contains Python, JavaScript/TypeScript, and GitHub Actions code, so those
are the three analysis languages. There is no Ruby source to analyze.

The core CI job scans only the commits introduced by a pull request with
Gitleaks. A weekly Gitleaks run scans all history. This catches new secrets at
review time without paying for a second runner and without repeating the same
full-history scan on every synchronize event.

Main is protected from direct updates. CI, CodeQL, and pull-request gitleaks do
not repeat after a reviewed PR is merged. The site workflow is the exception:
its main-branch run is the deployment.

## Run the complete suite locally

Before pushing runtime changes:

```sh
uv run --with 'ruff==0.15.22' ruff check .
uv run --with 'ruff==0.15.22' ruff format --check .
uv run --with 'mypy==2.3.0' mypy lib/
uv run --python 3.11 --with pytest --with fastapi --with httpx --with httpx2 pytest tests/ -v
uv run --python 3.12 --with pytest --with fastapi --with httpx --with httpx2 pytest tests/ -v
uv run --python 3.13 --with pytest --with fastapi --with httpx --with httpx2 pytest tests/ -v
find . -type f \( -name "*.sh" -o -name "*.bash" \) -print0 | xargs -0 shellcheck -S warning
bash bin/scrub-check.sh
```

For desktop changes:

```sh
cd clients/desktop
npm ci
npm run typecheck
npm run test
npx playwright install chromium
npm run test:contract
cargo fmt --manifest-path src-tauri/Cargo.toml --check
cargo test --manifest-path src-tauri/Cargo.toml
npm run tauri -- build --no-bundle --ci
```

For site changes:

```sh
cd site
npm ci
npm test
npm run build
```

Use the manual `CI` dispatch when a PR needs the full three-version Python
compatibility run and native desktop validation regardless of changed paths.
Use the manual `scrub-history` workflow only while preparing or verifying a
supervised history rewrite.
