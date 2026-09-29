import csv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
INPUT_CSV = BASE_DIR / "all_scores_feature_analysis.csv"
OUTPUT_CSV = BASE_DIR / "first_stage_filtered_scores.csv"

REQUIRED_COLUMNS = (
    "parse_success",
    "num_parts",
    "key_signature",
    "num_measures",
    "RH_note_count",
    "LH_note_count",
    "RH_rest_ratio",
    "LH_rest_ratio",
    "RH_note_density",
    "LH_note_density",
)


def is_true(value):
    """Accept a real bool or the CSV string representation of True."""
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() == "true"


def to_number(value):
    """Convert a CSV value to a number; invalid or missing values stay unusable."""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def row_matches(row, column_indices):
    try:
        parse_success = row[column_indices["parse_success"]]
        numeric_values = {
            column: to_number(row[column_indices[column]])
            for column in REQUIRED_COLUMNS
            if column != "parse_success"
        }
    except IndexError:
        return False

    if any(value is None for value in numeric_values.values()):
        return False

    return (
        is_true(parse_success)
        and numeric_values["num_parts"] == 2
        and numeric_values["key_signature"] == 0
        and numeric_values["num_measures"] == 8
        and 17 <= numeric_values["RH_note_count"] <= 61
        and 14 <= numeric_values["LH_note_count"] <= 57
        and numeric_values["RH_rest_ratio"] <= 0.3354
        and numeric_values["LH_rest_ratio"] <= 0.4375
        and 0.6563 <= numeric_values["RH_note_density"] <= 2.4000
        and 0.4688 <= numeric_values["LH_note_density"] <= 2.0000
    )


def filter_csv(input_csv=INPUT_CSV, output_csv=OUTPUT_CSV):
    input_csv = Path(input_csv).resolve()
    output_csv = Path(output_csv).resolve()

    if input_csv == output_csv:
        raise ValueError("Input and output CSV paths must be different.")

    with input_csv.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"Input CSV is empty: {input_csv}")

        missing = [column for column in REQUIRED_COLUMNS if column not in header]
        if missing:
            raise ValueError(f"Missing required columns: {', '.join(missing)}")

        column_indices = {
            column: header.index(column) for column in REQUIRED_COLUMNS
        }

        original_count = 0
        filtered_rows = []
        for row in reader:
            original_count += 1
            if row_matches(row, column_indices):
                filtered_rows.append(row)

    with output_csv.open("w", newline="", encoding="utf-8-sig") as destination:
        writer = csv.writer(destination)
        writer.writerow(header)
        writer.writerows(filtered_rows)

    print(f"原始总行数: {original_count}")
    print(f"最终行数: {len(filtered_rows)}")
    print(f"最终 CSV 保存路径: {output_csv}")


def main():
    filter_csv()


if __name__ == "__main__":
    main()
