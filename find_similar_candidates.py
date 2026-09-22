import csv
import itertools
import math
import os
import statistics
from pathlib import Path

os.environ.setdefault(
    "MPLCONFIGDIR",
    str(Path("./.matplotlib_cache").resolve())
)

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from analyze_scores import analyze_score, CSV_FIELDNAMES


CANDIDATE_FOLDER = Path("./Candidate")
OUTPUT_CSV = "candidate_top10_similar.csv"
OUTPUT_PLOT = "candidate_similarity_plot.png"
NUMBER_TO_SELECT = 10
LARGE_LEAP_THRESHOLD = 10

SUPPORTED_EXTENSIONS = {".musicxml", ".xml", ".mxl"}

SIMILARITY_FIELDS = [
    "note_density",
    "right_hand_single_notes",
    "left_hand_single_notes",
    "num_large_leaps",
    "large_leap_ratio",
    "max_large_leap_semitones",
    "num_chords",
    "chord_ratio"
]


def analyze_candidate_folder(candidate_folder):
    files = sorted(
        path
        for path in candidate_folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    rows = []

    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] Analyzing: {file_path.name}")

        row = analyze_score(
            file_path,
            large_leap_threshold=LARGE_LEAP_THRESHOLD
        )

        if row["parse_success"]:
            rows.append(row)
        else:
            print(f"  Parse failed: {row['error']}")

    return rows


def standardize_features(rows):
    means = {}
    standard_deviations = {}

    for field in SIMILARITY_FIELDS:
        values = [float(row[field]) for row in rows]
        means[field] = statistics.mean(values)
        standard_deviations[field] = statistics.pstdev(values)

    vectors = []

    for row in rows:
        vector = []

        for field in SIMILARITY_FIELDS:
            standard_deviation = standard_deviations[field]

            if standard_deviation == 0:
                value = 0
            else:
                value = (
                    float(row[field]) - means[field]
                ) / standard_deviation

            vector.append(value)

        vectors.append(vector)

    return vectors


def squared_distance(vector_a, vector_b):
    return sum(
        (value_a - value_b) ** 2
        for value_a, value_b in zip(vector_a, vector_b)
    )


def average_pairwise_distance(indices, vectors):
    distances = []

    for index_a, index_b in itertools.combinations(indices, 2):
        distances.append(
            squared_distance(vectors[index_a], vectors[index_b])
        )

    if not distances:
        return 0

    return math.sqrt(sum(distances) / len(distances))


def find_most_similar_group(rows, number_to_select):
    if len(rows) < number_to_select:
        raise ValueError(
            f"Only {len(rows)} scores were parsed successfully, "
            f"but {number_to_select} are required."
        )

    vectors = standardize_features(rows)
    best_indices = None
    best_distance = None

    for indices in itertools.combinations(
        range(len(rows)),
        number_to_select
    ):
        distance = average_pairwise_distance(indices, vectors)

        if best_distance is None or distance < best_distance:
            best_indices = indices
            best_distance = distance

    selected_rows = [rows[index] for index in best_indices]
    selected_rows.sort(key=lambda row: row["filename"].lower())

    for row in selected_rows:
        row["selected"] = True

    return selected_rows, best_distance


def make_short_label(filename):
    stem = Path(filename).stem
    parts = stem.split("__")
    title = parts[0].replace("_", " ")
    measure_range = next(
        (part for part in parts if part.startswith("m")),
        ""
    )

    if len(title) > 24:
        title = f"{title[:23]}…"

    return f"{title} {measure_range}".strip()


