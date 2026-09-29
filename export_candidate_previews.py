import csv
from pathlib import Path

from export_previews import clear_output_folder, export_first_page


INPUT_FOLDER = Path("./Candidate").resolve()
OUTPUT_FOLDER = Path("./candidate_previews").resolve()
MUSESCORE_EXE = Path(r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe")

SUPPORTED_EXTENSIONS = {".musicxml", ".xml", ".mxl", ".mscz"}


def find_candidate_scores(input_folder):
    return sorted(
        path
        for path in input_folder.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def main():
    if not INPUT_FOLDER.exists():
        print(f"Candidate folder does not exist: {INPUT_FOLDER}")
        return

    if not MUSESCORE_EXE.exists():
        print(f"MuseScore executable not found: {MUSESCORE_EXE}")
        return

    files = find_candidate_scores(INPUT_FOLDER)
    print(f"Found {len(files)} Candidate score files.")

    clear_output_folder(OUTPUT_FOLDER)
    print(f"Cleared previous Candidate previews: {OUTPUT_FOLDER}")

    log_rows = []

    for i, score_file in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] Exporting preview: {score_file.name}")

        preview_path, error = export_first_page(
            score_file=score_file,
            input_root=INPUT_FOLDER,
            output_root=OUTPUT_FOLDER,
            musescore_exe=MUSESCORE_EXE
        )

        log_rows.append({
            "input_file": str(score_file),
            "preview_file": str(preview_path) if preview_path else "",
            "success": error is None,
            "error": error or ""
        })

    log_csv = OUTPUT_FOLDER / "candidate_preview_export_log.csv"

    with open(
        log_csv,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "input_file",
                "preview_file",
                "success",
                "error"
            ]
        )
        writer.writeheader()
        writer.writerows(log_rows)

    successful_exports = sum(
        row["success"]
        for row in log_rows
    )

    print()
    print("Done.")
    print(f"Candidate scores attempted: {len(files)}")
    print(f"Previews exported: {successful_exports}")
    print(f"Export failures: {len(files) - successful_exports}")
    print(f"Preview folder: {OUTPUT_FOLDER}")
    print(f"Log file: {log_csv}")


if __name__ == "__main__":
    main()
