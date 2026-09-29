import csv
import os
import re
from collections import defaultdict
from pathlib import Path

from music21 import chord, clef, converter, key, meter, note, stream


SUPPORTED_EXTENSIONS = {".musicxml", ".xml", ".mxl"}
INPUT_FOLDER = "./candidate_musicxml_full_bothhands"
OUTPUT_CSV = "all_scores_feature_analysis.csv"
LARGE_LEAP_THRESHOLD = 10
ONSET_PRECISION = 6

BASE_FIELDS = [
    "file_name", "title", "composer", "num_parts", "num_measures",
    "time_signature", "key_signature", "total_duration", "parse_success",
    "parse_error", "analysis_error", "hand_assignment_method",
]

HAND_FEATURE_NAMES = [
    "note_count", "note_density", "chord_ratio", "avg_chord_size",
    "accidental_ratio", "pitch_range", "large_leap_count",
    "large_leap_ratio", "max_leap", "rest_ratio", "rhythm_variety",
    "short_note_ratio", "max_polyphony",
]

CSV_FIELDNAMES = (
    BASE_FIELDS
    + [f"RH_{name}" for name in HAND_FEATURE_NAMES]
    + [f"LH_{name}" for name in HAND_FEATURE_NAMES]
    + [
        "both_onset_ratio", "RH_LH_density_difference",
        "rhythm_mismatch_ratio", "total_note_density",
    ]
)


def parse_score(file_path):
    """Parse one MusicXML file without allowing an error to stop the batch."""
    try:
        return converter.parse(file_path), None
    except Exception as exc:
        return None, str(exc)


def _normalized_text(value):
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _part_label(part):
    labels = [getattr(part, "partName", None), getattr(part, "id", None)]
    try:
        instrument = part.getInstrument(returnDefault=False)
    except Exception:
        instrument = None
    if instrument is not None:
        labels.extend([
            getattr(instrument, "instrumentName", None),
            getattr(instrument, "partName", None),
        ])
    return _normalized_text(" ".join(str(label) for label in labels if label))


def _named_hand(label):
    tokens = set(label.split())
    if (
        "right" in tokens or "rh" in tokens or "upper" in tokens
        or "treble" in tokens or "staff 1" in label
    ):
        return "RH"
    if (
        "left" in tokens or "lh" in tokens or "lower" in tokens
        or "bass" in tokens or "staff 2" in label
    ):
        return "LH"
    return None


def _first_clef_role(part):
    try:
        clefs = list(part.recurse().getElementsByClass(clef.Clef))
    except Exception:
        return None
    if not clefs:
        return None
    sign = getattr(clefs[0], "sign", None)
    if sign == "G":
        return "RH"
    if sign == "F":
        return "LH"
    return None


def _average_pitch(part):
    midi_values = []
    for element in part.recurse().notes:
        if isinstance(element, note.Note):
            midi_values.append(element.pitch.midi)
        elif isinstance(element, chord.Chord):
            midi_values.extend(pitch.midi for pitch in element.pitches)
    if not midi_values:
        return None
    return sum(midi_values) / len(midi_values)


def _staff_number(part):
    """Return an explicit staff number encoded in a PartStaff/id, if present."""
    class_name = part.__class__.__name__.lower()
    label = _part_label(part)
    match = re.search(r"(?:staff|stave)\s*([12])(?:\b|$)", label)
    if match:
        return int(match.group(1))
    if class_name == "partstaff":
        # music21 preserves grand-staff order in Score.parts. The caller may
        # therefore use order when both objects are explicitly PartStaff.
        return 0
    return None


