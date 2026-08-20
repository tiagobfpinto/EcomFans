from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import threading
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


ANALYSIS_SAMPLE_RATE = 16_000
ANALYSIS_WINDOW_MS = 10
ANALYSIS_WINDOW_SAMPLES = ANALYSIS_SAMPLE_RATE * ANALYSIS_WINDOW_MS // 1000

PRESET_DEFAULTS = {
    "natural": {
        "sensitivity": "auto",
        "min_pause_ms": 240,
        "within_sentence_gap_ms": 120,
        "sentence_gap_ms": 240,
        "overlap_ms": 10,
        "transition_smoothness": 80,
        "pre_speech_ms": 35,
        "post_speech_ms": 65,
        "protect_word_endings": True,
        "breath_handling": "keep",
        "head_silence_ms": 80,
        "tail_silence_ms": 150,
        "preserve_intentional_pauses": True,
        "preserve_original_quality": True,
    },
    "dynamic": {
        "sensitivity": "auto",
        "min_pause_ms": 160,
        "within_sentence_gap_ms": 75,
        "sentence_gap_ms": 145,
        "overlap_ms": 40,
        "transition_smoothness": 75,
        "pre_speech_ms": 25,
        "post_speech_ms": 45,
        "protect_word_endings": True,
        "breath_handling": "shorten",
        "head_silence_ms": 60,
        "tail_silence_ms": 100,
        "preserve_intentional_pauses": True,
        "preserve_original_quality": True,
    },
    "aggressive": {
        "sensitivity": "auto",
        "min_pause_ms": 110,
        "within_sentence_gap_ms": 45,
        "sentence_gap_ms": 95,
        "overlap_ms": 40,
        "transition_smoothness": 65,
        "pre_speech_ms": 20,
        "post_speech_ms": 35,
        "protect_word_endings": True,
        "breath_handling": "shorten",
        "head_silence_ms": 40,
        "tail_silence_ms": 70,
        "preserve_intentional_pauses": True,
        "preserve_original_quality": True,
    },
}

_INTEGER_RANGES = {
    "min_pause_ms": (80, 2000),
    "within_sentence_gap_ms": (0, 500),
    "sentence_gap_ms": (0, 1000),
    "overlap_ms": (0, 80),
    "transition_smoothness": (0, 100),
    "pre_speech_ms": (0, 150),
    "post_speech_ms": (0, 200),
    "head_silence_ms": (0, 1000),
    "tail_silence_ms": (0, 1000),
}
_BOOLEAN_FIELDS = {
    "protect_word_endings",
    "preserve_intentional_pauses",
    "preserve_original_quality",
}


class VoiceoverProcessingError(RuntimeError):
    pass


@dataclass(frozen=True)
class AudioProbe:
    duration_ms: int
    sample_rate: int
    channels: int
    codec: str


@dataclass(frozen=True)
class SpeechAnalysis:
    levels_db: list[float]
    speech_segments: list[tuple[int, int]]
    noise_floor_db: float
    speech_level_db: float
    start_threshold_db: float
    stop_threshold_db: float
    warnings: list[str]


@dataclass(frozen=True)
class EditPlan:
    intervals_ms: list[tuple[int, int]]
    overlaps_ms: list[int]
    pauses_shortened: int
    overlaps_applied: int


def resolve_ffmpeg_executable() -> str:
    configured = (os.getenv("FFMPEG_BINARY") or "").strip()
    if configured:
        if os.path.isfile(configured):
            return os.path.abspath(configured)
        discovered = shutil.which(configured)
        if discovered:
            return discovered
        raise VoiceoverProcessingError(
            "FFMPEG_BINARY does not point to an available executable."
        )
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except (ImportError, RuntimeError, OSError) as exc:
        discovered = shutil.which("ffmpeg")
        if discovered:
            return discovered
        raise VoiceoverProcessingError(
            "Voiceover processing requires FFmpeg. Install the application "
            "requirements or configure FFMPEG_BINARY."
        ) from exc