def create_similarity_plot(rows, selected_rows, output_plot):
    vectors = np.array(
        standardize_features(rows),
        dtype=float
    )

    # PCA using singular value decomposition, without an sklearn dependency.
    centered_vectors = vectors - vectors.mean(axis=0)
    _, singular_values, components = np.linalg.svd(
        centered_vectors,
        full_matrices=False
    )
    coordinates = centered_vectors @ components[:2].T

    explained_variance = singular_values ** 2
    explained_variance_ratio = (
        explained_variance / explained_variance.sum()
        if explained_variance.sum() > 0
        else np.zeros_like(explained_variance)
    )

    selected_filenames = {
        row["filename"]
        for row in selected_rows
    }
    selected_indices = [
        index
        for index, row in enumerate(rows)
        if row["filename"] in selected_filenames
    ]
    excluded_indices = [
        index
        for index, row in enumerate(rows)
        if row["filename"] not in selected_filenames
    ]

    figure, axes = plt.subplots(
        1,
        2,
        figsize=(20, 11),
        gridspec_kw={"width_ratios": [1.05, 1.45]}
    )

    scatter_axis = axes[0]

    if excluded_indices:
        scatter_axis.scatter(
            coordinates[excluded_indices, 0],
            coordinates[excluded_indices, 1],
            color="#a9adb5",
            edgecolor="white",
            linewidth=0.8,
            s=90,
            label="Not selected",
            zorder=2
        )

    scatter_axis.scatter(
        coordinates[selected_indices, 0],
        coordinates[selected_indices, 1],
        color="#2676d9",
        edgecolor="white",
        linewidth=1,
        s=115,
        label="Selected top 10",
        zorder=3
    )

    for index, row in enumerate(rows):
        scatter_axis.annotate(
            make_short_label(row["filename"]),
            (coordinates[index, 0], coordinates[index, 1]),
            xytext=(5, 5),
            textcoords="offset points",
            fontsize=7.5,
            color=(
                "#174a8b"
                if index in selected_indices
                else "#666a73"
            )
        )

    scatter_axis.axhline(0, color="#dddddd", linewidth=0.8, zorder=1)
    scatter_axis.axvline(0, color="#dddddd", linewidth=0.8, zorder=1)
    scatter_axis.set_title("Candidate Similarity Map (PCA)", fontsize=15)
    scatter_axis.set_xlabel(
        f"PC1 ({explained_variance_ratio[0] * 100:.1f}% variance)"
    )
    scatter_axis.set_ylabel(
        f"PC2 ({explained_variance_ratio[1] * 100:.1f}% variance)"
    )
    scatter_axis.legend(loc="best")
    scatter_axis.grid(alpha=0.18)

    heatmap_axis = axes[1]
    selected_vectors = vectors[selected_indices]
    selected_labels = [
        make_short_label(rows[index]["filename"])
        for index in selected_indices
    ]
    feature_labels = [
        "Note density",
        "RH single notes",
        "LH single notes",
        "Large-leap count",
        "Large-leap ratio",
        "Maximum leap",
        "Chord count",
        "Chord ratio"
    ]
    maximum_absolute_value = max(
        1,
        float(np.abs(selected_vectors).max())
    )

    image = heatmap_axis.imshow(
        selected_vectors,
        cmap="coolwarm",
        aspect="auto",
        vmin=-maximum_absolute_value,
        vmax=maximum_absolute_value
    )
    heatmap_axis.set_title(
        "Selected Top 10 — Standardized Features",
        fontsize=15
    )
    heatmap_axis.set_xticks(range(len(feature_labels)))
    heatmap_axis.set_xticklabels(
        feature_labels,
        rotation=38,
        ha="right"
    )
    heatmap_axis.set_yticks(range(len(selected_labels)))
    heatmap_axis.set_yticklabels(selected_labels, fontsize=8)

    for row_index in range(selected_vectors.shape[0]):
        for column_index in range(selected_vectors.shape[1]):
            value = selected_vectors[row_index, column_index]
            heatmap_axis.text(
                column_index,
                row_index,
                f"{value:.1f}",
                ha="center",
                va="center",
                fontsize=7,
                color=(
                    "white"
                    if abs(value) > maximum_absolute_value * 0.52
                    else "#222222"
                )
            )

    colorbar = figure.colorbar(
        image,
        ax=heatmap_axis,
        fraction=0.035,
        pad=0.02
    )
    colorbar.set_label(
        "Standard deviations from the 17-score mean"
    )

    figure.suptitle(
        "Most Similar 10 Scores in the Candidate Folder",
        fontsize=18,
        fontweight="bold"
    )
    figure.tight_layout(rect=[0, 0, 1, 0.96])
    figure.savefig(output_plot, dpi=200, bbox_inches="tight")
    plt.close(figure)


def write_results(rows, output_csv):
    with open(
        output_csv,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=CSV_FIELDNAMES
        )
        writer.writeheader()
        writer.writerows(rows)


def main():
    if not CANDIDATE_FOLDER.exists():
        print(f"Candidate folder does not exist: {CANDIDATE_FOLDER}")
        return

    rows = analyze_candidate_folder(CANDIDATE_FOLDER)

    print()
    print(f"Candidate scores parsed successfully: {len(rows)}")

    selected_rows, average_distance = find_most_similar_group(
        rows,
        NUMBER_TO_SELECT
    )

    create_similarity_plot(
        rows,
        selected_rows,
        OUTPUT_PLOT
    )

    csv_saved = True

    try:
        write_results(selected_rows, OUTPUT_CSV)
    except PermissionError:
        csv_saved = False

    print(f"Most similar scores selected: {len(selected_rows)}")
    print(f"Group similarity distance: {average_distance:.6f}")
    print("Selected files:")

    for row in selected_rows:
        print(f"  {row['filename']}")

    if csv_saved:
        print(f"CSV saved to: {OUTPUT_CSV}")
    else:
        print(
            f"CSV could not be overwritten because it is open: "
            f"{OUTPUT_CSV}"
        )
    print(f"Plot saved to: {OUTPUT_PLOT}")


if __name__ == "__main__":
    main()
