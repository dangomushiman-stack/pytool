from __future__ import annotations

import csv
import json
import threading
import os
import sys
import datetime
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# サムネイル表示用（任意）
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")

# 設定ファイル（最後に使ったフォルダAの保存先）
CONFIG_PATH = Path.home() / ".video_browser_gui.json"


# ------------------ 設定ファイルの読み書き ------------------ #
def load_last_root() -> Optional[str]:
    """前回使ったフォルダAのパスを設定ファイルから読み込む。"""
    try:
        if CONFIG_PATH.exists():
            with CONFIG_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
            root = data.get("last_root")
            if isinstance(root, str) and root:
                return root
    except Exception:
        pass
    return None


def save_last_root(path: Path) -> None:
    """フォルダAのパスを設定ファイルに保存する。"""
    try:
        data = {"last_root": str(path)}
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        # 保存失敗しても致命的ではないので無視
        pass


# ------------------ タグファイルの読み書き ------------------ #
def load_tags_file(dirpath: Path) -> Optional[str]:
    """
    各フォルダ内の tags.json からタグを読み込む。
    形式: {"tags": ["tag1", "tag2", ...]}
    表示用には "tag1, tag2" のような文字列で返す。
    """
    path = dirpath / "tags.json"
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        tags = data.get("tags")
        if isinstance(tags, list):
            return ", ".join(str(t) for t in tags)
        if isinstance(tags, str):
            return tags
    except Exception:
        pass
    return None


def save_tags_file(dirpath: Path, tags_str: str) -> None:
    """
    タグの文字列（カンマ区切り）を tags.json に保存する。
    "tag1, tag2" -> {"tags": ["tag1", "tag2"]}
    """
    tags_list: List[str] = []
    for t in tags_str.split(","):
        t = t.strip()
        if t:
            tags_list.append(t)
    data = {"tags": tags_list}
    path = dirpath / "tags.json"
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ------------------ データ構造 ------------------ #
@dataclass
class VideoRow:
    folder: str
    has_video_info: bool
    title: Optional[str]
    tags: Optional[str]          # 表示用タグ文字列 "tag1, tag2"
    video_id: Optional[str]
    duration: Optional[str]
    best_height: Optional[int]
    thumbnail: Optional[str]
    uploader: Optional[str]
    upload_date: Optional[str]
    webpage_url: Optional[str]
    info_timestamp: Optional[str]   # video_info.json の更新日時 "YYYY-MM-DD HH:MM:SS"