def normalize_settings(preset: str, provided: dict | None = None) -> tuple[str, dict]:
    normalized_preset = (preset or "dynamic").strip().lower()
    if normalized_preset not in PRESET_DEFAULTS:
        raise ValueError("Preset must be natural, dynamic, or aggressive.")
    settings = dict(PRESET_DEFAULTS[normalized_preset])
    provided = provided or {}
    if not isinstance(provided, dict):
        raise ValueError("Settings must be a JSON object.")

    unknown = set(provided) - set(settings)
    if unknown:
        raise ValueError(f"Unknown setting: {sorted(unknown)[0]}.")
    for key, value in provided.items():
        if key in _INTEGER_RANGES:
            if isinstance(value, bool):
                raise ValueError(f"{key} must be an integer.")
            try:
                number = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an integer.") from exc
            low, high = _INTEGER_RANGES[key]
            if number < low or number > high:
                unit = "%" if key == "transition_smoothness" else "ms"
                raise ValueError(f"{key} must be between {low} and {high} {unit}.")
            settings[key] = number
        elif key in _BOOLEAN_FIELDS:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false.")
            settings[key] = value
        elif key == "sensitivity":
            normalized = str(value).strip().lower()
            if normalized not in {"auto", "low", "medium", "high"}:
                raise ValueError("Sensitivity must be auto, low, medium, or high.")
            settings[key] = normalized
        elif key == "breath_handling":
            normalized = str(value).strip().lower()
            if normalized not in {"keep", "shorten", "remove"}:
                raise ValueError("Breath handling must be keep, shorten, or remove.")
            settings[key] = normalized

    if settings["sentence_gap_ms"] < settings["within_sentence_gap_ms"]:
        raise ValueError(
            "Sentence gap must be greater than or equal to the within-sentence gap."
        )
    return normalized_preset, settings


