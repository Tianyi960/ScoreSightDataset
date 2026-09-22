import os
import csv
import zipfile
import tempfile
from pathlib import Path
from collections import Counter

from music21 import converter, chord, note, stream, interval, key, meter, tempo


SUPPORTED_EXTENSIONS = {".musicxml", ".xml", ".mxl"}

CSV_FIELDNAMES = [
    "filename",
    "filepath",
    "parse_success",
    "error",
    "title",
    "composer",
    "num_parts",
    "num_measures",
    "actual_measures",
    "num_notes",
    "num_chords",
    "num_rest",
    "num_accidentals",
    "accidental_ratio",
    "pitch_min",
    "pitch_max",
    "pitch_range_semitones",
    "num_large_leaps",
    "large_leap_ratio",
    "max_large_leap_semitones",
    "max_polyphony",
    "duration_quarter_lengths",
    "note_density",
    "chord_ratio",
    "tempo_bpm",
    "time_signatures",
    "key_signature_sharps",
    "no_key_signature",
    "right_hand_single_notes",
    "right_hand_chords",
    "left_hand_single_notes",
    "left_hand_chords",
    "left_hand_melody_range_semitones",
    "left_hand_is_melody",
    "selected"
]


def load_score(file_path):
    """
    Load MusicXML / MXL file using music21.
    Returns:
        score, error_message
    """
    try:
        score = converter.parse(file_path)
        return score, None
    except Exception as e:
        return None, str(e)


def get_note_sequences(part):
    """
    Extract single pitched notes in temporal order for each voice.

    Chords are excluded completely from melodic leap calculation, and
    notes from different voices are never compared with each other.
    """
    sequences = {}

    for element in part.recurse().notes:
        if isinstance(element, note.Note):
            current_voice = element.getContextByClass(stream.Voice)

            if current_voice is None:
                voice_id = "default"
            elif current_voice.id is not None:
                voice_id = f"voice_{current_voice.id}"
            else:
                current_measure = current_voice.getContextByClass(
                    stream.Measure
                )
                measure_voices = list(
                    current_measure.getElementsByClass(stream.Voice)
                )
                voice_index = measure_voices.index(current_voice)
                voice_id = f"voice_index_{voice_index}"

            sequences.setdefault(voice_id, []).append({
                "offset": float(element.getOffsetInHierarchy(part)),
                "pitch": element.pitch,
                "is_chord": False,
                "chord_size": 1
            })

    for events in sequences.values():
        events.sort(key=lambda x: x["offset"])

    return list(sequences.values())


def count_large_leaps(events, threshold_semitones=10):
    """
    Count melodic jumps.

    Default threshold:
        10 semitones = minor seventh.

    This includes both minor sevenths (10 semitones), major sevenths
    (11 semitones), octaves, and larger intervals.

    You can change this depending on how you define
    'large leap' in ScoreSight.
    """
    leap_count = 0
    leap_sizes = []

    for i in range(1, len(events)):
        p1 = events[i - 1]["pitch"]
        p2 = events[i]["pitch"]

        semitones = abs(p2.midi - p1.midi)

        if semitones >= threshold_semitones:
            leap_count += 1
            leap_sizes.append(semitones)

    return leap_count, leap_sizes


def calculate_polyphony(score):
    """
    Rough estimate of maximum simultaneous notes.

    This is intentionally simple:
    count notes/chord tones starting at same offset.
    """
    offset_counter = Counter()

    for element in score.recurse().notes:
        global_offset = float(element.getOffsetInHierarchy(score))

        if isinstance(element, note.Note):
            offset_counter[global_offset] += 1

        elif isinstance(element, chord.Chord):
            offset_counter[global_offset] += len(element.pitches)

    if not offset_counter:
        return 0

    return max(offset_counter.values())


def get_pitch_range(score):
    pitches = []

    for element in score.recurse().notes:
        if isinstance(element, note.Note):
            pitches.append(element.pitch)

        elif isinstance(element, chord.Chord):
            pitches.extend(element.pitches)

    if not pitches:
        return None, None, 0

    min_pitch = min(pitches)
    max_pitch = max(pitches)

    semitone_range = max_pitch.midi - min_pitch.midi

    return min_pitch.nameWithOctave, max_pitch.nameWithOctave, semitone_range


def estimate_duration(score):
    """
    Estimate score duration in quarter lengths.

    This is not real seconds unless tempo is known.
    """
    try:
        return float(score.highestTime)
    except Exception:
        return 0


def get_tempo(score):
    """
    Return first detected metronome mark.
    """
    tempos = list(score.recurse().getElementsByClass(tempo.MetronomeMark))

    for t in tempos:
        if t.number is not None:
            return float(t.number)

    return None


def get_time_signatures(score):
    signatures = []

    for ts in score.recurse().getElementsByClass(meter.TimeSignature):
        signatures.append(ts.ratioString)

    return ",".join(sorted(set(signatures)))