def identify_hands(score):
    """Return (RH parts, LH parts, assignment method), in priority order."""
    parts = list(score.parts)
    if len(parts) < 2:
        return None, None, "unreliable"

    staff_numbers = [_staff_number(part) for part in parts]
    if len(parts) == 2:
        if set(staff_numbers) == {1, 2}:
            return (
                [parts[staff_numbers.index(1)]],
                [parts[staff_numbers.index(2)]],
                "staff_structure",
            )
        if all(number == 0 for number in staff_numbers):
            return [parts[0]], [parts[1]], "staff_structure"

    named_roles = [_named_hand(_part_label(part)) for part in parts]
    rh_named = [part for part, role in zip(parts, named_roles) if role == "RH"]
    lh_named = [part for part, role in zip(parts, named_roles) if role == "LH"]
    if len(rh_named) == 1 and len(lh_named) == 1 and rh_named[0] is not lh_named[0]:
        return rh_named, lh_named, "part_name"

    if len(parts) == 2:
        clef_roles = [_first_clef_role(part) for part in parts]
        if clef_roles == ["RH", "LH"]:
            return [parts[0]], [parts[1]], "clef"
        if clef_roles == ["LH", "RH"]:
            return [parts[1]], [parts[0]], "clef"

        averages = [_average_pitch(part) for part in parts]
        if None not in averages and abs(averages[0] - averages[1]) >= 1.0:
            if averages[0] > averages[1]:
                return [parts[0]], [parts[1]], "average_pitch_fallback"
            return [parts[1]], [parts[0]], "average_pitch_fallback"

    return None, None, "unreliable"


def _voice_key(element, source, source_index):
    current_voice = element.getContextByClass(stream.Voice)
    if current_voice is None:
        return (source_index, "default")
    if current_voice.id is not None:
        return (source_index, f"voice_{current_voice.id}")

    current_measure = current_voice.getContextByClass(stream.Measure)
    if current_measure is not None:
        voices = list(current_measure.getElementsByClass(stream.Voice))
        try:
            return (source_index, f"voice_index_{voices.index(current_voice)}")
        except ValueError:
            pass
    return (source_index, "unnamed_voice")


def extract_hand_events(hand_sources):
    """Extract positioned note/chord/rest events for one hand."""
    events = []
    for source_index, source in enumerate(hand_sources):
        for element in source.recurse().notesAndRests:
            if not isinstance(element, (note.Note, chord.Chord, note.Rest)):
                continue
            try:
                onset = float(element.getOffsetInHierarchy(source))
                duration = float(element.duration.quarterLength)
            except (TypeError, ValueError, AttributeError):
                continue

            if isinstance(element, note.Note):
                pitches = [element.pitch]
                event_type = "note"
            elif isinstance(element, chord.Chord):
                pitches = list(element.pitches)
                event_type = "chord"
            else:
                pitches = []
                event_type = "rest"

            events.append({
                "onset": onset,
                "duration": duration,
                "end": onset + duration,
                "pitches": pitches,
                "type": event_type,
                "voice": _voice_key(element, source, source_index),
            })
    events.sort(key=lambda event: (event["onset"], event["type"], event["duration"]))
    return events


def calculate_max_polyphony(events):
    """Calculate maximum simultaneously sounding pitches using time spans."""
    changes = []
    for event in events:
        if not event["pitches"] or event["duration"] <= 0:
            continue
        pitch_count = len(event["pitches"])
        changes.append((event["onset"], 1, pitch_count))
        changes.append((event["end"], 0, -pitch_count))

    # Endings sort before starts at the same time, so adjacent notes do not overlap.
    changes.sort(key=lambda item: (item[0], item[1]))
    sounding = 0
    maximum = 0
    for _, _, delta in changes:
        sounding += delta
        maximum = max(maximum, sounding)
    return maximum


def calculate_sounding_union_duration(events):
    """Return the union length of all sounding note/chord time intervals.

    Each note or chord event contributes one interval regardless of chord size.
    Overlapping and adjacent intervals are merged, which prevents rests or
    sounding time in multiple voices from being counted more than once.
    """
    intervals = sorted(
        (event["onset"], event["end"])
        for event in events
        if event["pitches"] and event["duration"] > 0
    )
    if not intervals:
        return 0.0

    merged_duration = 0.0
    current_start, current_end = intervals[0]
    tolerance = 10 ** (-ONSET_PRECISION)

    for start, end in intervals[1:]:
        if start <= current_end + tolerance:
            current_end = max(current_end, end)
        else:
            merged_duration += current_end - current_start
            current_start, current_end = start, end

    merged_duration += current_end - current_start
    return merged_duration


def _explicit_accidental_count(pitches):
    return sum(
        1 for pitch in pitches
        if pitch.accidental is not None and pitch.accidental.displayStatus is True
    )


