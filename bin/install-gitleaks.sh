#!/usr/bin/env bash
set -euo pipefail

version="${GITLEAKS_VERSION:-8.30.1}"
destination="${1:?usage: install-gitleaks.sh <destination>}"

case "$(uname -s)" in
  Linux) platform="linux" ;;
  Darwin) platform="darwin" ;;
  *)
    echo "Unsupported Gitleaks platform: $(uname -s)" >&2
    exit 1
    ;;
esac

case "$(uname -m)" in
  x86_64) architecture="x64" ;;
  arm64 | aarch64) architecture="arm64" ;;
  *)
    echo "Unsupported Gitleaks architecture: $(uname -m)" >&2
    exit 1
    ;;
esac

mkdir -p "$destination"
archive="gitleaks_${version}_${platform}_${architecture}.tar.gz"
release="https://github.com/gitleaks/gitleaks/releases/download/v${version}"
archive_path="$destination/$archive"
checksums_path="$destination/gitleaks_${version}_checksums.txt"

curl -sSfL "$release/$archive" -o "$archive_path"
curl -sSfL "$release/gitleaks_${version}_checksums.txt" -o "$checksums_path"

(
  cd "$destination"
  checksum_line="$(grep "  $archive\$" "$(basename "$checksums_path")")"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s\n' "$checksum_line" | sha256sum --check -
  else
    printf '%s\n' "$checksum_line" | shasum -a 256 --check
  fi
)

tar -xzf "$archive_path" -C "$destination" gitleaks
"$destination/gitleaks" version
