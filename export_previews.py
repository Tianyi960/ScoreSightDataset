import os
import csv
import shutil
import subprocess
from pathlib import Path

# =========================
# 需要你改的四个地方
# =========================

# analyze_scores.py 生成的筛选结果
ANALYSIS_CSV = Path(
    r"C:\Users\86138\Desktop\ScoreSightDataset\scoresight_musicxml_analysis.csv")

# 你的谱子文件夹
INPUT_FOLDER = Path(
    r"C:\Users\86138\Desktop\ScoreSightDataset\candidate_musicxml_full_bothhands")

# 导出预览图的文件夹
OUTPUT_FOLDER = Path(
    r"C:\Users\86138\Desktop\ScoreSightDataset\previews\selected")

# MuseScore 可执行文件路径
# 你需要根据自己电脑实际安装位置修改
MUSESCORE_EXE = Path(r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe")

# 支持的文件类型
SUPPORTED_EXTENSIONS = {".musicxml", ".xml", ".mxl", ".mscz"}


def load_score_files(analysis_csv: Path):
    """
    从 analyze_scores.py 生成的 CSV 中读取筛选后保留的谱子。
    """
    files = []
    seen = set()

    with open(analysis_csv, "r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)

        if not reader.fieldnames or "filepath" not in reader.fieldnames:
            raise ValueError("Analysis CSV does not contain a filepath column")

        for row in reader:
            selected = row.get("selected", "").strip().lower()

            if selected and selected not in {"true", "1", "yes"}:
                continue

            filepath = row.get("filepath", "").strip()

            if not filepath:
                continue

            path = Path(filepath)

            if not path.is_absolute():
                path = analysis_csv.parent / path

            path = path.resolve()

            if path.suffix.lower() in SUPPORTED_EXTENSIONS and path not in seen:
                files.append(path)
                seen.add(path)

    return files


def safe_unlink(path: Path):
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def clear_output_folder(output_folder: Path):
    """
    清空上一轮导出结果，确保输出目录只包含当前 CSV 的预览。
    """
    output_folder.mkdir(parents=True, exist_ok=True)

    for path in output_folder.iterdir():
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)


def export_first_page(score_file: Path, input_root: Path, output_root: Path, musescore_exe: Path):
    """
    用 MuseScore 导出 PNG，并只保留第一页。
    返回:
        preview_path, error_message
    """
    try:
        # 保留输入文件夹的子目录结构
        relative_parent = score_file.parent.relative_to(input_root)
        target_dir = output_root / relative_parent
        target_dir.mkdir(parents=True, exist_ok=True)

        # MuseScore 导出基名
        # 比如 target_base = previews/subfolder/abc
        target_base = target_dir / score_file.stem

        # 先删除旧文件，避免混淆
        old_candidates = list(target_dir.glob(f"{score_file.stem}*.png"))
        for p in old_candidates:
            safe_unlink(p)

        # MuseScore 导出命令
        # 输出 PNG 时，若有多页，通常会生成 xxx-1.png, xxx-2.png...
        output_png = target_base.with_suffix(".png")

        cmd = [
            str(musescore_exe),
            str(score_file),
            "-o",
            str(output_png)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            err = result.stderr.strip() or result.stdout.strip(
            ) or "Unknown MuseScore export error"
            return None, err

        # 找导出的 PNG
        candidates = []

        # 常见情况：多页输出为 xxx-1.png, xxx-2.png...
        candidates.extend(sorted(target_dir.glob(f"{score_file.stem}-*.png")))

        # 有些情况下也可能直接输出 xxx.png
        direct_png = target_base.with_suffix(".png")
        if direct_png.exists():
            candidates.insert(0, direct_png)

        # 去重
        seen = set()
        unique_candidates = []
        for c in candidates:
            if c not in seen and c.exists():
                unique_candidates.append(c)
                seen.add(c)

        if not unique_candidates:
            return None, "No PNG output found after MuseScore export"

        # 选第一页
        first_page = None

        # 优先找 -1.png
        page1 = target_dir / f"{score_file.stem}-1.png"
        if page1.exists():
            first_page = page1
        else:
            # 否则取第一个
            first_page = unique_candidates[0]

        # 最终统一命名为 xxx_preview.png
        final_preview = target_dir / f"{score_file.stem}_preview.png"

        # 如果 final_preview 已存在，先删
        safe_unlink(final_preview)

        shutil.move(str(first_page), str(final_preview))

        # 删除其余页
        for c in unique_candidates:
            if c.exists() and c != final_preview:
                safe_unlink(c)

        return final_preview, None

    except subprocess.TimeoutExpired:
        return None, "MuseScore export timed out"
    except Exception as e:
        return None, str(e)


def main():
    if not ANALYSIS_CSV.exists():
        print(f"Analysis CSV does not exist: {ANALYSIS_CSV}")
        return

    if not INPUT_FOLDER.exists():
        print(f"Input folder does not exist: {INPUT_FOLDER}")
        return

    if not MUSESCORE_EXE.exists():
        print(f"MuseScore executable not found: {MUSESCORE_EXE}")
        return

    try:
        files = load_score_files(ANALYSIS_CSV)
    except Exception as e:
        print(f"Could not read analysis CSV: {e}")
        return

    print(f"Found {len(files)} selected score files in the analysis CSV.")

    clear_output_folder(OUTPUT_FOLDER)
    print(f"Cleared previous preview files: {OUTPUT_FOLDER}")

    log_rows = []

    for i, score_file in enumerate(files[:100], start=1):
        print(f"[{i}/{len(files)}] Exporting preview: {score_file.name}")

        if not score_file.exists():
            preview_path = None
            error = "Score file not found"
        else:
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

    log_csv = OUTPUT_FOLDER / "preview_export_log.csv"
    with open(log_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["input_file", "preview_file", "success", "error"]
        )
        writer.writeheader()
        writer.writerows(log_rows)

    print()
    print("Done.")
    print(f"Total selected scores: {len(files)}")
    print(f"Previews exported: {sum(row['success'] for row in log_rows)}")
    print(f"Export failures: {sum(not row['success'] for row in log_rows)}")
    print(f"Preview folder: {OUTPUT_FOLDER}")
    print(f"Log file: {log_csv}")


if __name__ == "__main__":
    main()
