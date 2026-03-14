# Dolby Vision Fix for Plex

Unmanic plugin that converts Dolby Vision (Profile 5) HEVC to Plex-compatible Main10 HEVC. DV files are re-encoded; non-DV HEVC files are remuxed (stream copy). Audio and subtitles are preserved.

## Requirements

- **Unmanic** (Plugin Handler v2)
- **FFmpeg** with HEVC support
- **NVIDIA GPU** (recommended) with NVENC for hardware encoding
- **libx265** (optional) for CPU fallback when NVENC is unavailable

## Installation

1. Download the plugin zip or clone this repository.
2. In Unmanic: **Settings → Plugins → Install plugin** and select the zip, or place the plugin folder in your Unmanic plugins directory.
3. Enable the plugin and configure settings.

## Settings

| Setting | Default | Description |
|--------|---------|-------------|
| **Use NVENC** | On | Use NVIDIA GPU encoding for DV files. Recommended for 4K. |
| **Enable CPU fallback** | Off | Use libx265 when NVENC is unavailable. Slow for 4K. |
| **NVENC preset** | p5 | Encoding speed vs quality: p1 (fastest) to p7 (best quality). p5 is a good balance. |
| **Enable DV metadata removal** | On | Strip Dolby Vision metadata when FFmpeg supports `dovi_rpu`. |

## Behavior

- **DV Profile 5 detected**: Re-encodes to Main10 HEVC using NVENC (or libx265 if CPU fallback is enabled and NVENC is unavailable).
- **Non-DV HEVC**: Remuxes (stream copy) without re-encoding.
- **No NVENC, no CPU fallback**: DV files are skipped.

## Troubleshooting

**"NVENC required" or files are skipped**
- Ensure an NVIDIA GPU is available and drivers are installed.
- Or enable **Enable CPU fallback** (requires libx265; encoding will be slow).

**Green or pink tint in output**
- Verify the source is Dolby Vision Profile 5.
- Try a different NVENC preset (e.g. p5 or p6).

**Encoding is very slow**
- Use NVENC (GPU) instead of CPU fallback.
- For NVENC, try preset p4 for faster encoding (slightly lower quality).

## License

See repository license file.
