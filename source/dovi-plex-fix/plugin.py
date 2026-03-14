#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dolby Vision Fix Plugin for Unmanic

Converts Dolby Vision (Profile 5) HEVC to Plex-compatible Main10 HEVC.
- Detects DV streams via ffprobe JSON
- Re-encodes with NVENC when DV detected (or CPU libx265 if fallback enabled)
- Strips DV metadata (dovi_rpu=strip) when FFmpeg supports it
- Remuxes non-DV files (copy streams)
- Preserves audio and subtitle tracks
"""
import json
import re
import subprocess

from unmanic.libs.unplugins.settings import PluginSettings


class Settings(PluginSettings):
    """Plugin settings for Unmanic WebUI."""
    settings = {
        "Use NVENC": True,
        "Enable CPU fallback": False,
        "NVENC preset": "p5",
        "Enable DV metadata removal": True,
    }
    form_settings = {
        "Use NVENC": {
            "input_type": "checkbox",
            "label": "Use NVENC GPU encoding for DV files (recommended)",
        },
        "Enable CPU fallback": {
            "input_type": "checkbox",
            "label": "Use CPU (libx265) when NVENC unavailable (slow for 4K)",
        },
        "NVENC preset": {
            "input_type": "select",
            "label": "NVENC preset (p1=fast, p7=quality)",
            "options": [
                ("p1", "p1 - Fastest"),
                ("p2", "p2"),
                ("p3", "p3"),
                ("p4", "p4"),
                ("p5", "p5 - Balanced (default)"),
                ("p6", "p6"),
                ("p7", "p7 - Best quality"),
            ],
        },
        "Enable DV metadata removal": {
            "input_type": "checkbox",
            "label": "Strip Dolby Vision metadata when FFmpeg supports it",
        },
    }


def _run_ffprobe_json(filepath):
    """Run ffprobe and return parsed JSON, or None on failure."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_streams", "-show_format", "-show_data",
            "-print_format", "json",
            filepath
        ]
        output = subprocess.check_output(cmd, text=True, timeout=30)
        return json.loads(output)
    except Exception:
        return None


def _is_dolby_vision_profile5(stream):
    """Return True if stream is HEVC with Dolby Vision Profile 5."""
    if stream.get("codec_type") != "video":
        return False
    if stream.get("codec_name") != "hevc":
        return False

    tags = stream.get("tags") or {}
    codec_tag = (tags.get("codec_tag_string") or stream.get("codec_tag_string") or "").lower()
    if codec_tag in ("dvhe", "dvh1"):
        return True

    for side in stream.get("side_data_list") or []:
        side_type = side.get("side_data_type", "")
        if "dovi" in side_type.lower() or "dolby" in side_type.lower():
            profile = side.get("dolby_vision_profile") or side.get("profile")
            if profile is not None:
                return int(profile) == 5
            return True

    ct = (stream.get("color_transfer") or "").lower()
    if "dv" in ct or ct == "dvhe":
        return True

    return False


def _scan_dovi(filepath):
    """Return True if file has Dolby Vision Profile 5 on any video stream."""
    probe = _run_ffprobe_json(filepath)
    if not probe:
        return False
    for stream in probe.get("streams") or []:
        if _is_dolby_vision_profile5(stream):
            return True
    return False


def _check_ffmpeg_dovi_rpu():
    """Return True if FFmpeg supports dovi_rpu=strip for DV metadata removal. Never raises."""
    try:
        out = subprocess.check_output(["ffmpeg", "-bsfs"], text=True, timeout=5, stderr=subprocess.STDOUT)
        return "dovi_rpu" in out
    except Exception:
        return False


def _check_nvenc_available():
    """Return True if hevc_nvenc encoder is available. Never raises."""
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            text=True, timeout=5, stderr=subprocess.STDOUT
        )
        for line in out.splitlines():
            if "hevc_nvenc" in line and line.strip().startswith("V"):
                return True
    except Exception:
        pass
    return False


def _check_hevc_cuvid_available():
    """Return True if hevc_cuvid decoder (NVDEC) is available for GPU decoding. Never raises."""
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-decoders"],
            text=True, timeout=5, stderr=subprocess.STDOUT
        )
        for line in out.splitlines():
            if "hevc_cuvid" in line and line.strip().startswith("V"):
                return True
    except Exception:
        pass
    return False


def _check_libx265_available():
    """Return True if libx265 encoder is available for CPU encoding. Never raises."""
    try:
        out = subprocess.check_output(
            ["ffmpeg", "-hide_banner", "-encoders"],
            text=True, timeout=5, stderr=subprocess.STDOUT
        )
        for line in out.splitlines():
            if "hevc" in line and "libx265" in line and line.strip().startswith("V"):
                return True
    except Exception:
        pass
    return False


def _parse_time_to_seconds(time_str):
    """Convert HH:MM:SS.ms to seconds."""
    match = re.match(r"(\d+):(\d+):(\d+)\.?(\d*)", (time_str or "").strip())
    if not match:
        return None
    h, m, s, ms = match.groups()
    return int(h) * 3600 + int(m) * 60 + int(s) + (int(ms or 0) / 100)


def _make_ffmpeg_progress_parser(default_parser):
    """Return a progress parser that extracts percent from FFmpeg stderr output."""
    duration_seconds = [None]  # mutable container for closure

    def parser(line_text):
        line = str(line_text or "").strip()
        if not line:
            return default_parser(line_text)

        # Capture Duration: HH:MM:SS.ms from initial output
        dur_match = re.search(r"Duration:\s*(\d+:\d+:\d+\.?\d*)", line)
        if dur_match:
            duration_seconds[0] = _parse_time_to_seconds(dur_match.group(1))

        # Capture time=HH:MM:SS.ms from progress lines
        time_match = re.search(r"time=\s*(\d+:\d+:\d+\.?\d*)", line)
        if time_match and duration_seconds[0]:
            current = _parse_time_to_seconds(time_match.group(1))
            if current is not None and duration_seconds[0] > 0:
                percent = min(100, int(100 * current / duration_seconds[0]))
                return {"percent": str(percent), "killed": False, "paused": False}

        return default_parser(line_text)

    return parser