# ------------------ ユーティリティ ------------------ #
def hhmmss(total_seconds: Optional[float]) -> Optional[str]:
    if total_seconds is None:
        return None
    try:
        s = int(round(float(total_seconds)))
    except (TypeError, ValueError):
        return None
    h, r = divmod(s, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def pick_thumbnail(dirpath: Path) -> Optional[Path]:
    """フォルダ内のサムネ画像を1つ選ぶ。"""
    priority = ["thumbnail", "thumb", "cover", "poster"]
    files = [p for p in dirpath.iterdir()
             if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    if not files:
        return None

    # 優先名前から探す
    for base in priority:
        for f in files:
            if f.stem.lower().startswith(base):
                return f

    # なければ更新日時が新しいもの
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def parse_video_info(info: Dict[str, Any]) -> Dict[str, Any]:
    """video_info.json（yt-dlp想定）から必要情報を抽出。"""
    vid = {
        "video_id": info.get("id"),
        "title": info.get("title"),
        "duration_sec": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel"),
        "upload_date": info.get("upload_date"),
        "webpage_url": info.get("webpage_url"),
    }

    # 最大height（画質の目安）
    best_h = None
    best_fmt = None
    for f in info.get("formats", []) or []:
        h = f.get("height")
        if isinstance(h, int) or (isinstance(h, float) and not isinstance(h, bool)):
            if best_h is None or h > best_h:
                best_h = int(h)
                best_fmt = f.get("format_id")
    vid["best_height"] = best_h
    vid["best_format_id"] = best_fmt
    return vid


def load_video_info(json_path: Path) -> Optional[Dict[str, Any]]:
    try:
        with json_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def collect_rows(root: Path, on_progress=None) -> List[VideoRow]:
    """フォルダA直下の各フォルダから VideoRow を作る。"""
    rows: List[VideoRow] = []
    children = [c for c in sorted(root.iterdir()) if c.is_dir()]
    total = len(children)

    for idx, child in enumerate(children, start=1):
        info_path = child / "video_info.json"
        info_raw = load_video_info(info_path)

        # JSONファイルの更新日時
        timestamp = None
        if info_path.exists():
            t = info_path.stat().st_mtime
            timestamp = datetime.datetime.fromtimestamp(t).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        if info_raw:
            parsed = parse_video_info(info_raw)
            duration = hhmmss(parsed.get("duration_sec"))
            has_info = True
            title = parsed.get("title")
            video_id = parsed.get("video_id")
            best_height = parsed.get("best_height")
            uploader = parsed.get("uploader")
            upload_date = parsed.get("upload_date")
            webpage_url = parsed.get("webpage_url")
        else:
            has_info = False
            title = None
            video_id = None
            duration = None
            best_height = None
            uploader = None
            upload_date = None
            webpage_url = None

        thumb = pick_thumbnail(child)

        # タグ読み込み
        tags_str = load_tags_file(child)

        rows.append(
            VideoRow(
                folder=child.name,
                has_video_info=has_info,
                title=title,
                tags=tags_str,
                video_id=video_id,
                duration=duration,
                best_height=best_height,
                thumbnail=str(thumb) if thumb else None,
                uploader=uploader,
                upload_date=upload_date,
                webpage_url=webpage_url,
                info_timestamp=timestamp,
            )
        )

        if on_progress:
            on_progress(idx, total)

    return rows


# ------------------ GUI ------------------ #
class VideoBrowserGUI(ttk.Frame):
    # Tree の列キー
    COLUMNS = (
        "folder",
        "title",
        "tags",
        "video_id",
        "duration",
        "best_height",
        "uploader",
        "upload_date",
        "webpage_url",
        "info_timestamp",
    )

    # 検索対象として使う内部キー
    SEARCHABLE_COLUMNS = (
        "folder",
        "title",
        "tags",
        "video_id",
        "uploader",
        "upload_date",
        "webpage_url",
        "info_timestamp",
    )

    # GUIに表示する日本語 → 内部キー の対応
    SEARCH_LABEL_MAP = {
        "フォルダ": "folder",
        "タイトル": "title",
        "タグ": "tags",
        "動画ID": "video_id",
        "投稿者": "uploader",
        "投稿日": "upload_date",
        "URL": "webpage_url",
        "JSON更新日時": "info_timestamp",
    }

    def __init__(self, master: tk.Tk):
        super().__init__(master)
        self.master.title("Video Browser (フォルダA一覧)")
        self.pack(fill=tk.BOTH, expand=True)

        self.root_dir: Optional[Path] = None
        self.rows: List[VideoRow] = []       # 全件
        self.view_rows: List[VideoRow] = []  # フィルタ・ソート後の表示用

        self._thumb_cache: Dict[str, Any] = {}
        self._sort_state: Dict[str, bool] = {}  # 列ごとの昇順/降順

        # 先に前回のフォルダを読み込んでおく
        self._initial_root = load_last_root()

        self._build_widgets()

    # ---------- UI構築 ---------- #
    def _build_widgets(self):
        # === 上部バー（フォルダ選択など） ===
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8, pady=(8, 4))

        self.root_var = tk.StringVar()
        ttk.Label(bar, text="フォルダA:").pack(side=tk.LEFT)
        entry = ttk.Entry(bar, textvariable=self.root_var, width=60)
        entry.pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="参照…", command=self.choose_root).pack(side=tk.LEFT)

        self.scan_btn = ttk.Button(bar, text="スキャン", command=self.scan)
        self.scan_btn.pack(side=tk.LEFT, padx=6)

        self.export_csv_btn = ttk.Button(
            bar, text="CSV出力", command=self.export_csv, state=tk.DISABLED
        )
        self.export_csv_btn.pack(side=tk.LEFT)

        self.export_json_btn = ttk.Button(
            bar, text="JSON出力", command=self.export_json, state=tk.DISABLED
        )
        self.export_json_btn.pack(side=tk.LEFT, padx=4)

        # 前回のフォルダAがあれば自動セット
        if self._initial_root:
            self.root_var.set(self._initial_root)

        # === 検索バー ===
        sbar = ttk.Frame(self)
        sbar.pack(fill=tk.X, padx=8, pady=(0, 8))

        ttk.Label(sbar, text="検索:").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_entry = ttk.Entry(sbar, textvariable=self.search_var, width=40)
        self.search_entry.pack(side=tk.LEFT, padx=6)
        # 入力のたびに絞り込み
        self.search_entry.bind("<KeyRelease>", lambda e: self.apply_filter())

        ttk.Label(sbar, text="対象:").pack(side=tk.LEFT, padx=(10, 0))
        self.search_target = ttk.Combobox(
            sbar,
            values=["すべて"] + list(self.SEARCH_LABEL_MAP.keys()),
            width=14,
            state="readonly",
        )
        self.search_target.current(0)
        self.search_target.pack(side=tk.LEFT)
        self.search_target.bind("<<ComboboxSelected>>", lambda e: self.apply_filter())

        ttk.Button(sbar, text="クリア", command=self.clear_filter).pack(
            side=tk.LEFT, padx=6
        )

        # プログレスバー
        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill=tk.X, padx=8)

        # === スクロール付き Treeview ===
        columns = self.COLUMNS
        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        self.tree = ttk.Treeview(
            tree_frame, columns=columns, show="headings", height=14
        )

        # スクロールバー
        vsb = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        headings = {
            "folder": "フォルダ",
            "title": "タイトル",
            "tags": "タグ",
            "video_id": "動画ID",
            "duration": "長さ",
            "best_height": "最大高さ(px)",
            "uploader": "投稿者",
            "upload_date": "投稿日",
            "webpage_url": "URL",
            "info_timestamp": "JSON更新日時",
        }
        widths = {
            "folder": 120,
            "title": 240,
            "tags": 200,
            "video_id": 120,
            "duration": 80,
            "best_height": 110,
            "uploader": 160,
            "upload_date": 100,
            "webpage_url": 220,
            "info_timestamp": 150,
        }

        for cid in columns:
            self.tree.heading(
                cid,
                text=headings.get(cid, cid),
                command=lambda c=cid: self.sort_by_column(c),
            )
            self.tree.column(cid, width=widths.get(cid, 120), anchor=tk.W)

        # === 詳細エリア ===
        detail = ttk.LabelFrame(self, text="詳細 / サムネイル")
        detail.pack(fill=tk.X, padx=8, pady=(0, 8))

        self.thumb_label = ttk.Label(detail)
        self.thumb_label.pack(side=tk.LEFT, padx=8, pady=8)

        info_frame = ttk.Frame(detail)
        info_frame.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=8)

        self.info_text = tk.Text(info_frame, height=6, wrap=tk.WORD)
        self.info_text.pack(fill=tk.X, padx=8, pady=(0, 4))

        # タグ編集行
        tag_frame = ttk.Frame(info_frame)
        tag_frame.pack(fill=tk.X, padx=8, pady=(2, 4))
        ttk.Label(tag_frame, text="タグ (カンマ区切り):").pack(side=tk.LEFT)
        self.tag_var = tk.StringVar()
        self.tag_entry = ttk.Entry(tag_frame, textvariable=self.tag_var, width=50)
        self.tag_entry.pack(side=tk.LEFT, padx=4)
        ttk.Button(tag_frame, text="タグ保存", command=self.save_tag_for_selected).pack(
            side=tk.LEFT
        )

        self.open_folder_btn = ttk.Button(
            detail, text="フォルダを開く",
            command=self.open_selected_folder,
            state=tk.DISABLED,
        )
        self.open_folder_btn.pack(side=tk.RIGHT, padx=8)

        # 選択イベント
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    # ---------- 共通ヘルパー ---------- #
    def _row_to_values(self, r: VideoRow):
        return (
            r.folder,
            r.title or "",
            r.tags or "",
            r.video_id or "",
            r.duration or "",
            r.best_height if r.best_height is not None else "",
            r.uploader or "",
            r.upload_date or "",
            r.webpage_url or "",
            r.info_timestamp or "",
        )

    def refresh_tree(self):
        """view_rows の内容で Treeview を再描画。"""
        self.tree.delete(*self.tree.get_children())
        for r in self.view_rows:
            self.tree.insert("", tk.END, iid=r.folder, values=self._row_to_values(r))

    # ---------- フィルタ（検索）関連 ---------- #
    def apply_filter(self):
        query = (self.search_var.get() or "").strip().lower()
        target_label = self.search_target.get()

        if not query:
            # 検索文字列が空なら全件表示（現在の self.rows の並びを尊重）
            self.view_rows = list(self.rows)
            self.refresh_tree()
            return

        # 対象列の内部キー（見つからなければ ALL 扱い）
        target = self.SEARCH_LABEL_MAP.get(target_label)

        def match(r: VideoRow) -> bool:
            def field_text(name: str) -> str:
                v = getattr(r, name, "")
                return ("" if v is None else str(v)).lower()

            # 「すべて」のときは SEARCHABLE_COLUMNS 全部
            if target_label == "すべて" or target is None:
                for name in self.SEARCHABLE_COLUMNS:
                    if query in field_text(name):
                        return True
                return False
            else:
                return query in field_text(target)

        self.view_rows = [r for r in self.rows if match(r)]
        self.refresh_tree()

    def clear_filter(self):
        """検索条件をクリアして、JSON更新日時の新しい順に戻す。"""
        self.search_var.set("")
        self.search_target.current(0)

        # クリア時も JSON更新日時で降順ソート（新しいものが上）
        self.rows.sort(key=lambda r: r.info_timestamp or "", reverse=True)

        self.view_rows = list(self.rows)
        self.refresh_tree()

    # ---------- ソート ---------- #
    def sort_by_column(self, col: str):
        if not self.view_rows:
            return
        reverse = self._sort_state.get(col, False)
        self._sort_state[col] = not reverse

        def sort_key(r: VideoRow):
            val = getattr(r, col, None)
            if val is None:
                return ""
            # 数値っぽい列は数値でソート
            if col in ("best_height",):
                try:
                    return int(val)
                except Exception:
                    return -1
            return str(val).lower()

        self.view_rows.sort(key=sort_key, reverse=reverse)
        self.refresh_tree()

    # ---------- アクション ---------- #
    def choose_root(self):
        path = filedialog.askdirectory(title="フォルダAを選択")
        if path:
            self.root_var.set(path)

    def set_progress(self, value: int, maximum: int):
        self.progress["maximum"] = max(1, maximum)
        self.progress["value"] = value
        self.update_idletasks()

    def scan(self):
        root_path = Path(self.root_var.get()).expanduser()
        if not root_path.exists() or not root_path.is_dir():
            messagebox.showerror("エラー", f"フォルダが見つかりません: {root_path}")
            return

        self.scan_btn.configure(state=tk.DISABLED)
        self.export_csv_btn.configure(state=tk.DISABLED)
        self.export_json_btn.configure(state=tk.DISABLED)
        self.tree.delete(*self.tree.get_children())
        self.rows = []
        self.view_rows = []
        self.set_progress(0, 1)

        def _worker():
            try:
                def _on_progress(i, total):
                    self.master.after(
                        0, lambda: self.set_progress(i, total)
                    )

                rows = collect_rows(root_path, on_progress=_on_progress)
            except Exception as e:
                self.master.after(
                    0, lambda: messagebox.showerror("スキャン失敗", str(e))
                )
                rows = []

            self.master.after(0, lambda: self._finish_scan(root_path, rows))

        threading.Thread(target=_worker, daemon=True).start()

    def _finish_scan(self, root_path: Path, rows: List[VideoRow]):
        self.root_dir = root_path

        # ★ 初回スキャン時：JSON更新日時で新しい順（降順）にソート
        rows.sort(key=lambda r: r.info_timestamp or "", reverse=True)

        self.rows = rows
        self.view_rows = list(rows)
        self.refresh_tree()

        # 成功したフォルダAを保存
        save_last_root(root_path)

        self.set_progress(len(rows), max(1, len(rows)))
        self.scan_btn.configure(state=tk.NORMAL)
        state = tk.NORMAL if rows else tk.DISABLED
        self.export_csv_btn.configure(state=state)
        self.export_json_btn.configure(state=state)

    def on_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        row = next((r for r in self.rows if r.folder == iid), None)
        if not row:
            return

        self.info_text.delete("1.0", tk.END)
        self.info_text.insert(
            tk.END,
            (
                f"フォルダ: {row.folder}\n"
                f"タイトル: {row.title or ''}\n"
                f"タグ: {row.tags or ''}\n"
                f"動画ID: {row.video_id or ''}\n"
                f"長さ: {row.duration or ''}\n"
                f"最大高さ(px): {row.best_height or ''}\n"
                f"投稿者: {row.uploader or ''}\n"
                f"投稿日: {row.upload_date or ''}\n"
                f"URL: {row.webpage_url or ''}\n"
                f"JSON更新日時: {row.info_timestamp or ''}\n"
                f"サムネ: {row.thumbnail or ''}\n"
            ),
        )

        self.tag_var.set(row.tags or "")
        self.show_thumbnail(row.thumbnail)
        self.open_folder_btn.configure(state=tk.NORMAL)

    def show_thumbnail(self, path_str: Optional[str]):
        if not PIL_AVAILABLE:
            self.thumb_label.configure(text="(Pillow未インストールのためプレビュー不可)")
            self._thumb_cache.clear()
            return

        if not path_str:
            self.thumb_label.configure(text="(サムネなし)")
            self._thumb_cache.clear()
            return

        p = Path(path_str)
        if not p.exists():
            self.thumb_label.configure(text="(画像ファイルが見つかりません)")
            self._thumb_cache.clear()
            return

        try:
            img = Image.open(p)
            img.thumbnail((240, 240))
            tkimg = ImageTk.PhotoImage(img)
            self.thumb_label.configure(image=tkimg, text="")
            self._thumb_cache["thumb"] = tkimg
        except Exception as e:
            self.thumb_label.configure(text=f"(表示失敗: {e})")
            self._thumb_cache.clear()

    def save_tag_for_selected(self):
        """現在選択中のフォルダにタグを保存する。"""
        sel = self.tree.selection()
        if not sel or not self.root_dir:
            messagebox.showwarning("警告", "フォルダが選択されていません。")
            return
        folder_name = sel[0]
        target_dir = self.root_dir / folder_name
        tags_str = self.tag_var.get().strip()

        try:
            save_tags_file(target_dir, tags_str)
        except Exception as e:
            messagebox.showerror("保存失敗", f"タグの保存に失敗しました:\n{e}")
            return

        # rows / view_rows を更新
        for r in self.rows:
            if r.folder == folder_name:
                r.tags = tags_str
                break

        self.refresh_tree()
        messagebox.showinfo("完了", "タグを保存しました。")

    def export_csv(self):
        if not self.rows:
            return

        dst = filedialog.asksaveasfilename(
            title="CSVに保存",
            defaultextension=".csv",
            filetypes=[("CSV", ".csv")],
        )
        if not dst:
            return

        keys = list(VideoRow.__annotations__.keys())
        try:
            with open(dst, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=keys)
                writer.writeheader()
                for r in self.rows:
                    writer.writerow(asdict(r))
            messagebox.showinfo("完了", f"CSVを書き出しました:\n{dst}")
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))

    def export_json(self):
        if not self.rows:
            return

        dst = filedialog.asksaveasfilename(
            title="JSONに保存",
            defaultextension=".json",
            filetypes=[("JSON", ".json")],
        )
        if not dst:
            return

        try:
            with open(dst, "w", encoding="utf-8") as f:
                json.dump(
                    [asdict(r) for r in self.rows],
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            messagebox.showinfo("完了", f"JSONを書き出しました:\n{dst}")
        except Exception as e:
            messagebox.showerror("保存失敗", str(e))

    def open_selected_folder(self):
        sel = self.tree.selection()
        if not sel or not self.root_dir:
            return
        folder_name = sel[0]
        target = self.root_dir / folder_name

        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                import subprocess
                subprocess.run(["open", str(target)])
            else:
                import subprocess
                subprocess.run(["xdg-open", str(target)])
        except Exception as e:
            messagebox.showerror("起動失敗", str(e))


# ------------------ エントリポイント ------------------ #
def main():
    root = tk.Tk()
    try:
        style = ttk.Style()
        if "clam" in style.theme_names():
            style.theme_use("clam")
    except Exception:
        pass

    app = VideoBrowserGUI(root)
    root.geometry("1250x780")
    root.mainloop()


if __name__ == "__main__":
    main()
