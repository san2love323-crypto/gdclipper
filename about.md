# GD Clipper

Records the game while you are inside a level. When you **clear** it, two files are saved:

- `GD_clear_<date>_cut.mp4` - only the winning attempt
- `GD_clear_<date>_full.mp4` - everything since you entered the level (can be turned off)

If you leave the level without clearing, the footage is thrown away. ESC just pauses.

No companion app is needed. On first launch the mod downloads `ffmpeg.exe` (about 90 MB) into its save folder; recording starts working once that has finished.

Video only (no game audio yet). Output folder: `Videos\GD Clips` unless changed in the mod settings.