def on_library_management_file_test(data, **kwargs):
    """
    Runner - file test during library scan.
    Queues HEVC video files for processing. Uses shared_info to cache ffprobe for worker.
    Accepts **kwargs to avoid conflict when Unmanic passes task_data_store/file_metadata.
    """
    path = data.get("path", "")
    if not path:
        return data

    shared = data.get("shared_info") or {}
    if "dovi_fix_probe" in shared:
        data["add_file_to_pending_tasks"] = True
        return data

    probe = _run_ffprobe_json(path)
    if probe:
        streams = probe.get("streams") or []
        has_hevc_video = any(
            s.get("codec_type") == "video" and s.get("codec_name") == "hevc"
            for s in streams
        )
        if has_hevc_video:
            data["add_file_to_pending_tasks"] = True

        if "shared_info" not in data:
            data["shared_info"] = {}
        data["shared_info"]["dovi_fix_probe"] = probe
        data["shared_info"]["dovi_fix_is_dv"] = any(
            _is_dolby_vision_profile5(s) for s in streams
        )

    return data


def on_worker_process(data):
    """
    Runner - build custom FFmpeg command for DV fix or remux.
    Replaces exec_command with our command. Preserves file_in, file_out from data.
    """
    file_in = data.get("file_in", "")
    file_out = data.get("file_out", "")
    worker_log = data.get("worker_log") or []

    def log(msg):
        worker_log.append(f"[Dolby Vision Fix] {msg}")

    if not file_in or not file_out:
        log("Missing file_in or file_out")
        return data

    settings = Settings(library_id=data.get("library_id"))
    use_nvenc = settings.get_setting("Use NVENC") and _check_nvenc_available()
    use_cpu_fallback = settings.get_setting("Enable CPU fallback")
    nvenc_preset = settings.get_setting("NVENC preset") or "p5"
    if nvenc_preset not in ("p1", "p2", "p3", "p4", "p5", "p6", "p7"):
        nvenc_preset = "p5"
    use_dovi_rpu = settings.get_setting("Enable DV metadata removal") and _check_ffmpeg_dovi_rpu()

    # Use cached probe from file test if available
    shared = data.get("shared_info") or {}
    if "dovi_fix_is_dv" in shared:
        is_dovi = shared["dovi_fix_is_dv"]
    else:
        is_dovi = _scan_dovi(file_in)

    if is_dovi:
        log("DV Profile 5 detected")
        use_cpu = use_cpu_fallback and _check_libx265_available()

        if not use_nvenc and not use_cpu:
            log("NVENC unavailable and CPU fallback disabled - skipping DV file")
            data["exec_command"] = ["sh", "-c", "echo 'NVENC required (or enable CPU fallback)' && exit 1"]
            return data

        if use_nvenc:
            use_gpu_decode = _check_hevc_cuvid_available()
            if use_gpu_decode:
                log("Using NVDEC (hevc_cuvid) for GPU decoding")

            log("Branch: DV transcode (NVENC Main10)")
            input_opts = ["-hide_banner", "-loglevel", "info"]
            if use_gpu_decode:
                input_opts.extend(["-hwaccel", "cuda", "-c:v", "hevc_cuvid"])
            elif use_dovi_rpu:
                input_opts.extend(["-bsf:v", "dovi_rpu=strip=1"])
            else:
                log("FFmpeg dovi_rpu not available - proceeding without DV metadata removal")
            input_opts.extend(["-i", file_in])

            video_args = [
                "-map", "0:v:0",
                "-map", "0:a",
                "-map", "0:s",
                "-c:v:0", "hevc_nvenc",
                "-preset", nvenc_preset,
                "-profile:v", "main10",
                "-pix_fmt", "p010le",
                "-tag:v", "hvc1",
                "-c:a", "copy",
                "-c:s", "copy",
            ]
        else:
            log("Branch: DV transcode (CPU libx265 Main10)")
            input_opts = ["-hide_banner", "-loglevel", "info"]
            if use_dovi_rpu:
                input_opts.extend(["-bsf:v", "dovi_rpu=strip=1"])
            else:
                log("FFmpeg dovi_rpu not available - proceeding without DV metadata removal")
            input_opts.extend(["-i", file_in])

            video_args = [
                "-map", "0:v:0",
                "-map", "0:a",
                "-map", "0:s",
                "-c:v:0", "libx265",
                "-profile:v", "main10",
                "-pix_fmt", "yuv420p10le",
                "-crf", "20",
                "-tag:v", "hvc1",
                "-c:a", "copy",
                "-c:s", "copy",
            ]
    else:
        log("Branch: non-DV remux (copy streams)")
        input_opts = ["-hide_banner", "-loglevel", "info", "-i", file_in]
        video_args = [
            "-map", "0:v:0",
            "-map", "0:a",
            "-map", "0:s",
            "-c:v:0", "copy",
            "-c:a", "copy",
            "-c:s", "copy",
        ]

    cmd = ["ffmpeg"] + input_opts + ["-max_muxing_queue_size", "2048"] + video_args + [file_out]

    log(f"FFmpeg: {' '.join(cmd)}")
    data["exec_command"] = cmd

    # Custom progress parser so the web UI shows FFmpeg encoding percentage
    default_parser = data.get("command_progress_parser")
    if default_parser:
        data["command_progress_parser"] = _make_ffmpeg_progress_parser(default_parser)

    return data
