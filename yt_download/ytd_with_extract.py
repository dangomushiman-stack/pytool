# -*- coding: utf-8 -*-
"""
yt-dlp GUIラッパー（CLI直接実行版 + 標準出力Verbose）
 
機能:
  - 複数URLの一括ダウンロード
  - 事前フォルダ存在チェック
  - CLI (yt-dlp) 直接呼び出しによる安定化
  - コンソールへの進行状況出力 (print)
"""

import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re
import json
import subprocess
import datetime
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse

# ---------------------- 設定ファイルユーティリティ ----------------------
SETTINGS_FILE = "settings.json"

def load_settings():
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

# ---------------------- ログ出力ヘルパー ----------------------
def console_log(msg):
    """標準出力に時刻付きで表示"""
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {msg}")

# ---------------------- ffmpeg/ffprobe ユーティリティ ----------------------
def _run(cmd):
    # Windowsでウィンドウを出さないための設定
    startupinfo = None
    if os.name == 'nt':
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)

def _probe_audio_codec(path: Path):
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_name",
        "-of", "json",
        str(path),
    ]
    proc = _run(cmd)
    try:
        data = json.loads(proc.stdout)
        streams = data.get("streams") or []
        if not streams:
            return None
        return streams[0].get("codec_name")
    except Exception:
        return None

def _probe_stream_types(path: Path) -> tuple[bool, bool]:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams",
        "-of", "json",
        str(path),
    ]
    proc = _run(cmd)
    has_v = False
    has_a = False
    try:
        data = json.loads(proc.stdout)
        for s in data.get("streams") or []:
            codec_type = s.get("codec_type")
            if codec_type == "video":
                has_v = True
            elif codec_type == "audio":
                has_a = True
    except Exception:
        pass
    return has_v, has_a

@dataclass
class _Plan:
    ext: str
    extra_args: list

_CODEC_PLAN = {
    "aac": _Plan(".m4a", []),
    "alac": _Plan(".m4a", []),
    "mp3": _Plan(".mp3", []),
    "flac": _Plan(".flac", []),
    "ac3": _Plan(".ac3", []),
    "eac3": _Plan(".eac3", []),
    "opus": _Plan(".mka", ["-f", "matroska"]),
    "vorbis": _Plan(".ogg", ["-f", "ogg"]),
}

def _plan_for(codec: str | None):
    if not codec:
        return None
    if codec.startswith("pcm_"):
        return _Plan(".wav", ["-f", "wav"])
    return _CODEC_PLAN.get(codec)

def _extract_audio_copy(src: Path, dst: Path, plan: _Plan):
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-acodec", "copy"] + plan.extra_args + [str(dst)]
    proc = _run(cmd)
    ok = proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    return ok, proc.stderr

def extract_all_audios(outdir: Path, log_cb=lambda s: None):
    console_log(f"  [FFmpeg] 音声抽出処理を開始: {outdir}")
    targets: list[Path] = []
    for ext in ("*.mp4", "*.webm", "*.mkv"):
        targets.extend(outdir.glob(ext))
    targets = sorted(set(targets))

    if not targets:
        log_cb("対象ファイルなし")
        return

    for src in targets:
        codec = _probe_audio_codec(src)
        if not codec:
            continue
        plan = _plan_for(codec)
        if not plan:
            console_log(f"  [FFmpeg] Skip (未対応コーデック): {codec} in {src.name}")
            continue

        dst = src.with_suffix(plan.ext)
        i = 1
        while dst.exists():
            dst = src.with_name(f"{src.stem}_{i}{plan.ext}")
            i += 1

        console_log(f"    -> Extracting: {src.name} -> {dst.name}")
        ok, err = _extract_audio_copy(src, dst, plan)
        if ok:
            log_cb(f"✅ 音声出力: {dst.name}")
        else:
            console_log(f"    -> Failed: {err}")
            log_cb(f"❌ 失敗: {dst.name}")

