"""Record published native MCP images and join verified architecture digests."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path

from woowtech_mcp_artifact import require, verify, write_json

PUBLIC_IMAGE = "ghcr.io/woowtech/omnigent-server-kubernetes"
VARIANTS = ("server", "server-kubernetes")
CORE_VERSION = "0.23.0"


def candidate_tag(
    source: str, variant: str, run_id: str, attempt: str, arch: str | None = None
) -> str:
    require(variant in VARIANTS, "Unknown image variant")
    require(re.fullmatch(r"[0-9a-f]{40}", source) is not None, "Need immutable source commit")
    require(run_id.isdigit() and attempt.isdigit(), "Need numeric CI run and attempt")
    require(arch is None or arch in ("amd64", "arm64"), "Unknown architecture")
    suffix = f"-{arch}" if arch else ""
    return f"{PUBLIC_IMAGE}:{CORE_VERSION}-mcp.1-{variant}-sha-{source}-run-{run_id}-{attempt}{suffix}"


def digest_from_manifest(manifest: dict) -> str:
    digest = manifest.get("digest", "")
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is not None, "Missing registry digest")
    return digest


def architecture(args: argparse.Namespace) -> None:
    receipt, _ = verify(args.artifact)
    require(receipt["publishable"], "Cannot publish snapshot artifact")
    digest = digest_from_manifest(json.loads(args.manifest.read_text()))
    variant = os.environ["VARIANT"]
    tag = os.environ["IMAGE_TAG"]
    require(
        tag
        == candidate_tag(
            receipt["sourceCommit"],
            variant,
            os.environ["GITHUB_RUN_ID"],
            os.environ["GITHUB_RUN_ATTEMPT"],
            os.environ["ARCH"],
        ),
        "Candidate tag differs from receipt identity",
    )
    image = tag.rsplit(":", 1)[0] + "@" + digest
    write_json(
        args.output,
        {
            **receipt,
            "component": variant,
            "variant": variant,
            "baseImage": os.environ["BASE_IMAGE"],
            "architecture": "linux/" + os.environ["ARCH"],
            "tag": tag,
            "image": image,
            "imageDigest": digest,
            "wheelImage": image,
            "wheelPath": "/opt/woow-artifact/" + receipt["wheelFilename"],
            "runId": os.environ["GITHUB_RUN_ID"],
            "runAttempt": os.environ["GITHUB_RUN_ATTEMPT"],
        },
    )


def validate_architectures(receipts: list[dict], source_commit: str, variant: str) -> None:
    require(len(receipts) == 2, "Need exactly two architecture receipts")
    require(
        {r["architecture"] for r in receipts} == {"linux/amd64", "linux/arm64"},
        "Need verified amd64 and arm64 receipts",
    )
    for receipt in receipts:
        require(receipt["sourceCommit"] == source_commit, "Architecture source differs")
        require(
            receipt["component"] == variant and receipt["variant"] == variant,
            "Wrong image variant",
        )
        require(
            re.fullmatch(
                r"ghcr.io/omnigent-ai/omnigent-"
                + re.escape(variant)
                + re.escape(f":v{CORE_VERSION}@sha256:")
                + r"[0-9a-f]{64}",
                receipt["baseImage"],
            )
            is not None,
            "Wrong variant base image",
        )
        require(
            receipt["tag"]
            == candidate_tag(
                source_commit,
                variant,
                receipt["runId"],
                receipt["runAttempt"],
                receipt["architecture"].split("/")[1],
            ),
            "Architecture tag differs from receipt identity",
        )
        require(receipt["publishable"], "Unpublished snapshot receipt")
        require(
            re.fullmatch(
                re.escape(PUBLIC_IMAGE) + r"@sha256:[0-9a-f]{64}",
                receipt["image"],
            )
            is not None,
            "Unexpected immutable image reference",
        )
    for field in ("wheelSha256", "uiSha256", "contentSha256", "baseImage", "runId", "runAttempt"):
        require(receipts[0][field] == receipts[1][field], f"Architecture {field} differs")


def manifest(args: argparse.Namespace) -> None:
    receipts = sorted(
        [json.loads(path.read_text()) for path in args.receipts.rglob("image-receipt.json")],
        key=lambda receipt: receipt["architecture"],
    )
    source = os.environ["SOURCE_COMMIT"]
    variant = os.environ["VARIANT"]
    validate_architectures(receipts, source, variant)
    run_id, attempt = os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"]
    require(
        receipts[0]["runId"] == run_id and receipts[0]["runAttempt"] == attempt,
        "Architecture receipts must come from this CI run attempt",
    )
    tag = candidate_tag(source, variant, run_id, attempt)
    subprocess.run(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--tag",
            tag,
            *[receipt["image"] for receipt in receipts],
        ],
        check=True,
    )
    raw = subprocess.check_output(
        ["docker", "buildx", "imagetools", "inspect", tag, "--format", "{{json .Manifest}}"],
        text=True,
    )
    registry = json.loads(raw)
    digest = digest_from_manifest(registry)
    require(
        {item["digest"] for item in registry["manifests"]} == {r["imageDigest"] for r in receipts},
        "Registry manifest children differ",
    )
    image = tag.rsplit(":", 1)[0] + "@" + digest
    receipt = {
        **receipts[0],
        "architecture": [r["architecture"] for r in receipts],
        "tag": tag,
        "image": image,
        "imageDigest": digest,
        "wheelImage": image,
        "platforms": [{"architecture": r["architecture"], "image": r["image"]} for r in receipts],
    }
    write_json(args.output, receipt)
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write("```json\n" + json.dumps(receipt, indent=2) + "\n```\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tag")
    arch = commands.add_parser("architecture")
    arch.add_argument("--artifact", type=Path, required=True)
    arch.add_argument("--manifest", type=Path, required=True)
    index = commands.add_parser("manifest")
    index.add_argument("--receipts", type=Path, required=True)
    for command in (arch, index):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "tag":
        print(
            "tag="
            + candidate_tag(
                os.environ["SOURCE_COMMIT"],
                os.environ["VARIANT"],
                os.environ["GITHUB_RUN_ID"],
                os.environ["GITHUB_RUN_ATTEMPT"],
                os.environ["ARCH"],
            )
        )
    elif args.command == "architecture":
        architecture(args)
    else:
        manifest(args)


if __name__ == "__main__":
    main()
