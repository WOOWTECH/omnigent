"""Portable artifact boundaries: deterministic bytes and rejection of wrong receipts."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "woowtech_mcp_artifact", _ROOT / "scripts/woowtech_mcp_artifact.py"
)
assert _SPEC and _SPEC.loader
artifact = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(artifact)


def _build_fixture(path: Path) -> tuple[dict, dict]:
    path.mkdir()
    identity = {
        "sourceCommit": "a" * 40,
        "sourceDateEpoch": 1700000000,
        "sourceTreeSha256": artifact.sha256(artifact.canonical({"source.py": "b" * 64})),
        "publishable": True,
    }
    with zipfile.ZipFile(path / artifact.WHEEL, "w") as wheel:
        for name, markers in artifact.MARKERS.items():
            wheel.writestr(name, b"\n".join(markers))
        wheel.writestr(artifact.UI + "index.html", b"<html>built UI</html>")
        wheel.writestr(
            artifact.UI + "assets/native-mcp.js",
            b'mcp-server connection_url displayName:"OpenCode",iconKind:"opencode",'
            b'sortRank:25,capabilities:["modelPicker"]',
        )
        wheel.writestr(
            f"omnigent-{artifact.CORE_VERSION}.dist-info/METADATA",
            f"Name: omnigent\nVersion: {artifact.CORE_VERSION}\n".encode(),
        )
        wheel.writestr("omnigent/_build_info.py", b"BUILD_TIME_EPOCH = 1")
    files = artifact.normalize_wheel(path / artifact.WHEEL, identity)
    manifest = {"files": files, "sourceFiles": {"source.py": "b" * 64}}
    artifact.write_json(path / artifact.MANIFEST, manifest)
    receipt = {
        **identity,
        "wheelFilename": artifact.WHEEL,
        "wheelSha256": artifact.sha256((path / artifact.WHEEL).read_bytes()),
        "contentManifestSha256": artifact.sha256((path / artifact.MANIFEST).read_bytes()),
        "contentSha256": artifact.sha256(artifact.canonical(files)),
        "uiSha256": artifact.sha256(
            artifact.canonical({k: v for k, v in files.items() if k.startswith(artifact.UI)})
        ),
    }
    artifact.write_json(path / artifact.RECEIPT, receipt)
    return identity, receipt


def test_normalization_is_repeatable_and_keeps_valid_record(tmp_path: Path) -> None:
    directory = tmp_path / "wheel"
    identity, receipt = _build_fixture(directory)
    first = (directory / artifact.WHEEL).read_bytes()
    artifact.normalize_wheel(directory / artifact.WHEEL, identity)
    assert (directory / artifact.WHEEL).read_bytes() == first
    assert artifact.verify(directory, "a" * 40)[0] == receipt
    payload = artifact.wheel_payload(directory / artifact.WHEEL)
    assert b"1700000000" in payload["omnigent/_build_info.py"]
    assert payload[f"omnigent-{artifact.CORE_VERSION}.dist-info/RECORD"].count(b",sha256=") == len(payload) - 1


@pytest.mark.parametrize("failure", ["wheel", "manifest", "source", "snapshot"])
def test_rejects_untrusted_artifacts(tmp_path: Path, failure: str) -> None:
    directory = tmp_path / "wheel"
    _, receipt = _build_fixture(directory)
    source = "a" * 40
    if failure == "wheel":
        with (directory / artifact.WHEEL).open("ab") as stream:
            stream.write(b"tampered")
    elif failure == "manifest":
        (directory / artifact.MANIFEST).write_text("{}")
    elif failure == "source":
        source = "b" * 40
    else:
        receipt["publishable"] = False
        receipt["sourceCommit"] = None
        artifact.write_json(directory / artifact.RECEIPT, receipt)
    with pytest.raises(ValueError):
        artifact.verify(directory, source)


def test_rejects_missing_migration_or_old_ui(tmp_path: Path) -> None:
    directory = tmp_path / "wheel"
    _build_fixture(directory)
    payload = artifact.wheel_payload(directory / artifact.WHEEL)
    migration = next(name for name in payload if "za3b4c5d6e7f" in name)
    del payload[migration]
    with pytest.raises(ValueError, match="Missing"):
        artifact.validate_payload(payload)


@pytest.fixture
def images(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setitem(sys.modules, "woowtech_mcp_artifact", artifact)
    spec = importlib.util.spec_from_file_location(
        "woow_image_receipt", _ROOT / "scripts/woowtech_mcp_image_receipt.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("variant", ["server", "server-kubernetes"])
@pytest.mark.parametrize("failure", ["wheelSha256", "variant", "baseImage", "tag", "image"])
def test_architecture_receipts_reject_mixed_identity(images, variant: str, failure: str) -> None:
    common = {
        "sourceCommit": "a" * 40,
        "component": variant,
        "variant": variant,
        "publishable": True,
        "wheelSha256": "b" * 64,
        "uiSha256": "c" * 64,
        "contentSha256": "d" * 64,
        "baseImage": f"ghcr.io/omnigent-ai/omnigent-{variant}:v{artifact.CORE_VERSION}@sha256:" + "e" * 64,
        "runId": "1",
        "runAttempt": "1",
    }
    receipts = [
        {
            **common,
            "architecture": "linux/" + arch,
            "tag": images.candidate_tag("a" * 40, variant, "1", "1", arch),
            "image": images.PUBLIC_IMAGE + "@sha256:" + digest * 64,
        }
        for arch, digest in [("amd64", "f"), ("arm64", "0")]
    ]
    images.validate_architectures(receipts, "a" * 40, variant)
    receipts[1][failure] = "wrong"
    with pytest.raises(ValueError):
        images.validate_architectures(receipts, "a" * 40, variant)


@pytest.mark.parametrize("variant", ["server", "server-kubernetes"])
def test_public_candidate_tag_and_receipt(
    images, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    directory = tmp_path / "wheel"
    _build_fixture(directory)
    tag = images.candidate_tag("a" * 40, variant, "42", "2")
    assert tag == (
        f"ghcr.io/woowtech/omnigent-server-kubernetes:{artifact.CORE_VERSION}-mcp.1-"
        + variant
        + "-sha-"
        + "a" * 40
        + "-run-42-2"
    )
    base = f"ghcr.io/omnigent-ai/omnigent-{variant}:v{artifact.CORE_VERSION}@sha256:" + "e" * 64
    for key, value in {
        "VARIANT": variant,
        "ARCH": "amd64",
        "BASE_IMAGE": base,
        "IMAGE_TAG": tag + "-amd64",
        "GITHUB_RUN_ID": "42",
        "GITHUB_RUN_ATTEMPT": "2",
    }.items():
        monkeypatch.setenv(key, value)
    registry = tmp_path / "registry.json"
    artifact.write_json(registry, {"digest": "sha256:" + "f" * 64})
    destination = tmp_path / "image-receipt.json"
    images.architecture(
        argparse.Namespace(artifact=directory, manifest=registry, output=destination)
    )
    receipt = json.loads(destination.read_text())
    assert receipt["variant"] == receipt["component"] == variant
    assert receipt["baseImage"] == base
    assert receipt["image"] == receipt["wheelImage"] == images.PUBLIC_IMAGE + "@sha256:" + "f" * 64
    assert receipt["tag"] == tag + "-amd64"


def test_workflow_passes_exact_shared_wheel_and_preserves_host() -> None:
    import yaml

    workflow = yaml.safe_load((_ROOT / ".github/workflows/woowtech-mcp-artifacts.yml").read_text())
    build = next(
        step
        for step in workflow["jobs"]["images"]["steps"]
        if str(step.get("uses", "")).startswith("docker/build-push-action@")
    )
    assert build["with"]["build-contexts"] == "mcp-artifact=./portable-wheel"
    assert build["with"]["push"] is False
    assert build["with"]["labels"] == "io.woowtech.omnigent.variant=${{ matrix.variant }}"
    tag_step = next(
        step for step in workflow["jobs"]["images"]["steps"] if step.get("id") == "candidate"
    )
    assert "woowtech_mcp_image_receipt.py tag" in tag_step["run"]
    verification = next(
        step
        for step in workflow["jobs"]["images"]["steps"]
        if step.get("name") == "Verify candidate runtime and OCI identity"
    )
    assert "io.woowtech.omnigent.variant" in verification["run"]
    assert "io.woowtech.omnigent.base-image" in verification["run"]
    assert "needs.wheel.outputs.wheel_sha256" in build["with"]["build-args"]
    assert "IMAGE_VARIANT=${{ matrix.variant }}" in build["with"]["build-args"]
    assert '--image-variant "$VARIANT"' in verification["run"]
    dockerfile = (_ROOT / "deploy/docker/Dockerfile.woowtech-overlay-server").read_text()
    assert '--image-variant "$IMAGE_VARIANT"' in dockerfile
    old = yaml.safe_load(
        (_ROOT / ".github/workflows/woowtech-overlay-v0230-qa-image.yml").read_text()
    )
    assert old["jobs"]["native-mcp"]["uses"] == "./.github/workflows/woowtech-mcp-artifacts.yml"
    assert old["jobs"]["build"]["strategy"]["matrix"]["include"] == [
        {"component": "host", "image": "ghcr.io/woowtech/omnigent-host"}
    ]
    # The external receipt cannot create a wheel checksum circular dependency.
    assert json.loads(artifact.canonical({"wheelFilename": artifact.WHEEL}))["wheelFilename"]


@pytest.mark.parametrize("variant", ["server", "server-kubernetes"])
def test_manifest_downloads_only_its_two_architecture_receipts(variant: str) -> None:
    import fnmatch

    import yaml

    workflow = yaml.safe_load((_ROOT / ".github/workflows/woowtech-mcp-artifacts.yml").read_text())
    downloads = [
        step["with"]
        for step in workflow["jobs"]["manifests"]["steps"]
        if str(step.get("uses", "")).startswith("actions/download-artifact@")
    ]
    available = [
        f"woow-mcp-image-{image_variant}-{arch}-42-2"
        for image_variant in ("server", "server-kubernetes")
        for arch in ("amd64", "arm64")
    ]
    selected = []
    for download in downloads:
        selector = download.get("name", download.get("pattern", ""))
        for expression, value in (
            ("${{ matrix.variant }}", variant),
            ("${{ github.run_id }}", "42"),
            ("${{ github.run_attempt }}", "2"),
        ):
            selector = selector.replace(expression, value)
        selected.extend(
            name
            for name in available
            if (name == selector if "name" in download else fnmatch.fnmatchcase(name, selector))
        )
    assert sorted(selected) == [
        f"woow-mcp-image-{variant}-amd64-42-2",
        f"woow-mcp-image-{variant}-arm64-42-2",
    ]
    assert all("name" in download for download in downloads)
    assert {download["path"] for download in downloads} == {
        "image-receipts/amd64",
        "image-receipts/arm64",
    }


@pytest.mark.parametrize("variant", ["server", "server-kubernetes"])
@pytest.mark.parametrize("oauthlib", [None, "4.0.0", "4.0.1"])
@pytest.mark.parametrize("pip", ["26.2", "26.3"])
def test_debian_security_matches_variant_dependencies(
    monkeypatch: pytest.MonkeyPatch, variant: str, oauthlib: str | None, pip: str
) -> None:
    def version(name: str) -> str:
        if name == "oauthlib":
            if oauthlib is None:
                raise artifact.importlib.metadata.PackageNotFoundError(name)
            return oauthlib
        assert name == "pip"
        return pip

    monkeypatch.setattr(artifact.importlib.metadata, "version", version)
    acceptable = pip == "26.3" and (
        oauthlib == "4.0.1" or (variant == "server" and oauthlib is None)
    )
    if acceptable:
        artifact.verify_debian_security(variant)
    else:
        with pytest.raises((ValueError, artifact.importlib.metadata.PackageNotFoundError)):
            artifact.verify_debian_security(variant)