def mux_ragtag_av(outdir: Path, log_cb=lambda s: None):
    console_log(f"  [FFmpeg] ragtag結合処理を確認: {outdir}")
    candidates: list[Path] = []
    for ext in ("*.mp4", "*.webm", "*.mkv"):
        candidates.extend(outdir.glob(ext))
    if not candidates:
        return

    video_only: list[Path] = []
    audio_only: list[Path] = []

    for p in candidates:
        has_v, has_a = _probe_stream_types(p)
        if has_v and not has_a:
            video_only.append(p)
        elif has_a and not has_v:
            audio_only.append(p)

    if not video_only or not audio_only:
        console_log("  [FFmpeg] 結合対象なし (映像のみ/音声のみ のペアが見つかりません)")
        return

    v = max(video_only, key=lambda x: x.stat().st_size)
    a = max(audio_only, key=lambda x: x.stat().st_size)

    console_log(f"  [FFmpeg] Muxing: {v.name} + {a.name}")
    log_cb(f"結合中: {v.name} + {a.name}")

    dst = v.with_suffix("")
    dst = dst.with_name(dst.name + "_muxed.mkv")

    i = 1
    while dst.exists():
        dst = dst.with_name(f"{dst.stem}_{i}.mkv")
        i += 1

    cmd = [
        "ffmpeg", "-y",
        "-i", str(v),
        "-i", str(a),
        "-c", "copy",
        str(dst),
    ]
    proc = _run(cmd)
    if dst.exists() and dst.stat().st_size > 0 and proc.returncode == 0:
        console_log(f"    -> Mux Success: {dst.name}")
        log_cb(f"✅ 結合完了: {dst.name}")
    else:
        console_log(f"    -> Mux Failed: {proc.stderr}")
        log_cb(f"❌ 結合失敗")


# ---------------------- URL 関連ユーティリティ ----------------------
def extract_youtube_id(url: str) -> str | None:
    pattern = re.compile(
        r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
    )
    match = pattern.search(url)
    return match.group(1) if match else None

def derive_savedir_from_url(url: str, base_outdir: str) -> str:
    vid = extract_youtube_id(url)
    if vid:
        return os.path.join(base_outdir, vid)
    parsed = urlparse(url)
    m_bv = re.search(r"(BV[0-9A-Za-z]+)", url)
    if m_bv:
        bv = m_bv.group(1)
        return os.path.join(base_outdir, f"bilibili_{bv}")
    m_ni = re.search(r"(sm\d+)", url)
    if m_ni:
        smid = m_ni.group(1)
        return os.path.join(base_outdir, f"niconico_{smid}")
    if "ragtag" in (parsed.netloc or ""):
        m_v = re.search(r"[?&]v=([0-9A-Za-z_-]+)", url)
        if m_v:
            rag_id = m_v.group(1)
            return os.path.join(base_outdir, f"ragtag_{rag_id}")
        path = parsed.path.strip("/")
        last = path.split("/")[-1] if path else "episode"
        return os.path.join(base_outdir, f"ragtag_{last}")
    path = parsed.path.strip("/") or "download"
    sub = path.replace("/", "_")
    return os.path.join(base_outdir, sub)