def get_initial_key_signature(parts):
    """
    Return the initial key signature's number of sharps/flats.

    An absent key signature is treated as zero. If the piano staves have
    different initial signatures, return the first non-zero value so the
    score is not classified as having no key signature.
    """
    initial_sharps = []

    for part in parts:
        for ks in part.recurse().getElementsByClass(key.KeySignature):
            try:
                offset = float(ks.getOffsetInHierarchy(part))
            except Exception:
                offset = float(ks.offset)

            if abs(offset) < 1e-9:
                initial_sharps.append(int(ks.sharps or 0))

    for sharps in initial_sharps:
        if sharps != 0:
            return sharps

    return 0


def get_hand_melody_metrics(parts):
    """
    Estimate whether the lower-pitched piano part carries the melody.

    The higher-pitched part is treated as the right hand and the
    lower-pitched part as the left hand. A left-hand melody is flagged
    conservatively when the right hand is chord-dominant, the left hand
    has more single-note events, and its melodic range is at least one octave.
    """
    empty_metrics = {
        "right_hand_single_notes": 0,
        "right_hand_chords": 0,
        "left_hand_single_notes": 0,
        "left_hand_chords": 0,
        "left_hand_melody_range_semitones": 0,
        "left_hand_is_melody": False
    }

    if len(parts) != 2:
        return empty_metrics

    part_metrics = []

    for part in parts:
        single_notes = list(
            part.recurse().getElementsByClass(note.Note)
        )
        chords = list(
            part.recurse().getElementsByClass(chord.Chord)
        )

        all_pitches = [n.pitch.midi for n in single_notes]

        for current_chord in chords:
            all_pitches.extend(
                pitch.midi for pitch in current_chord.pitches
            )

        average_pitch = (
            sum(all_pitches) / len(all_pitches)
            if all_pitches
            else 0
        )

        single_note_pitches = [n.pitch.midi for n in single_notes]
        single_note_range = (
            max(single_note_pitches) - min(single_note_pitches)
            if single_note_pitches
            else 0
        )

        part_metrics.append({
            "single_notes": len(single_notes),
            "chords": len(chords),
            "average_pitch": average_pitch,
            "single_note_range": single_note_range
        })

    part_metrics.sort(
        key=lambda metrics: metrics["average_pitch"],
        reverse=True
    )

    right_hand = part_metrics[0]
    left_hand = part_metrics[1]

    left_hand_is_melody = (
        right_hand["chords"] > right_hand["single_notes"]
        and left_hand["single_notes"] > right_hand["single_notes"]
        and left_hand["single_note_range"] >= 12
    )

    return {
        "right_hand_single_notes": right_hand["single_notes"],
        "right_hand_chords": right_hand["chords"],
        "left_hand_single_notes": left_hand["single_notes"],
        "left_hand_chords": left_hand["chords"],
        "left_hand_melody_range_semitones": left_hand["single_note_range"],
        "left_hand_is_melody": left_hand_is_melody
    }


def meets_filter_criteria(row):
    return (
        row["parse_success"] is True
        and row["num_parts"] == 2
        and row["no_key_signature"] is True
        and row["left_hand_is_melody"] is False
        and 8 <= row["actual_measures"] <= 64
        and row["num_notes"] >= 30
        and row["max_polyphony"] <= 6
        # and row["note_density"] <= 5
        and 1 <= row["num_chords"] <= 10
        and row["accidental_ratio"] <= 0.10
        and row["num_large_leaps"] >= 1
        and row["large_leap_ratio"] <= 0.35
    )