def _melodic_intervals(events):
    by_voice = defaultdict(list)
    for event in events:
        if event["type"] == "note" and len(event["pitches"]) == 1:
            by_voice[event["voice"]].append(event)

    intervals = []
    for voice_events in by_voice.values():
        voice_events.sort(key=lambda event: event["onset"])
        for previous, current in zip(voice_events, voice_events[1:]):
            intervals.append(abs(
                current["pitches"][0].midi - previous["pitches"][0].midi
            ))
    return intervals


def extract_hand_features(events, total_duration, prefix):
    musical_events = [event for event in events if event["type"] != "rest"]
    chord_events = [event for event in musical_events if event["type"] == "chord"]
    pitches = [pitch for event in musical_events for pitch in event["pitches"]]

    note_count = len(pitches)
    event_count = len(musical_events)
    intervals = _melodic_intervals(events)
    large_leaps = [size for size in intervals if size >= LARGE_LEAP_THRESHOLD]

    values = {
        "note_count": note_count,
        "note_density": note_count / total_duration if total_duration > 0 else None,
        "chord_ratio": len(chord_events) / event_count if event_count else None,
        "avg_chord_size": (
            sum(len(event["pitches"]) for event in chord_events) / len(chord_events)
            if chord_events else 0
        ),
        "accidental_ratio": (
            _explicit_accidental_count(pitches) / note_count if note_count else None
        ),
        "pitch_range": (
            max(pitch.midi for pitch in pitches) - min(pitch.midi for pitch in pitches)
            if pitches else None
        ),
        "large_leap_count": len(large_leaps),
        "large_leap_ratio": len(large_leaps) / len(intervals) if intervals else None,
        "max_leap": max(intervals) if intervals else None,
        "rest_ratio": (
            max(
                0.0,
                total_duration - calculate_sounding_union_duration(musical_events),
            )
            / total_duration
            if total_duration > 0 else None
        ),
        "rhythm_variety": len({
            round(event["duration"], ONSET_PRECISION) for event in musical_events
        }),
        "short_note_ratio": (
            sum(event["duration"] <= 0.5 for event in musical_events) / event_count
            if event_count else None
        ),
        "max_polyphony": calculate_max_polyphony(musical_events),
    }
    return {f"{prefix}_{name}": value for name, value in values.items()}


def _onset_duration_map(events):
    result = defaultdict(set)
    for event in events:
        if event["type"] == "rest":
            continue
        onset = round(event["onset"], ONSET_PRECISION)
        duration = round(event["duration"], ONSET_PRECISION)
        result[onset].add(duration)
    return result


def calculate_both_onset_ratio(rh_events, lh_events):
    rh_onsets = set(_onset_duration_map(rh_events))
    lh_onsets = set(_onset_duration_map(lh_events))
    all_onsets = rh_onsets | lh_onsets
    if not all_onsets:
        return None
    return len(rh_onsets & lh_onsets) / len(all_onsets)


def calculate_rhythm_mismatch_ratio(rh_events, lh_events):
    """Compare complete duration sets at shared onsets, supporting multiple voices."""
    rh_durations = _onset_duration_map(rh_events)
    lh_durations = _onset_duration_map(lh_events)
    simultaneous = set(rh_durations) & set(lh_durations)
    if not simultaneous:
        return None
    mismatches = sum(
        rh_durations[onset] != lh_durations[onset] for onset in simultaneous
    )
    return mismatches / len(simultaneous)


def _initial_element(score, element_class):
    candidates = []
    for part_index, part in enumerate(score.parts):
        for element in part.recurse().getElementsByClass(element_class):
            try:
                offset = float(element.getOffsetInHierarchy(part))
            except (TypeError, ValueError, AttributeError):
                offset = float(getattr(element, "offset", 0))
            candidates.append((offset, part_index, element))
    if not candidates:
        return None
    return min(candidates, key=lambda item: (item[0], item[1]))[2]


def _empty_row(file_path):
    return {field: None for field in CSV_FIELDNAMES} | {
        "file_name": Path(file_path).name,
        "parse_success": False,
        "parse_error": "",
        "analysis_error": "",
        "hand_assignment_method": "unreliable",
    }