# ---------------------- GUI 本体 ----------------------
class YTDLPDownloaderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("yt-dlp Downloader (Console Verbose)")
        self.geometry("820x680")
        self.minsize(720, 520)

        self.events = queue.Queue()
        self.worker: threading.Thread | None = None
        
        self.url_queue: list[str] = []
        self.total_tasks = 0
        self.current_task_idx = 0

        self.settings = load_settings()

        # --- UI構成 ---
        frm_top = ttk.Frame(self, padding=12)
        frm_top.pack(fill=tk.X)
        ttk.Label(frm_top, text="URL（複数可：1行に1つずつ入力してください）").pack(anchor=tk.W)
        frm_text = ttk.Frame(frm_top)
        frm_text.pack(fill=tk.X, pady=(4, 0))
        self.txt_url = tk.Text(frm_text, height=5)
        self.txt_url.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scr = ttk.Scrollbar(frm_text, command=self.txt_url.yview)
        scr.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_url.configure(yscrollcommand=scr.set)

        frm_dir = ttk.Frame(self, padding=(12, 0))
        frm_dir.pack(fill=tk.X)
        ttk.Label(frm_dir, text="保存先（親フォルダ）").pack(side=tk.LEFT)
        default_outdir = self.settings.get("last_outdir", os.getcwd())
        self.var_outdir = tk.StringVar(value=default_outdir)
        ent_out = ttk.Entry(frm_dir, textvariable=self.var_outdir)
        ent_out.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        ttk.Button(frm_dir, text="参照...", command=self.browse_outdir).pack(side=tk.LEFT)

        grp_sel = ttk.LabelFrame(self, text="設定 (CLI引数)", padding=12)
        grp_sel.pack(fill=tk.X, padx=12, pady=8)
        self.var_content = tk.StringVar(value="video")
        frm_radios = ttk.Frame(grp_sel)
        frm_radios.pack(fill=tk.X, anchor=tk.W)
        ttk.Radiobutton(frm_radios, text="動画（音声付き）", value="video", variable=self.var_content).pack(side=tk.LEFT)
        ttk.Radiobutton(frm_radios, text="音声のみ", value="audio", variable=self.var_content).pack(side=tk.LEFT, padx=16)
        ttk.Radiobutton(frm_radios, text="動画のみ", value="video_only", variable=self.var_content).pack(side=tk.LEFT, padx=16)
        
        frm_checks = ttk.Frame(grp_sel)
        frm_checks.pack(fill=tk.X, anchor=tk.W, pady=(8,0))
        self.var_thumb = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_checks, text="サムネイル保存", variable=self.var_thumb).pack(side=tk.LEFT)
        self.var_extra_audio = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_checks, text="（動画時）Audio別途保存", variable=self.var_extra_audio).pack(side=tk.LEFT, padx=16)
        self.var_post_extract = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm_checks, text="完了後に結合/音声抽出を実行", variable=self.var_post_extract).pack(side=tk.LEFT, padx=16)

        frm_btn = ttk.Frame(self, padding=(12, 0))
        frm_btn.pack(fill=tk.X)
        self.btn_start = ttk.Button(frm_btn, text="一括ダウンロード開始", command=self.on_start)
        self.btn_start.pack(side=tk.RIGHT)

        frm_prog = ttk.Frame(self, padding=12)
        frm_prog.pack(fill=tk.X)
        self.pb = ttk.Progressbar(frm_prog, length=300, mode="determinate", maximum=100)
        self.pb.pack(fill=tk.X)
        self.var_status = tk.StringVar(value="待機中")
        ttk.Label(frm_prog, textvariable=self.var_status).pack(anchor=tk.W, pady=(6, 0))

        frm_log = ttk.Frame(self, padding=(12, 0))
        frm_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log = tk.Text(frm_log, height=12, wrap="word")
        self.txt_log.pack(fill=tk.BOTH, expand=True)
        self.txt_log.insert("end", "コンソール（標準出力）にも詳細な進捗を表示します。\n")
        self.txt_log.configure(state="disabled")

        self.after(100, self.process_events)

    def browse_outdir(self):
        path = filedialog.askdirectory(initialdir=self.var_outdir.get() or os.path.expanduser("~"))
        if path:
            self.var_outdir.set(path)
            self.settings["last_outdir"] = path
            save_settings(self.settings)

    def on_start(self):
        raw_text = self.txt_url.get("1.0", "end").strip()
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]

        if not lines:
            messagebox.showwarning("入力不足", "URL を入力してください")
            return
        outdir_base = self.var_outdir.get().strip()
        if not outdir_base:
            messagebox.showwarning("入力不足", "保存先フォルダを指定してください")
            return
        
        self.settings["last_outdir"] = outdir_base
        save_settings(self.settings)

        if self.worker and self.worker.is_alive():
            messagebox.showinfo("実行中", "現在処理中です")
            return

        # 事前チェック
        console_log("==== 事前チェック開始 ====")
        existing_list = []
        for i, url in enumerate(lines, 1):
            savedir = derive_savedir_from_url(url, outdir_base)
            if os.path.isdir(savedir):
                existing_list.append(savedir)
                console_log(f"  [WARN] 既存フォルダ検知: {os.path.basename(savedir)}")
            else:
                console_log(f"  [OK] 新規作成予定: {os.path.basename(savedir)}")

        if existing_list:
            count = len(existing_list)
            display_list = existing_list[:10]
            msg_txt = "\n".join([os.path.basename(p) for p in display_list])
            if count > 10:
                msg_txt += f"\n... 他 {count - 10} 件"
            proceed = messagebox.askyesno(
                "確認", f"{count} 件のフォルダが既に存在します。続行しますか？\n\n{msg_txt}"
            )
            if not proceed:
                console_log("ユーザーキャンセルにより中止")
                return

        self.url_queue = lines
        self.total_tasks = len(lines)
        self.current_task_idx = 0
        self.btn_start.configure(state="disabled")
        
        console_log(f"==== 一括ダウンロード開始 (全 {self.total_tasks} 件) ====")
        self.start_next_download()

    def start_next_download(self):
        if not self.url_queue:
            console_log("==== 全タスク完了 ====")
            self.var_status.set("全タスク完了")
            self.pb.configure(value=100)
            self.btn_start.configure(state="normal")
            return

        url = self.url_queue.pop(0)
        self.current_task_idx += 1
        outdir_base = self.var_outdir.get().strip()
        savedir = derive_savedir_from_url(url, outdir_base)
        os.makedirs(savedir, exist_ok=True)

        self.pb.configure(value=0)
        self.var_status.set(f"[{self.current_task_idx}/{self.total_tasks}] 初期化中...")
        
        console_log(f"\n--- [Task {self.current_task_idx}/{self.total_tasks}] Start ---")
        console_log(f"  URL: {url}")
        console_log(f"  Dir: {savedir}")

        content = self.var_content.get()
        want_thumb = self.var_thumb.get()
        extra_audio = self.var_extra_audio.get()

        # JSON取得（CLI実行）
        threading.Thread(target=self._run_json_dump, args=(url, savedir), daemon=True).start()

        # ダウンロードワーカー起動
        self.worker = threading.Thread(
            target=self.download_worker_cli,
            args=(url, savedir, content, want_thumb, extra_audio),
            daemon=True,
        )
        self.worker.start()

    # ---------- Worker (CLI Call) ----------
    def _run_json_dump(self, url, savedir):
        """メタデータを --dump-json で取得して保存"""
        console_log("  [Step] メタデータ(JSON)取得中...")
        cmd = ["yt-dlp", "--dump-json", "--skip-download", "--cookies-from-browser", "chrome", url]
        try:
            startupinfo = None
            if os.name == 'nt':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace', startupinfo=startupinfo)
            if proc.returncode == 0:
                data = json.loads(proc.stdout)
                with open(os.path.join(savedir, "video_info.json"), "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self.events.put(("info", {"title": data.get("title", "")}))
                console_log("  [Step] JSON保存完了")
            else:
                console_log("  [Step] JSON取得失敗 (Skip)")
        except Exception as e:
            console_log(f"  [Error] JSON取得例外: {e}")

    def download_worker_cli(self, url, outdir, content, want_thumb, extra_audio):
        parsed = urlparse(url)
        is_ragtag = "ragtag" in (parsed.netloc or "")

        console_log("  [Step] メインダウンロード開始 (yt-dlp)...")

        # ベースコマンド
        cmd = ["yt-dlp"]
        cmd.extend(["-o", os.path.join(outdir, "%(title)s.%(ext)s")])
        cmd.extend([
            "--cookies-from-browser", "chrome",
            "--retries", "20",
            "--fragment-retries", "50",
            "--file-access-retries", "10",
            "--extractor-retries", "10",
            "--retry-sleep", "2",
            "--newline",
            "--no-colors"
        ])

        if not is_ragtag:
            cmd.append("--no-playlist")

        if want_thumb:
            cmd.append("--write-thumbnail")
            cmd.extend(["--convert-thumbnails", "jpg"])

        if is_ragtag:
            pass 
        else:
            if content == "audio":
                cmd.extend(["-f", "bestaudio/best"])
            elif content == "video_only":
                cmd.extend(["-f", "bestvideo/best"])
            else:
                cmd.extend(["-f", "bestvideo*+bestaudio*/best"])

        cmd.append(url)

        # 実行とログ解析
        success = self._run_and_monitor(cmd)

        if not success:
            console_log("  [Error] メインダウンロード失敗")
            self.events.put(("error", "yt-dlp コマンドがエラー終了しました"))
            self.events.put(("done", {"outdir": outdir, "is_ragtag": is_ragtag, "error": True}))
            return

        console_log("  [Step] メインダウンロード完了")

        # 追加オーディオ（動画モード時）
        if content == "video" and extra_audio:
            console_log("  [Step] 追加オーディオトラック取得開始...")
            self.events.put(("note", "別途音声トラック取得中(CLI)..."))
            cmd2 = ["yt-dlp"]
            cmd2.extend(["-o", os.path.join(outdir, "%(title)s [audio].%(ext)s")])
            cmd2.extend(["--cookies-from-browser", "chrome", "--newline", "--no-colors"])
            cmd2.extend(["-f", "bestaudio[protocol!=m3u8][vcodec=none]/bestaudio[vcodec=none]"])
            if not is_ragtag:
                cmd2.append("--no-playlist")
            cmd2.append(url)
            self._run_and_monitor(cmd2)
            console_log("  [Step] 追加オーディオ完了")

        self.events.put(("done", {"outdir": outdir, "is_ragtag": is_ragtag, "error": False}))

    def _run_and_monitor(self, cmd):
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, 
                text=True,
                encoding='utf-8',
                errors='replace',
                startupinfo=startupinfo,
                bufsize=1
            )
            re_prog = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")

            for line in process.stdout:
                line = line.strip()
                if not line:
                    continue
                
                m = re_prog.search(line)
                if m:
                    try:
                        pct = float(m.group(1))
                        self.events.put(("progress", pct))
                    except:
                        pass
                else:
                    # GUIには全部流さないが、コンソールにはエラーっぽいものだけ出す？
                    # ここでは全て流すと多すぎるので、ERRORだけprintする
                    if "ERROR" in line:
                        console_log(f"    (yt-dlp) {line}")

                    if line.startswith("[") and "download" not in line:
                        self.events.put(("log_line", line))
            
            process.wait()
            return process.returncode == 0

        except Exception as e:
            console_log(f"  [Error] subprocess例外: {e}")
            self.events.put(("error", str(e)))
            return False

    # ---------- Event Loop ----------
    def process_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self.pb.configure(value=payload)
                    self.var_status.set(f"[{self.current_task_idx}/{self.total_tasks}] DL中... {payload}%")
                elif kind == "log_line":
                    if len(payload) < 60:
                        self.var_status.set(payload)
                elif kind == "done":
                    self._handle_task_done(payload)
                elif kind == "error":
                    self._log(f"エラー: {payload}")
                elif kind == "info":
                    self._log(f"タイトル: {payload.get('title')}")
                elif kind == "note":
                    self._log(str(payload))
                elif kind == "post_extract_log":
                    self._log(str(payload))
                elif kind == "post_extract_done":
                    console_log("  [Step] タスク完了。次へ。")
                    self.after(100, self.start_next_download)
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_events)

    def _handle_task_done(self, payload):
        outdir = payload.get("outdir")
        is_ragtag = payload.get("is_ragtag")
        has_error = payload.get("error")

        if has_error:
            console_log("  [Warn] エラーのため事後処理をスキップ")
            self.after(500, self.start_next_download)
            return

        if self.var_post_extract.get() and outdir:
            console_log("  [Step] 事後処理スレッド起動 (FFmpeg)")
            threading.Thread(
                target=self._post_extract_worker,
                args=(Path(outdir), bool(is_ragtag)),
                daemon=True,
            ).start()
        else:
            console_log("  [Info] 事後処理なし")
            self.after(500, self.start_next_download)

    def _post_extract_worker(self, outdir, is_ragtag):
        def log_cb(s):
            self.events.put(("post_extract_log", s))
        try:
            if is_ragtag:
                mux_ragtag_av(outdir, log_cb=log_cb)
            extract_all_audios(outdir, log_cb=log_cb)
        except Exception as e:
            console_log(f"  [Error] 事後処理例外: {e}")
            log_cb(f"事後処理エラー: {e}")
        finally:
            self.events.put(("post_extract_done", {}))

    def _log(self, text):
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", text + "\n")
        self.txt_log.see("end")
        self.txt_log.configure(state="disabled")

if __name__ == "__main__":
    app = YTDLPDownloaderGUI()
    app.mainloop()