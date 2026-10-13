"""The published images' web SPA build must not run under QEMU arm64 emulation."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / ".github/workflows/oss-publish-images.yml"
_DOCKERFILE = _ROOT / "deploy/docker/Dockerfile"

_FROM_LINE = re.compile(
    r"^FROM\s+(?P<flags>(?:--[\w-]+=\S+\s+)*)(?P<base>\S+)(?:\s+AS\s+(?P<name>\S+))?",
    re.IGNORECASE | re.MULTILINE,
)
_COPY_FROM = re.compile(
    r"^COPY\s+(?:--[\w-]+=\S+\s+)*?--from=(?P<stage>\S+)", re.IGNORECASE | re.MULTILINE
)
_WEB_BUILD = re.compile(r"^RUN\b.*\bpnpm\b.*\brun build\b", re.MULTILINE)
_BUILD_PLATFORM = re.compile(r"--platform=\$\{?BUILDPLATFORM\}?")
_MATRIX_PLATFORMS = re.compile(r"^\$\{\{\s*matrix\.(?P<key>[\w-]+)\s*\}\}$")


def _stages(dockerfile: str) -> list[dict]:
    froms = list(_FROM_LINE.finditer(dockerfile))
    stages = []
    for index, match in enumerate(froms):
        end = froms[index + 1].start() if index + 1 < len(froms) else len(dockerfile)
        body = dockerfile[match.end() : end]
        stages.append(
            {
                "name": match.group("name") or match.group("base"),
                "deps": {match.group("base"), *_COPY_FROM.findall(body)},
                "builds_web": bool(_WEB_BUILD.search(body)),
                "on_build_platform": bool(_BUILD_PLATFORM.search(match.group("flags"))),
            }
        )
    return stages


def _emulated_web_build_stages(stages: list[dict], target: str) -> list[str]:
    by_name = {stage["name"]: stage for stage in stages}
    pending, seen, hits = [target], set(), []
    while pending:
        name = pending.pop()
        if name in seen or name not in by_name:
            continue
        seen.add(name)
        stage = by_name[name]
        if stage["builds_web"] and not stage["on_build_platform"]:
            hits.append(name)
        pending.extend(stage["deps"])
    return hits


def _uses(step: dict, action: str) -> bool:
    return str(step.get("uses", "")).startswith(action)


def _step_platforms(job: dict, inputs: dict, label: str) -> str:
    """A step's platforms: a literal, or one `${{ matrix.<key> }}` resolved from the job."""
    platforms = str(inputs.get("platforms", ""))
    if "${{" not in platforms:
        return platforms
    match = _MATRIX_PLATFORMS.fullmatch(platforms.strip())
    matrix = (job.get("strategy") or {}).get("matrix")
    if not match or not isinstance(matrix, dict) or match["key"] not in matrix:
        pytest.fail(f"{label}: cannot resolve platforms {platforms!r}; extend this guard")
    values = matrix[match["key"]]
    return ",".join(map(str, values if isinstance(values, list) else [values]))


def test_release_image_builds_do_not_run_web_build_under_emulation() -> None:
    workflow = yaml.safe_load(_WORKFLOW.read_text())
    stages_by_file: dict[str, list[dict]] = {}

    emulated = []
    for job_name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        if not any(_uses(step, "docker/setup-qemu-action") for step in steps):
            continue
        for step in steps:
            if not _uses(step, "docker/build-push-action"):
                continue
            label = f"{job_name} / {step.get('name', step['uses'])}"
            inputs = step.get("with", {})
            if "linux/arm64" not in _step_platforms(job, inputs, label):
                continue
            dockerfile = str(inputs.get("file", "Dockerfile"))
            if dockerfile not in stages_by_file:
                stages_by_file[dockerfile] = _stages((_ROOT / dockerfile).read_text())
            stages = stages_by_file[dockerfile]
            target = inputs.get("target", stages[-1]["name"])
            for stage in _emulated_web_build_stages(stages, target):
                emulated.append(f"{label} -> stage {stage}")

    assert not emulated, (
        "These image builds run the web SPA build (`pnpm ... run build`) under QEMU arm64 "
        "emulation, where it has hung past the job timeout and left release tags "
        "unpublished. Pin the stage to --platform=$BUILDPLATFORM or build linux/arm64 "
        "natively:\n  " + "\n  ".join(emulated)
    )


@pytest.mark.parametrize(
    "dockerfile", [_DOCKERFILE, _ROOT / "deploy/docker/Dockerfile.ubi"], ids=["slim", "ubi"]
)
def test_web_builder_stage_is_pinned_to_the_build_platform(dockerfile: Path) -> None:
    stages = {stage["name"]: stage for stage in _stages(dockerfile.read_text())}
    assert stages["web-builder"]["builds_web"]
    assert stages["web-builder"]["on_build_platform"], (
        f"{dockerfile.name}: web-builder runs the SPA build on the target platform; pin it "
        "to --platform=$BUILDPLATFORM so a cross-build never emulates it"
    )


@pytest.mark.parametrize(
    ("platforms", "strategy", "expected"),
    [
        ("linux/amd64,linux/arm64", None, "linux/amd64,linux/arm64"),
        (
            "${{ matrix.platform }}",
            {"matrix": {"platform": ["linux/amd64", "linux/arm64"]}},
            "linux/amd64,linux/arm64",
        ),
    ],
)
def test_step_platforms_resolves_literals_and_matrix_keys(
    platforms: str, strategy: dict | None, expected: str
) -> None:
    job = {"strategy": strategy} if strategy else {}
    assert _step_platforms(job, {"platforms": platforms}, "job / step") == expected


@pytest.mark.parametrize(
    ("platforms", "strategy"),
    [
        ("${{ inputs.platforms }}", {"matrix": {"platform": ["linux/arm64"]}}),
        ("${{ matrix.platform }}", {"matrix": {"other": ["linux/arm64"]}}),
        ("${{ matrix.platform }}", None),
    ],
)
def test_step_platforms_rejects_unresolvable_expressions(
    platforms: str, strategy: dict | None
) -> None:
    job = {"strategy": strategy} if strategy else {}
    with pytest.raises(pytest.fail.Exception, match="cannot resolve platforms"):
        _step_platforms(job, {"platforms": platforms}, "job / step")