def _analyze_score(file_path):
    row = _empty_row(file_path)
    score, error = parse_score(file_path)
    if score is None:
        row["parse_error"] = error
        return row

    row["parse_success"] = True
    metadata = getattr(score, "metadata", None)
    row["title"] = getattr(metadata, "title", None) if metadata else None
    row["composer"] = getattr(metadata, "composer", None) if metadata else None

    parts = list(score.parts)
    row["num_parts"] = len(parts)
    row["num_measures"] = max(
        (len(list(part.recurse().getElementsByClass(stream.Measure))) for part in parts),
        default=0,
    )
    try:
        row["total_duration"] = float(score.highestTime)
    except (TypeError, ValueError, AttributeError):
        row["total_duration"] = None

    initial_time = _initial_element(score, meter.TimeSignature)
    initial_key = _initial_element(score, key.KeySignature)
    row["time_signature"] = initial_time.ratioString if initial_time else None
    row["key_signature"] = int(initial_key.sharps or 0) if initial_key else None

    rh_sources, lh_sources, method = identify_hands(score)
    row["hand_assignment_method"] = method
    if method == "unreliable":
        return row

    rh_events = extract_hand_events(rh_sources)
    lh_events = extract_hand_events(lh_sources)
    total_duration = row["total_duration"]
    usable_duration = total_duration if total_duration is not None else 0

    row.update(extract_hand_features(rh_events, usable_duration, "RH"))
    row.update(extract_hand_features(lh_events, usable_duration, "LH"))
    row["both_onset_ratio"] = calculate_both_onset_ratio(rh_events, lh_events)
    row["rhythm_mismatch_ratio"] = calculate_rhythm_mismatch_ratio(
        rh_events, lh_events
    )

    rh_density = row["RH_note_density"]
    lh_density = row["LH_note_density"]
    row["RH_LH_density_difference"] = (
        abs(rh_density - lh_density)
        if rh_density is not None and lh_density is not None else None
    )
    row["total_note_density"] = (
        (row["RH_note_count"] + row["LH_note_count"]) / total_duration
        if total_duration is not None and total_duration > 0 else None
    )
    return row


def analyze_score(file_path):
    """Analyze one file and always return a CSV-compatible result row."""
    try:
        return _analyze_score(file_path)
    except Exception as exc:
        # parse_score catches parser exceptions itself. Therefore an exception
        # reaching this wrapper occurred later, during feature extraction.
        row = _empty_row(file_path)
        row["parse_success"] = True
        row["analysis_error"] = str(exc)
        return row


def find_musicxml_files(input_folder):
    files = []
    for root, _, filenames in os.walk(input_folder):
        for filename in filenames:
            path = Path(root) / filename
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)
    return sorted(files, key=lambda path: str(path).lower())


def main(input_folder=INPUT_FOLDER, output_csv=OUTPUT_CSV):
    files = find_musicxml_files(input_folder)
    print(f"Found {len(files)} MusicXML files.")

    results = []
    for index, file_path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] Analyzing: {file_path.name}")
        results.append(analyze_score(file_path))

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(results)

    successful = sum(row["parse_success"] is True for row in results)
    failed = len(results) - successful
    identified = sum(
        row["parse_success"] is True
        and row["hand_assignment_method"] != "unreliable"
        for row in results
    )
    fallback = sum(
        row["hand_assignment_method"] == "average_pitch_fallback" for row in results
    )
    unreliable = sum(
        row["parse_success"] is True
        and row["hand_assignment_method"] == "unreliable"
        for row in results
    )
    analysis_errors = sum(bool(row["analysis_error"]) for row in results)

    print()
    print(f"Total files found: {len(files)}")
    print(f"Successfully parsed: {successful}")
    print(f"Parse failures: {failed}")
    print(f"RH/LH successfully identified: {identified}")
    print(f"Average-pitch fallback used: {fallback}")
    print(f"Unreliable hand assignments: {unreliable}")
    print(f"Feature extraction errors: {analysis_errors}")
    print(f"CSV saved to: {Path(output_csv).resolve()}")


if __name__ == "__main__":
    main()