def probe_mp3(file_path: str, *, timeout_seconds: int = 30) -> AudioProbe:
    command = [
        resolve_ffmpeg_executable(),
        "-hide_banner",
        "-i",
        file_path,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(5, int(timeout_seconds)),
        )
    except subprocess.TimeoutExpired as exc:
        raise VoiceoverProcessingError("Reading the MP3 timed out.") from exc
    except OSError as exc:
        raise VoiceoverProcessingError("FFmpeg could not read this MP3.") from exc

    output = f"{completed.stdout}\n{completed.stderr}"
    duration_match = re.search(
        r"Duration:\s*(\d{1,3}):(\d{2}):(\d{2}(?:\.\d+)?)", output
    )
    audio_match = re.search(
        r"Audio:\s*([^,]+),\s*(\d+)\s*Hz,\s*([^,\r\n]+)", output
    )
    if not duration_match or not audio_match:
        raise VoiceoverProcessingError(
            "This file is damaged, incompatible, or does not contain readable MP3 audio."
        )
    codec = audio_match.group(1).strip().lower()
    if not codec.startswith("mp3"):
        raise VoiceoverProcessingError("Only genuine MP3 audio is supported.")
    hours, minutes, seconds = duration_match.groups()
    duration_ms = round(
        (int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000
    )
    channel_label = audio_match.group(3).strip().lower()
    if channel_label.startswith("mono"):
        channels = 1
    elif channel_label.startswith("stereo"):
        channels = 2
    else:
        raise VoiceoverProcessingError(
            "Only mono or stereo MP3 voiceovers are supported."
        )
    if duration_ms <= 0:
        raise VoiceoverProcessingError("The MP3 is empty.")
    return AudioProbe(
        duration_ms=duration_ms,
        sample_rate=int(audio_match.group(2)),
        channels=channels,
        codec=codec,
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return -96.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _smooth_levels(levels: list[float]) -> list[float]:
    if len(levels) < 3:
        return list(levels)
    smoothed = []
    for index in range(len(levels)):
        start = max(0, index - 1)
        end = min(len(levels), index + 2)
        linear = [10 ** (level / 20.0) for level in levels[start:end]]
        smoothed.append(20.0 * math.log10(max(1e-6, sum(linear) / len(linear))))
    return smoothed


def _read_analysis_levels(file_path: str, timeout_seconds: int) -> list[float]:
    command = [
        resolve_ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        file_path,
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(ANALYSIS_SAMPLE_RATE),
        "-f",
        "s16le",
        "pipe:1",
    ]
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdout is None or process.stderr is None:
        process.kill()
        raise VoiceoverProcessingError("Could not start MP3 analysis.")
    levels: list[float] = []
    frame_bytes = ANALYSIS_WINDOW_SAMPLES * 2
    carry = b""
    timed_out = threading.Event()

    def kill_on_timeout():
        timed_out.set()
        process.kill()

    watchdog = threading.Timer(max(5, int(timeout_seconds)), kill_on_timeout)
    watchdog.daemon = True
    watchdog.start()
    try:
        while True:
            chunk = process.stdout.read(64 * 1024)
            if not chunk:
                break
            data = carry + chunk
            complete = len(data) - (len(data) % frame_bytes)
            for offset in range(0, complete, frame_bytes):
                samples = array("h")
                samples.frombytes(data[offset : offset + frame_bytes])
                if sys.byteorder != "little":
                    samples.byteswap()
                mean_square = sum(sample * sample for sample in samples) / max(1, len(samples))
                rms = math.sqrt(mean_square)
                levels.append(20.0 * math.log10(max(1.0, rms) / 32768.0))
            carry = data[complete:]
        stderr = process.stderr.read().decode("utf-8", errors="replace")
        return_code = process.wait()
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
    if timed_out.is_set():
        raise VoiceoverProcessingError("Voiceover analysis timed out.")
    if return_code != 0 or not levels:
        detail = stderr.strip().splitlines()
        suffix = f": {detail[-1][:300]}" if detail else ""
        raise VoiceoverProcessingError(f"Could not decode the MP3{suffix}")
    return levels


def _segments_from_mask(
    levels: list[float], start_threshold: float, stop_threshold: float
) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    active_start: int | None = None
    quiet_run = 0
    release_frames = 6
    for index, level in enumerate(levels):
        if active_start is None:
            if level >= start_threshold:
                active_start = index
                quiet_run = 0
            continue
        if level < stop_threshold:
            quiet_run += 1
            if quiet_run >= release_frames:
                end = index - release_frames + 1
                segments.append((active_start, max(active_start + 1, end)))
                active_start = None
                quiet_run = 0
        else:
            quiet_run = 0
    if active_start is not None:
        segments.append((active_start, len(levels)))

    merged: list[tuple[int, int]] = []
    for start, end in segments:
        if merged and start - merged[-1][1] <= 8:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [
        (start, end)
        for start, end in merged
        if end - start >= 5
        or max(levels[start:end], default=-96.0) >= start_threshold + 6.0
    ]


def analyze_voiceover(
    file_path: str, settings: dict, *, timeout_seconds: int = 1800
) -> SpeechAnalysis:
    raw_levels = _read_analysis_levels(file_path, timeout_seconds)
    levels = _smooth_levels(raw_levels)
    noise_floor = _percentile(levels, 0.20)
    speech_level = _percentile(levels, 0.85)
    contrast = speech_level - noise_floor
    base_threshold = noise_floor + max(4.0, contrast * 0.38)
    sensitivity_offset = {
        "low": 3.0,
        "medium": 0.0,
        "high": -3.0,
        "auto": 0.0,
    }[settings["sensitivity"]]
    start_threshold = min(speech_level - 1.0, base_threshold + sensitivity_offset + 1.5)
    stop_threshold = start_threshold - 3.0
    segments = _segments_from_mask(levels, start_threshold, stop_threshold)
    speech_frames = sum(end - start for start, end in segments)
    if contrast < 4.0 or speech_frames * ANALYSIS_WINDOW_MS < 400:
        raise VoiceoverProcessingError(
            "No clear speech was found. Try a cleaner voiceover recording."
        )
    warnings: list[str] = []
    occupancy = speech_frames / max(1, len(levels))
    if contrast < 12.0 or (occupancy > 0.92 and noise_floor > -42.0):
        warnings.append("Background audio or music may reduce detection accuracy.")
    return SpeechAnalysis(
        levels_db=levels,
        speech_segments=segments,
        noise_floor_db=noise_floor,
        speech_level_db=speech_level,
        start_threshold_db=start_threshold,
        stop_threshold_db=stop_threshold,
        warnings=warnings,
    )


def _quartiles(values: list[int]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    return _percentile([float(value) for value in values], 0.25), _percentile(
        [float(value) for value in values], 0.75
    )


def _gap_split(values: list[int], fallback: int) -> float:
    if len(values) < 2:
        return float(fallback)
    low = float(min(values))
    high = float(max(values))
    if low == high:
        return float(fallback)
    for _ in range(12):
        low_group = [value for value in values if abs(value - low) <= abs(value - high)]
        high_group = [value for value in values if abs(value - low) > abs(value - high)]
        if not low_group or not high_group:
            break
        next_low = sum(low_group) / len(low_group)
        next_high = sum(high_group) / len(high_group)
        if abs(next_low - low) + abs(next_high - high) < 1:
            low, high = next_low, next_high
            break
        low, high = next_low, next_high
    return max(float(fallback), (low + high) / 2.0)


def _breath_region(
    analysis: SpeechAnalysis, start_ms: int, end_ms: int
) -> tuple[int, int] | None:
    start_frame = max(0, start_ms // ANALYSIS_WINDOW_MS)
    end_frame = min(len(analysis.levels_db), math.ceil(end_ms / ANALYSIS_WINDOW_MS))
    candidate_threshold = analysis.noise_floor_db + max(
        3.0, (analysis.start_threshold_db - analysis.noise_floor_db) * 0.45
    )
    runs: list[tuple[int, int, float]] = []
    run_start: int | None = None
    for index in range(start_frame, end_frame):
        level = analysis.levels_db[index]
        is_breath_energy = candidate_threshold <= level < analysis.start_threshold_db
        if is_breath_energy and run_start is None:
            run_start = index
        elif not is_breath_energy and run_start is not None:
            if 3 <= index - run_start <= 50:
                peak = max(analysis.levels_db[run_start:index])
                runs.append((run_start, index, peak))
            run_start = None
    if run_start is not None and 3 <= end_frame - run_start <= 50:
        runs.append(
            (run_start, end_frame, max(analysis.levels_db[run_start:end_frame]))
        )
    if not runs:
        return None
    best = max(runs, key=lambda item: (item[2], item[1] - item[0]))
    return best[0] * ANALYSIS_WINDOW_MS, best[1] * ANALYSIS_WINDOW_MS


def _safe_overlap_ms(
    analysis: SpeechAnalysis,
    left_end_ms: int,
    right_start_ms: int,
    requested_ms: int,
    aggressive: bool,
) -> int:
    if requested_ms <= 0:
        return 0
    requested_frames = max(0, requested_ms // ANALYSIS_WINDOW_MS)
    left_frame = min(len(analysis.levels_db), left_end_ms // ANALYSIS_WINDOW_MS)
    right_frame = min(len(analysis.levels_db), right_start_ms // ANALYSIS_WINDOW_MS)
    left_safe = 0
    for index in range(left_frame - 1, max(-1, left_frame - requested_frames - 1), -1):
        if analysis.levels_db[index] >= analysis.stop_threshold_db:
            break
        left_safe += 1
    right_safe = 0
    for index in range(right_frame, min(len(analysis.levels_db), right_frame + requested_frames)):
        if analysis.levels_db[index] >= analysis.stop_threshold_db:
            break
        right_safe += 1
    safe_frames = min(requested_frames, left_safe, right_safe)
    if aggressive and safe_frames < requested_frames:
        extra_limit = min(requested_frames, 4)
        left_peak = max(
            analysis.levels_db[max(0, left_frame - extra_limit):left_frame],
            default=-96.0,
        )
        right_peak = max(
            analysis.levels_db[right_frame:min(len(analysis.levels_db), right_frame + extra_limit)],
            default=-96.0,
        )
        if not (
            left_peak >= analysis.start_threshold_db
            and right_peak >= analysis.start_threshold_db
        ):
            safe_frames = extra_limit
    return safe_frames * ANALYSIS_WINDOW_MS


def build_edit_plan(
    duration_ms: int, analysis: SpeechAnalysis, settings: dict, preset: str
) -> EditPlan:
    pre_frames = math.ceil(settings["pre_speech_ms"] / ANALYSIS_WINDOW_MS)
    post_frames = math.ceil(settings["post_speech_ms"] / ANALYSIS_WINDOW_MS)
    protected: list[tuple[int, int]] = []
    for start, end in analysis.speech_segments:
        protected_start = max(0, start - pre_frames)
        protected_end = min(len(analysis.levels_db), end + post_frames)
        if settings["protect_word_endings"]:
            while (
                protected_end < len(analysis.levels_db)
                and protected_end < end + post_frames + 8
                and analysis.levels_db[protected_end] >= analysis.stop_threshold_db
            ):
                protected_end += 1
        if protected and protected_start <= protected[-1][1]:
            protected[-1] = (protected[-1][0], max(protected[-1][1], protected_end))
        else:
            protected.append((protected_start, protected_end))
    if not protected:
        raise VoiceoverProcessingError("No clear speech was found.")

    head_ms = max(0, protected[0][0] * ANALYSIS_WINDOW_MS - settings["head_silence_ms"])
    tail_ms = min(
        duration_ms,
        protected[-1][1] * ANALYSIS_WINDOW_MS + settings["tail_silence_ms"],
    )
    gaps = []
    for index in range(len(protected) - 1):
        start_ms = protected[index][1] * ANALYSIS_WINDOW_MS
        end_ms = protected[index + 1][0] * ANALYSIS_WINDOW_MS
        if end_ms > start_ms:
            gaps.append((index, start_ms, end_ms, end_ms - start_ms))
    editable_durations = [
        duration for _, _, _, duration in gaps
        if duration >= settings["min_pause_ms"]
    ]
    q1, q3 = _quartiles(editable_durations)
    iqr = max(0.0, q3 - q1)
    intentional_threshold = max(1200.0, q3 + 1.5 * iqr)
    sentence_split = _gap_split(
        editable_durations,
        max(350, settings["sentence_gap_ms"] * 2),
    )

    cuts: list[tuple[int, int]] = []
    shortened = 0
    for _, gap_start, gap_end, gap_duration in gaps:
        if gap_duration < settings["min_pause_ms"]:
            continue
        intentional = gap_duration >= intentional_threshold
        sentence = gap_duration >= sentence_split
        if intentional and settings["preserve_intentional_pauses"]:
            target = max(
                settings["sentence_gap_ms"] * 2,
                min(800, round(gap_duration * 0.45)),
            )
        else:
            target = (
                settings["sentence_gap_ms"]
                if sentence or intentional
                else settings["within_sentence_gap_ms"]
            )
        retained = min(gap_duration, target + settings["overlap_ms"])
        if gap_duration <= retained:
            continue
        left_keep = retained // 2
        right_keep = retained - left_keep
        remove_start = gap_start + left_keep
        remove_end = gap_end - right_keep
        breath = _breath_region(analysis, gap_start, gap_end)
        breath_mode = settings["breath_handling"]
        gap_cuts: list[tuple[int, int]] = []
        if breath and breath_mode != "remove":
            breath_start, breath_end = breath
            if breath_mode == "shorten":
                center = (breath_start + breath_end) // 2
                kept_breath = min(80, max(30, (breath_end - breath_start) // 2))
                breath_start = center - kept_breath // 2
                breath_end = breath_start + kept_breath
            if remove_start < breath_start:
                gap_cuts.append((remove_start, breath_start))
            if breath_end < remove_end:
                gap_cuts.append((breath_end, remove_end))
        elif remove_end > remove_start:
            gap_cuts.append((remove_start, remove_end))
        valid_cuts = [(start, end) for start, end in gap_cuts if end - start >= 10]
        if valid_cuts:
            cuts.extend(valid_cuts)
            shortened += 1

    cuts.sort()
    intervals: list[tuple[int, int]] = []
    cursor = head_ms
    for cut_start, cut_end in cuts:
        cut_start = max(cursor, cut_start)
        cut_end = min(tail_ms, cut_end)
        if cut_end <= cut_start:
            continue
        if cut_start > cursor:
            intervals.append((cursor, cut_start))
        cursor = cut_end
    if cursor < tail_ms:
        intervals.append((cursor, tail_ms))
    intervals = [(start, end) for start, end in intervals if end > start]
    if not intervals:
        raise VoiceoverProcessingError("The edit would produce an empty result.")

    overlaps: list[int] = []
    for index in range(len(intervals) - 1):
        overlap = _safe_overlap_ms(
            analysis,
            intervals[index][1],
            intervals[index + 1][0],
            settings["overlap_ms"],
            preset == "aggressive",
        )
        max_interval_overlap = min(
            (intervals[index][1] - intervals[index][0]) // 2,
            (intervals[index + 1][1] - intervals[index + 1][0]) // 2,
        )
        overlaps.append(max(0, min(overlap, max_interval_overlap)))
    return EditPlan(
        intervals_ms=intervals,
        overlaps_ms=overlaps,
        pauses_shortened=shortened,
        overlaps_applied=sum(1 for value in overlaps if value > 0),
    )


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks = []
    remaining = max(0, size)
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _copy_exact(stream: BinaryIO, output: BinaryIO, size: int) -> int:
    remaining = max(0, size)
    copied = 0
    while remaining:
        chunk = stream.read(min(64 * 1024, remaining))
        if not chunk:
            break
        output.write(chunk)
        copied += len(chunk)
        remaining -= len(chunk)
    return copied


def _mix_crossfade(
    left: bytes, right: bytes, smoothness: int, channels: int
) -> bytes:
    sample_count = min(len(left), len(right)) // 2
    if sample_count <= 0:
        return b""
    left_samples = array("h")
    right_samples = array("h")
    left_samples.frombytes(left[: sample_count * 2])
    right_samples.frombytes(right[: sample_count * 2])
    if sys.byteorder != "little":
        left_samples.byteswap()
        right_samples.byteswap()
    blend = max(0.0, min(1.0, smoothness / 100.0))
    mixed = array("h")
    frame_count = sample_count // max(1, channels)
    denominator = max(1, frame_count - 1)
    for frame_index in range(frame_count):
        position = frame_index / denominator
        linear_left = 1.0 - position
        linear_right = position
        equal_left = math.cos(position * math.pi / 2.0)
        equal_right = math.sin(position * math.pi / 2.0)
        left_weight = linear_left * (1.0 - blend) + equal_left * blend
        right_weight = linear_right * (1.0 - blend) + equal_right * blend
        for channel_index in range(channels):
            index = frame_index * channels + channel_index
            value = round(
                left_samples[index] * left_weight
                + right_samples[index] * right_weight
            )
            mixed.append(max(-32768, min(32767, value)))
    if sys.byteorder != "little":
        mixed.byteswap()
    return mixed.tobytes()


def render_edit_plan(
    source_path: str,
    output_path: str,
    probe: AudioProbe,
    plan: EditPlan,
    settings: dict,
    *,
    timeout_seconds: int = 1800,
) -> None:
    ffmpeg = resolve_ffmpeg_executable()
    decoder_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        source_path,
        "-t",
        f"{plan.intervals_ms[-1][1] / 1000.0:.3f}",
        "-vn",
        "-f",
        "s16le",
        "-acodec",
        "pcm_s16le",
        "-ar",
        str(probe.sample_rate),
        "-ac",
        str(probe.channels),
        "pipe:1",
    ]
    encoder_command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "s16le",
        "-ar",
        str(probe.sample_rate),
        "-ac",
        str(probe.channels),
        "-i",
        "pipe:0",
        "-map_metadata",
        "-1",
        "-c:a",
        "libmp3lame",
    ]
    if settings["preserve_original_quality"]:
        encoder_command.extend(["-q:a", "0"])
    else:
        encoder_command.extend(["-b:a", "128k"])
    encoder_command.append(output_path)

    decoder = subprocess.Popen(
        decoder_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    encoder = subprocess.Popen(
        encoder_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if decoder.stdout is None or decoder.stderr is None or encoder.stdin is None or encoder.stderr is None:
        decoder.kill()
        encoder.kill()
        raise VoiceoverProcessingError("Could not start the voiceover renderer.")

    bytes_per_frame = probe.channels * 2
    source_cursor = 0
    pending_tail = b""
    timed_out = threading.Event()

    def kill_renderers_on_timeout():
        timed_out.set()
        decoder.kill()
        encoder.kill()

    watchdog = threading.Timer(
        max(5, int(timeout_seconds)), kill_renderers_on_timeout
    )
    watchdog.daemon = True
    watchdog.start()
    try:
        for index, (start_ms, end_ms) in enumerate(plan.intervals_ms):
            start_frame = round(start_ms * probe.sample_rate / 1000)
            end_frame = round(end_ms * probe.sample_rate / 1000)
            if start_frame > source_cursor:
                _read_exact(decoder.stdout, (start_frame - source_cursor) * bytes_per_frame)
                source_cursor = start_frame
            interval_bytes = max(0, end_frame - start_frame) * bytes_per_frame
            previous_overlap_ms = plan.overlaps_ms[index - 1] if index > 0 else 0
            previous_overlap_bytes = (
                round(previous_overlap_ms * probe.sample_rate / 1000)
                * bytes_per_frame
            )
            next_overlap_ms = plan.overlaps_ms[index] if index < len(plan.overlaps_ms) else 0
            next_overlap_bytes = (
                round(next_overlap_ms * probe.sample_rate / 1000)
                * bytes_per_frame
            )
            head = _read_exact(
                decoder.stdout, min(interval_bytes, previous_overlap_bytes)
            )
            source_cursor += len(head) // bytes_per_frame
            interval_bytes -= len(head)
            if pending_tail or head:
                mixed_size = min(len(pending_tail), len(head))
                if mixed_size:
                    encoder.stdin.write(
                        _mix_crossfade(
                            pending_tail[-mixed_size:],
                            head[:mixed_size],
                            settings["transition_smoothness"],
                            probe.channels,
                        )
                    )
                    if len(pending_tail) > mixed_size:
                        encoder.stdin.write(pending_tail[:-mixed_size])
                    if len(head) > mixed_size:
                        encoder.stdin.write(head[mixed_size:])
                else:
                    encoder.stdin.write(pending_tail or head)
                pending_tail = b""

            body_bytes = max(0, interval_bytes - next_overlap_bytes)
            copied = _copy_exact(decoder.stdout, encoder.stdin, body_bytes)
            source_cursor += copied // bytes_per_frame
            interval_bytes -= copied
            pending_tail = _read_exact(
                decoder.stdout, min(interval_bytes, next_overlap_bytes)
            )
            source_cursor += len(pending_tail) // bytes_per_frame
            remaining = interval_bytes - len(pending_tail)
            if remaining > 0:
                copied = _copy_exact(decoder.stdout, encoder.stdin, remaining)
                source_cursor += copied // bytes_per_frame
        if pending_tail:
            encoder.stdin.write(pending_tail)
        encoder.stdin.close()
        decoder_stderr = decoder.stderr.read().decode("utf-8", errors="replace")
        encoder_stderr = encoder.stderr.read().decode("utf-8", errors="replace")
        decoder_code = decoder.wait()
        encoder_code = encoder.wait()
    except (BrokenPipeError, subprocess.TimeoutExpired) as exc:
        decoder.kill()
        encoder.kill()
        raise VoiceoverProcessingError("Voiceover rendering failed or timed out.") from exc
    finally:
        watchdog.cancel()
        if decoder.poll() is None:
            decoder.kill()
        if encoder.poll() is None:
            encoder.kill()
    if timed_out.is_set():
        raise VoiceoverProcessingError("Voiceover rendering timed out.")
    if decoder_code != 0 or encoder_code != 0:
        detail = (encoder_stderr or decoder_stderr).strip().splitlines()
        suffix = f": {detail[-1][:300]}" if detail else ""
        raise VoiceoverProcessingError(f"Could not export the tightened MP3{suffix}")
    if not Path(output_path).is_file() or Path(output_path).stat().st_size <= 0:
        raise VoiceoverProcessingError("Voiceover export produced an empty file.")


def process_voiceover(
    source_path: str,
    output_path: str,
    preset: str,
    settings: dict,
    *,
    max_duration_seconds: int = 3600,
    timeout_seconds: int = 1800,
) -> dict:
    probe = probe_mp3(source_path)
    if probe.duration_ms > max_duration_seconds * 1000:
        raise VoiceoverProcessingError(
            f"Voiceovers must be {max_duration_seconds // 60} minutes or shorter."
        )
    analysis = analyze_voiceover(
        source_path, settings, timeout_seconds=timeout_seconds
    )
    plan = build_edit_plan(probe.duration_ms, analysis, settings, preset)
    render_edit_plan(
        source_path,
        output_path,
        probe,
        plan,
        settings,
        timeout_seconds=timeout_seconds,
    )
    output_probe = probe_mp3(output_path)
    return {
        "original_duration_ms": probe.duration_ms,
        "output_duration_ms": output_probe.duration_ms,
        "removed_duration_ms": max(0, probe.duration_ms - output_probe.duration_ms),
        "pauses_shortened": plan.pauses_shortened,
        "overlaps_applied": plan.overlaps_applied,
        "warnings": analysis.warnings,
    }
