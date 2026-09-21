# ScoreSight MusicXML Dataset Filtering Tools

This project filters candidate piano MusicXML excerpts for the ScoreSight sight-reading study and uses MuseScore to generate a first-page PNG preview for each selected score.

The workflow has two stages:

1. `analyze_scores.py` analyzes and filters the MusicXML files, then writes the selected scores to a CSV file.
2. `export_previews.py` reads the selected scores from the CSV and uses MuseScore to export their first-page previews.

## Project Structure

```text
ScoreSightDataset/
├── analyze_scores.py
├── export_previews.py
├── scoresight_musicxml_analysis.csv
├── candidate_musicxml_full_bothhands/
│   └── Candidate MusicXML files
└── previews/
    └── selected/
        ├── *_preview.png
        └── preview_export_log.csv
```

## Requirements

- Windows
- Python 3.13
- `music21`
- MuseScore 4 for preview generation

The Python installation currently used for this project is:

```text
C:\Users\86138\AppData\Local\Programs\Python\Python313\python.exe
```

Creating a project-specific virtual environment is recommended:

```powershell
cd C:\Users\86138\Desktop\ScoreSightDataset

& "C:\Users\86138\AppData\Local\Programs\Python\Python313\python.exe" -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install music21
```

If PowerShell prevents virtual-environment activation, run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## Running the Analysis

Place the candidate scores in:

```text
candidate_musicxml_full_bothhands/
```

Run:

```powershell
python analyze_scores.py
```

The script recursively discovers and analyzes all `.musicxml`, `.xml`, and `.mxl` files. Only scores that pass every filter are written to:

```text
scoresight_musicxml_analysis.csv
```

Files that fail a filter or cannot be parsed are not included in the CSV.

## Current Selection Criteria

A score must satisfy all of the following conditions:

| Metric | Required value |
|---|---:|
| MusicXML parsing | Successful |
| Number of parts | `num_parts == 2` |
| Initial key signature | No sharps or flats |
| Left-hand melody detection | False |
| Actual measure count | 8–64 |
| Note count | At least 30 |
| Maximum polyphony | No more than 6 |
| Chord event count | 1–10 |
| Accidental ratio | No more than 10% |
| Large-leap count | At least 1 |
| Large-leap ratio | No more than 35% |

The `note_density <= 5` condition is currently commented out. Note density is still calculated and written to the CSV, but it does not affect selection.

## Metric Definitions

### Actual Measure Count

`actual_measures` is the maximum measure count among all parts. For example, when both the right-hand and left-hand parts contain eight measures:

- `num_measures = 16`
- `actual_measures = 8`

### Notes and Chords

- `num_notes` includes individual notes and every pitch contained in a chord.
- `num_chords` counts chord events. One chord is counted as one event.
- `chord_ratio` is the number of chord events divided by the total number of individual-note and chord events.
- `note_density = num_notes / duration_quarter_lengths`.

### Large Leaps

The current entry point uses a large-leap threshold of seven semitones:

```python
large_leap_threshold=7
```

Large leaps are calculated as follows:

- The two hands are analyzed separately and their results are combined.
- Only individual `note.Note` events are used.
- `chord.Chord` events are completely excluded from the calculation.
- If a chord occurs between two individual notes, the chord is skipped and the two individual notes are compared directly.
- `large_leap_ratio` is the number of large leaps divided by the number of adjacent individual-note relationships.

### Accidentals

- `num_accidentals` is the number of pitches containing a sharp or flat.
- Individual notes and pitches inside chords are both included.
- `accidental_ratio = num_accidentals / num_notes`.
- Only scores with `accidental_ratio <= 0.10` are retained.
- Natural signs are not included in this count.

### Left-Hand Melody Detection

MusicXML does not normally identify which hand carries the primary melody, so the script uses a conservative heuristic.

The part with the higher average pitch is treated as the right hand, and the lower-pitched part is treated as the left hand. A score is classified as having a left-hand melody only when all of the following conditions are true:

- The right hand contains more chord events than individual-note events.
- The left hand contains more individual-note events than the right hand.
- The individual-note range of the left hand is at least 12 semitones.

The classification and its supporting measurements are written to these CSV fields:

- `right_hand_single_notes`
- `right_hand_chords`
- `left_hand_single_notes`
- `left_hand_chords`
- `left_hand_melody_range_semitones`
- `left_hand_is_melody`

This classification is heuristic and should be manually reviewed using the generated PNG previews.

## Exporting Previews

`export_previews.py` currently uses this MuseScore executable:

```text
C:\Program Files\MuseScore 4\bin\MuseScore4.exe
```

Run:

```powershell
python export_previews.py
```

The script performs the following steps:

1. Reads each `filepath` from `scoresight_musicxml_analysis.csv`.
2. Clears the previous contents of `previews\selected`.
3. Calls MuseScore to export each score as PNG.
4. Keeps only the first page of each score.
5. Renames each output to `original_filename_preview.png`.
6. Writes `preview_export_log.csv` with the success status and any error for each file.

The preview loop currently uses `files[:100]`, so it exports at most the first 100 scores from the CSV in one run. To export every selected score, change:

```python
for i, score_file in enumerate(files[:100], start=1):
```

to:

```python
for i, score_file in enumerate(files, start=1):
```

Each preview run clears the previous contents of `previews\selected` before exporting. If the process is interrupted, the directory may contain only a partial set of previews. Run the script again to rebuild it.

## Path Configuration

`analyze_scores.py` uses relative paths:

```python
INPUT_FOLDER = "./candidate_musicxml_full_bothhands"
OUTPUT_CSV = "scoresight_musicxml_analysis.csv"
```

`export_previews.py` currently uses absolute paths. If the project is moved, update these values near the top of the file:

- `ANALYSIS_CSV`
- `INPUT_FOLDER`
- `OUTPUT_FOLDER`
- `MUSESCORE_EXE`

## Troubleshooting

### `PermissionError` While Saving the CSV

The CSV is usually open and locked by Excel or WPS. Close the file and any remaining Excel or WPS processes, then run the analysis again.

### `No module named 'music21'`

Activate the project virtual environment and run:

```powershell
python -m pip install music21
```

### MuseScore Executable Not Found

Confirm that MuseScore 4 is installed and that `MUSESCORE_EXE` in `export_previews.py` points to the correct executable.

### Fewer Preview Images Than CSV Rows

Possible causes include:

- The preview script currently processes only the first 100 CSV rows.
- The export process was interrupted.
- MuseScore failed to export one or more files.

Review the export log for details:

```text
previews\selected\preview_export_log.csv
```
