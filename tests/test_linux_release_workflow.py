from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/package-linux.yml"


def test_linux_packaging_uses_a_verified_tag_and_separate_upload_job() -> None:
    assert WORKFLOW.is_file()
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "push:" not in workflow.partition("permissions:")[0]
    assert "RELEASE_INPUT_TAG: ${{ inputs.tag }}" in workflow
    assert 'tag="${{ inputs.tag }}"' not in workflow
    assert "^v[0-9]+\\.[0-9]+\\.[0-9]+$" in workflow
    assert 'if [ "$GITHUB_REF" != "refs/heads/main" ]; then' in workflow
    assert "bin/release-source-gate.sh" in workflow
    assert "--jq '.verification.verified'" in workflow
    assert "--jq '.commit.verification.verified'" in workflow
    assert workflow.count("--json isDraft") == 2

    build_job = workflow.partition("  build:")[2].partition("  upload:")[0]
    upload_job = workflow.partition("  upload:")[2]
    assert "permissions:\n      contents: read" in build_job
    assert "npm run tauri -- build --bundles appimage,deb --ci" in build_job
    assert "Alfred.AppImage" in build_job
    assert "Alfred.deb" in build_job
    assert "actions/upload-artifact@v7" in build_job
    assert "contents: write" not in build_job

    assert "needs: build" in upload_job
    assert "permissions:\n      contents: write" in upload_job
    assert "actions/download-artifact@v8" in upload_job
    assert "--json isDraft" in upload_job
    assert 'if [ "$is_draft" != "true" ]; then' in upload_job
    assert 'gh release upload "$tag"' in upload_job
    assert '"packages/Alfred.AppImage"' in upload_job
    assert '"packages/Alfred.deb"' in upload_job


def test_linux_package_validation_checks_both_formats_and_versions() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert 'test "$(dpkg-deb -f packages/Alfred.deb Version)" = "$version"' in workflow
    assert "dpkg-deb --info packages/Alfred.deb" in workflow
    assert "--appimage-extract" in workflow
    assert "file -b packages/Alfred.AppImage" in workflow
    assert "file -b packages/Alfred.deb" in workflow
    assert "mktemp -d" in workflow
    assert "rm -rf" not in workflow
    assert "sha256sum packages/Alfred.AppImage packages/Alfred.deb" in workflow