def analyze_score(file_path, large_leap_threshold=10):
    row = {
        "filename": os.path.basename(file_path),
        "filepath": str(file_path),

        "parse_success": False,
        "error": "",

        "title": "",
        "composer": "",

        "num_parts": 0,
        "num_measures": 0,
        "actual_measures": 0,
        "num_notes": 0,
        "num_chords": 0,
        "num_rest": 0,
        "num_accidentals": 0,
        "accidental_ratio": 0,

        "pitch_min": "",
        "pitch_max": "",
        "pitch_range_semitones": 0,

        "num_large_leaps": 0,
        "large_leap_ratio": 0,
        "max_large_leap_semitones": 0,

        "max_polyphony": 0,

        "duration_quarter_lengths": 0,
        "note_density": 0,
        "chord_ratio": 0,
        "tempo_bpm": "",
        "time_signatures": "",

        "key_signature_sharps": 0,
        "no_key_signature": True,

        "right_hand_single_notes": 0,
        "right_hand_chords": 0,
        "left_hand_single_notes": 0,
        "left_hand_chords": 0,
        "left_hand_melody_range_semitones": 0,
        "left_hand_is_melody": False,

        "selected": ""
    }

    score, error = load_score(file_path)

    if score is None:
        row["error"] = error
        return row

    row["parse_success"] = True

    # Metadata
    if score.metadata:
        row["title"] = score.metadata.title or ""
        row["composer"] = score.metadata.composer or ""

    parts = list(score.parts)
    row["num_parts"] = len(parts)

    # Right-hand / left-hand melodic roles
    row.update(get_hand_melody_metrics(parts))

    # Measures
    measures = list(score.recurse().getElementsByClass(stream.Measure))
    row["num_measures"] = len(measures)
    row["actual_measures"] = max(
        (
            len(list(part.recurse().getElementsByClass(stream.Measure)))
            for part in parts
        ),
        default=0
    )

    # Initial key signature
    key_signature_sharps = get_initial_key_signature(parts)
    row["key_signature_sharps"] = key_signature_sharps
    row["no_key_signature"] = key_signature_sharps == 0

    # Notes / chords / rests
    notes = list(score.recurse().notes)

    num_notes = 0
    num_chords = 0
    num_note_events = 0
    num_accidentals = 0

    for element in notes:
        if isinstance(element, note.Note):
            num_notes += 1
            num_note_events += 1

            if (
                element.pitch.accidental is not None
                and element.pitch.accidental.alter != 0
            ):
                num_accidentals += 1

        elif isinstance(element, chord.Chord):
            num_chords += 1
            num_notes += len(element.pitches)

            for pitch in element.pitches:
                if (
                    pitch.accidental is not None
                    and pitch.accidental.alter != 0
                ):
                    num_accidentals += 1

    row["num_notes"] = num_notes
    row["num_chords"] = num_chords
    row["num_accidentals"] = num_accidentals

    if num_notes > 0:
        row["accidental_ratio"] = num_accidentals / num_notes

    row["num_rest"] = len(
        list(score.recurse().getElementsByClass(note.Rest))
    )

    # Pitch range
    pitch_min, pitch_max, pitch_range = get_pitch_range(score)

    row["pitch_min"] = pitch_min or ""
    row["pitch_max"] = pitch_max or ""
    row["pitch_range_semitones"] = pitch_range

    # Large leaps
    total_events = 0
    total_large_leaps = 0
    all_leap_sizes = []

    for part in parts:
        note_sequences = get_note_sequences(part)

        for events in note_sequences:
            total_events += max(0, len(events) - 1)

            leap_count, leap_sizes = count_large_leaps(
                events,
                threshold_semitones=large_leap_threshold
            )

            total_large_leaps += leap_count
            all_leap_sizes.extend(leap_sizes)

    row["num_large_leaps"] = total_large_leaps

    if total_events > 0:
        row["large_leap_ratio"] = total_large_leaps / total_events

    if all_leap_sizes:
        row["max_large_leap_semitones"] = max(all_leap_sizes)

    # Polyphony
    row["max_polyphony"] = calculate_polyphony(score)

    # Duration
    row["duration_quarter_lengths"] = estimate_duration(score)

    if row["duration_quarter_lengths"] > 0:
        row["note_density"] = (
            row["num_notes"] / row["duration_quarter_lengths"]
        )

    total_note_chord_events = num_note_events + num_chords

    if total_note_chord_events > 0:
        row["chord_ratio"] = num_chords / total_note_chord_events

    # Tempo
    bpm = get_tempo(score)

    if bpm is not None:
        row["tempo_bpm"] = bpm

    # Time signatures
    row["time_signatures"] = get_time_signatures(score)

    return row


def analyze_folder(
    input_folder,
    output_csv="scoresight_musicxml_analysis.csv",
    large_leap_threshold=10
):
    files = []

    for root, dirs, filenames in os.walk(input_folder):
        for filename in filenames:
            path = Path(root) / filename

            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                files.append(path)

    print(f"Found {len(files)} MusicXML files.")

    results = []

    for i, file_path in enumerate(files, start=1):

        print(
            f"[{i}/{len(files)}] "
            f"Analyzing: {file_path.name}"
        )

        row = analyze_score(
            file_path,
            large_leap_threshold=large_leap_threshold
        )

        if meets_filter_criteria(row):
            row["selected"] = True
            results.append(row)

    fieldnames = CSV_FIELDNAMES

    with open(
        output_csv,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames
        )

        writer.writeheader()
        writer.writerows(results)

    print()
    print(f"Total files analyzed: {len(files)}")
    print(f"Files kept: {len(results)}")
    print(f"CSV saved to: {output_csv}")


if __name__ == "__main__":

    INPUT_FOLDER = "./candidate_musicxml_full_bothhands"

    OUTPUT_CSV = "scoresight_musicxml_analysis.csv"

    analyze_folder(
        INPUT_FOLDER,
        OUTPUT_CSV,
        large_leap_threshold=10
    )
