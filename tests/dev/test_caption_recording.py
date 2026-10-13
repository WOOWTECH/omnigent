"""Caption validation and a real encode/decode check of timing and placement."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dev.caption_recording import caption_recording, probe, run, validate_cues


@pytest.mark.parametrize(
    "cues",
    [
        [],
        {},
        ["caption"],
        [{"start": True, "end": 1, "text": "Open"}],
        [{"start": float("nan"), "end": 1, "text": "Open"}],
        [{"start": 0, "end": float("inf"), "text": "Open"}],
        [{"start": -1, "end": 1, "text": "Open"}],
        [{"start": 1, "end": 1, "text": "Open"}],
        [{"start": 0, "end": 5, "text": "Open"}],
        [{"start": 0, "end": 1, "text": "  "}],
        [{"start": 0, "end": 1, "text": "bad\0text"}],
        [{"start": 0, "end": 1, "text": "word " * 100}],
        [
            {"start": 0, "end": 2, "text": "Open"},
            {"start": 1, "end": 3, "text": "Close"},
        ],
    ],
)
def test_invalid_cues_are_rejected(cues):
    with pytest.raises(ValueError):
        validate_cues(cues, duration=4, columns=40)


def test_cues_support_adjacent_intervals_and_literal_text():
    text = "Select 'a:b', then %{n} \\ {x}; → done"
    cues = validate_cues(
        [{"start": 0, "end": 2, "text": text}, {"start": 2, "end": 4, "text": "Done"}],
        duration=4,
        columns=80,
    )
    assert cues[0]["text"] == text
    assert cues[1]["start"] == 2


def test_existing_files_cannot_be_overwritten(tmp_path):
    source = tmp_path / "raw.mp4"
    source.write_bytes(b"original")
    output = tmp_path / "after.mp4"
    output.write_bytes(b"previous render")
    cues = tmp_path / "cues.json"
    for destination in (source, output, cues):
        with pytest.raises(ValueError, match="never overwritten"):
            caption_recording(source, cues, destination)
    assert source.read_bytes() == b"original"
    assert output.read_bytes() == b"previous render"


def _run(command):
    try:
        return subprocess.run(command, check=True, capture_output=True, timeout=60).stdout
    except subprocess.CalledProcessError as exc:
        raise AssertionError(
            f"{command[0]} failed: {exc.stderr.decode(errors='replace')}"
        ) from exc


def _frame(path: Path, seconds: float, crop: str) -> bytes:
    frame = _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(seconds),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            crop,
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    assert frame, f"No frame decoded at {seconds}s from {path}"
    return frame


def test_render_preserves_footage_audio_and_timing_with_captions_below(tmp_path):
    if not all(shutil.which(tool) for tool in ("ffmpeg", "ffprobe")):
        pytest.skip("Install ffmpeg and ffprobe for the real media check")
    filters = _run(["ffmpeg", "-hide_banner", "-filters"]).decode()
    if "drawtext" not in filters:
        pytest.skip("ffmpeg needs drawtext for the real media check")
    font_probe = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=64x64:d=0.1",
            "-vf",
            "drawtext=font='DejaVu Sans Mono':text=Test",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if font_probe.returncode:
        pytest.skip(f"ffmpeg needs font lookup and an installed font: {font_probe.stderr}")
    # Special characters in both paths and text must never become filter syntax.
    folder = tmp_path / "a:b's [recording]"
    folder.mkdir()
    source = folder / "raw.mp4"
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=640x360:r=10:d=4",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=4",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(source),
        ]
    )
    original = source.read_bytes()
    cues = folder / "cues.json"
    cues.write_text(
        json.dumps(
            [
                {"start": 0.5, "end": 1.5, "text": "Open 'a:b' %{n} \\ {x}; →"},
                {"start": 2, "end": 4, "text": "The result remains visible."},
            ]
        )
    )
    output = folder / "after-captioned.mp4"
    result = caption_recording(source, cues, output)
    assert source.read_bytes() == original
    assert result["width"] == 640
    assert result["height"] > 360
    assert result["duration"] == pytest.approx(probe(source)["duration"], abs=0.25)
    info = json.loads(
        _run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)])
    )
    assert {s["codec_name"] for s in info["streams"]} == {"h264", "aac"}
    # Product pixels stay above the band, including the outcome at the end.
    for seconds in (0.2, 1, 1.7, 2.5, 3.8):
        product = _frame(output, seconds, "crop=640:360:0:0")
        assert min(product) > 240
    band = f"crop=640:{result['height'] - 360}:0:360"
    for seconds in (0.2, 1.7):
        assert max(_frame(output, seconds, band)) < 50
    for seconds in (1, 2.5, 3.8):
        assert max(_frame(output, seconds, band)) > 200
    _run(["ffmpeg", "-v", "error", "-i", str(output), "-f", "null", "-"])


def test_failed_render_leaves_raw_footage_and_no_partial_output(tmp_path, monkeypatch):
    source = tmp_path / "raw.webm"
    source.write_bytes(b"source")
    cues = tmp_path / "cues.json"
    cues.write_text('[{"start": 0, "end": 2, "text": "Open"}]')
    monkeypatch.setattr(
        "dev.caption_recording.probe", lambda _: {"width": 640, "height": 360, "duration": 3}
    )

    def fail(*args, **kwargs):
        assert kwargs["timeout"] == 900
        raise RuntimeError("drawtext unavailable")

    monkeypatch.setattr("dev.caption_recording.run", fail)
    output = tmp_path / "after.mp4"
    with pytest.raises(RuntimeError, match="drawtext unavailable"):
        caption_recording(source, cues, output, render_timeout=900)
    assert source.read_bytes() == b"source"
    assert not output.exists()
    assert not list(tmp_path.glob(".caption-*"))


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_unbounded_or_invalid_render_timeout_is_rejected(tmp_path, timeout):
    with pytest.raises(ValueError, match="positive finite"):
        caption_recording(
            tmp_path / "raw.webm",
            tmp_path / "cues.json",
            tmp_path / "out.mp4",
            render_timeout=timeout,
        )


@pytest.mark.parametrize(
    "stream", [None, {}, {"width": 640}, {"height": 360}, {"width": 0, "height": 0}]
)
def test_unprobeable_video_reports_a_captioning_error(tmp_path, monkeypatch, stream):
    info = {} if stream is None else {"streams": [{"codec_type": "video", **stream}]}
    monkeypatch.setattr("dev.caption_recording.run", lambda *a, **kw: json.dumps(info))
    with pytest.raises(ValueError, match="Source"):
        probe(tmp_path / "broken.mp4")


def test_media_command_timeout_is_reported(monkeypatch):
    def stalled(command, **kwargs):
        assert kwargs["timeout"] == 0.01
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr("dev.caption_recording.subprocess.run", stalled)
    with pytest.raises(RuntimeError, match=r"ffmpeg timed out after 0\.01s"):
        run(["ffmpeg"], timeout=0.01)


def test_concurrent_output_is_never_overwritten(tmp_path, monkeypatch):
    source = tmp_path / "raw.webm"
    source.write_bytes(b"source")
    cues = tmp_path / "cues.json"
    cues.write_text('[{"start": 0, "end": 2, "text": "Open"}]')
    output = tmp_path / "after.mp4"
    monkeypatch.setattr(
        "dev.caption_recording.probe", lambda _: {"width": 640, "height": 360, "duration": 3}
    )

    def concurrent_render(command, **kwargs):
        Path(command[-1]).write_bytes(b"our render")
        output.write_bytes(b"another render")

    monkeypatch.setattr("dev.caption_recording.run", concurrent_render)
    with pytest.raises(FileExistsError):
        caption_recording(source, cues, output)
    assert output.read_bytes() == b"another render"
    assert source.read_bytes() == b"source"
    assert not list(tmp_path.glob(".caption-*"))
