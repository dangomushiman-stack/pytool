# -*- coding: utf-8 -*-
"""
yt-dlp ダウンローダー（Tkinter GUI）
 + 事後処理：
   - 動画ファイル(mp4/webm/mkv)から音声のみ無再エンコード抽出
   - ragtag の場合は「映像-only」「音声-only」を結合して mkv を追加生成

対応想定サイト:
  - YouTube
  - bilibili（BV ID → bilibili_BVxxxx フォルダ）
  - ragtag archive（?v=ID → ragtag_ID フォルダ）
  - ニコニコ動画（smID → niconico_smXXXX フォルダ）

前提:
  pip install yt-dlp
  ffmpeg / ffprobe が PATH に通っていること
"""

import os
import threading
import queue
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import re
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass
from urllib.parse import urlparse

try:
    import yt_dlp
except Exception:
    raise SystemExit("yt-dlp がインポートできません。先に 'pip install yt-dlp' を実行してください")


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
        # 設定保存に失敗してもアプリ自体は続行する
        pass


# ---------------------- ffmpeg/ffprobe ユーティリティ ----------------------
def _run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _probe_audio_codec(path: Path):
    """先頭の音声ストリームの codec_name を返す。なければ None"""
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
    """
    そのファイルに video / audio ストリームがあるかを
    (has_video, has_audio) で返す
    """
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


# コーデック→出力先定義（無再エンコード）
_CODEC_PLAN = {
    "aac": _Plan(".m4a", []),
    "alac": _Plan(".m4a", []),
    "mp3": _Plan(".mp3", []),
    "flac": _Plan(".flac", []),
    "ac3": _Plan(".ac3", []),
    "eac3": _Plan(".eac3", []),
    "opus": _Plan(".mka", ["-f", "matroska"]),  # Opus は matroska にしておく
    "vorbis": _Plan(".ogg", ["-f", "ogg"]),
}


def _plan_for(codec: str | None):
    if not codec:
        return None
    if codec.startswith("pcm_"):
        return _Plan(".wav", ["-f", "wav"])
    return _CODEC_PLAN.get(codec)


def _extract_audio_copy(src: Path, dst: Path, plan: _Plan):
    """ffmpeg -vn -acodec copy + extra_args"""
    cmd = ["ffmpeg", "-y", "-i", str(src), "-vn", "-acodec", "copy"] + plan.extra_args + [
        str(dst)
    ]
    proc = _run(cmd)
    ok = proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    return ok, proc.stderr


def extract_all_audios(outdir: Path, log_cb=lambda s: None):
    """
    outdir 直下の mp4 / webm / mkv を走査して
    音声トラックを無再エンコードで抽出。
    """
    targets: list[Path] = []
    for ext in ("*.mp4", "*.webm", "*.mkv"):
        targets.extend(outdir.glob(ext))
    # 重複除去
    targets = sorted(set(targets))

    if not targets:
        log_cb("（事後抽出）対象の動画ファイルが見つかりませんでした。")
        return

    log_cb(f"（事後抽出）{len(targets)} 件のファイルから音声抽出を開始します。")
    for src in targets:
        log_cb(f"\n=== {src.name} ===")
        codec = _probe_audio_codec(src)
        if not codec:
            log_cb("音声ストリームが見つからないか解析に失敗しました。スキップ。")
            continue
        plan = _plan_for(codec)
        if not plan:
            log_cb(f"未対応コーデックのためスキップ: {codec}")
            continue

        dst = src.with_suffix(plan.ext)
        i = 1
        while dst.exists():
            dst = src.with_name(f"{src.stem}_{i}{plan.ext}")
            i += 1

        ok, err = _extract_audio_copy(src, dst, plan)
        if ok:
            log_cb(f"✅ 出力: {dst.name}")
        else:
            try:
                if dst.exists() and dst.stat().st_size == 0:
                    dst.unlink()
            except Exception:
                pass
            log_cb(f"❌ 失敗しました。詳細: {err}")


def mux_ragtag_av(outdir: Path, log_cb=lambda s: None):
    """
    ragtag で落ちてきた「映像-only」「音声-only」のファイルから
    映像＋音声の mkv ファイルを1つ作る（元ファイルは削除しない）
    """
    candidates: list[Path] = []
    for ext in ("*.mp4", "*.webm", "*.mkv"):
        candidates.extend(outdir.glob(ext))
    if not candidates:
        log_cb("（ragtag mux）対象ファイルが見つかりません。")
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
        log_cb("（ragtag mux）映像-only／音声-only の組み合わせが見つかりません。")
        return

    # 一番サイズの大きいもの同士を選ぶ
    v = max(video_only, key=lambda x: x.stat().st_size)
    a = max(audio_only, key=lambda x: x.stat().st_size)

    log_cb(f"（ragtag mux）映像: {v.name}")
    log_cb(f"（ragtag mux）音声: {a.name}")

    # 出力ファイル名：映像側の stem に _muxed を付けた mkv
    dst = v.with_suffix("")  # 拡張子なし Path
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
    log_cb(f"（ragtag mux）ffmpeg 実行中: {dst.name}")
    proc = _run(cmd)
    if dst.exists() and dst.stat().st_size > 0 and proc.returncode == 0:
        log_cb(f"✅ （ragtag mux）結合完了: {dst.name}")
    else:
        log_cb(f"❌ （ragtag mux）結合に失敗しました: {proc.stderr}")


# ---------------------- URL 関連ユーティリティ ----------------------
def extract_youtube_id(url: str) -> str | None:
    """YouTube URLから動画IDを抽出（該当しないURLなら None）"""
    pattern = re.compile(
        r"(?:https?://)?(?:www\.|m\.)?(?:youtube\.com/(?:watch\?v=|embed/|v/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})"
    )
    match = pattern.search(url)
    return match.group(1) if match else None


def derive_savedir_from_url(url: str, base_outdir: str) -> str:
    """
    URL から保存先サブフォルダ名を決める。
    - YouTube: 動画ID
    - bilibili: bilibili_BVxxxxxx
    - ragtag: ragtag_動画ID（?v=以降）
    - niconico: niconico_smXXXXXXX
    - その他: パスを加工
    """
    # --- YouTube ---
    vid = extract_youtube_id(url)
    if vid:
        return os.path.join(base_outdir, vid)

    parsed = urlparse(url)

    # --- bilibili（BV ID を抽出） ---
    m_bv = re.search(r"(BV[0-9A-Za-z]+)", url)
    if m_bv:
        bv = m_bv.group(1)
        return os.path.join(base_outdir, f"bilibili_{bv}")

    # --- niconico（sm12345678 など）---
    m_ni = re.search(r"(sm\d+)", url)
    if m_ni:
        smid = m_ni.group(1)
        return os.path.join(base_outdir, f"niconico_{smid}")

    # --- ragtag（?v=ID を抽出） ---
    if "ragtag" in (parsed.netloc or ""):
        m_v = re.search(r"[?&]v=([0-9A-Za-z_-]+)", url)
        if m_v:
            rag_id = m_v.group(1)
            return os.path.join(base_outdir, f"ragtag_{rag_id}")
        # v= が無い場合は最後のパス要素
        path = parsed.path.strip("/")
        last = path.split("/")[-1] if path else "episode"
        return os.path.join(base_outdir, f"ragtag_{last}")

    # --- その他サイト ---
    path = parsed.path.strip("/") or "download"
    sub = path.replace("/", "_")
    return os.path.join(base_outdir, sub)


# ---------------------- GUI 本体 ----------------------
class YTDLPDownloaderGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("yt-dlp Downloader (+ ragtag結合 & 音声抽出)")
        self.geometry("820x640")
        self.minsize(720, 520)

        self.events = queue.Queue()  # ワーカー→UI
        self.worker: threading.Thread | None = None

        # 設定読み込み
        self.settings = load_settings()

        # --- 上段：URL 入力と保存先 ---
        frm_top = ttk.Frame(self, padding=12)
        frm_top.pack(fill=tk.X)

        ttk.Label(frm_top, text="URL（YouTube / bilibili / ragtag / niconico など）").grid(
            row=0, column=0, sticky=tk.W
        )
        self.var_url = tk.StringVar()
        ent_url = ttk.Entry(frm_top, textvariable=self.var_url)
        ent_url.grid(row=0, column=1, columnspan=3, sticky=tk.EW, padx=(6, 0))
        frm_top.columnconfigure(1, weight=1)

        ttk.Label(frm_top, text="保存先").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))

        default_outdir = self.settings.get("last_outdir", os.getcwd())
        self.var_outdir = tk.StringVar(value=default_outdir)

        ent_out = ttk.Entry(frm_top, textvariable=self.var_outdir)
        ent_out.grid(row=1, column=1, sticky=tk.EW, padx=(6, 4), pady=(8, 0))
        ttk.Button(frm_top, text="参照...", command=self.browse_outdir).grid(
            row=1, column=2, sticky=tk.E, pady=(8, 0)
        )

        # --- ダウンロード内容の選択 ---
        grp_sel = ttk.LabelFrame(self, text="ダウンロード内容", padding=12)
        grp_sel.pack(fill=tk.X, padx=12, pady=(4, 0))

        self.var_content = tk.StringVar(value="video")  # video / audio / video_only
        ttk.Radiobutton(
            grp_sel, text="動画（音声付き）", value="video", variable=self.var_content
        ).grid(row=0, column=0, sticky=tk.W)
        ttk.Radiobutton(
            grp_sel, text="音声のみ（bestaudio）", value="audio", variable=self.var_content
        ).grid(row=0, column=1, sticky=tk.W, padx=(16, 0))
        ttk.Radiobutton(
            grp_sel, text="動画のみ（無音）", value="video_only", variable=self.var_content
        ).grid(row=0, column=2, sticky=tk.W, padx=(16, 0))

        self.var_thumb = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grp_sel, text="サムネイルも保存（.jpg 変換）", variable=self.var_thumb
        ).grid(row=1, column=0, sticky=tk.W, pady=(8, 0))

        self.var_extra_audio = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grp_sel, text="（動画選択時）別途 audio も保存", variable=self.var_extra_audio
        ).grid(row=1, column=1, sticky=tk.W, pady=(8, 0))

        # 自動抽出のON/OFF
        self.var_post_extract = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            grp_sel,
            text="ダウンロード後に ragtag結合 / 音声抽出 を実行",
            variable=self.var_post_extract,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=(8, 0))

        # --- 実行ボタン ---
        frm_btn = ttk.Frame(self, padding=(12, 0))
        frm_btn.pack(fill=tk.X)
        self.btn_start = ttk.Button(frm_btn, text="ダウンロード開始", command=self.on_start)
        self.btn_start.pack(side=tk.RIGHT)

        # --- 進捗 ---
        frm_prog = ttk.Frame(self, padding=12)
        frm_prog.pack(fill=tk.X)
        self.pb = ttk.Progressbar(
            frm_prog, length=300, mode="determinate", maximum=100
        )
        self.pb.pack(fill=tk.X)
        self.var_status = tk.StringVar(value="待機中")
        ttk.Label(frm_prog, textvariable=self.var_status).pack(
            anchor=tk.W, pady=(6, 0)
        )

        # --- ログ ---
        frm_log = ttk.Frame(self, padding=(12, 0))
        frm_log.pack(fill=tk.BOTH, expand=True)
        self.txt = tk.Text(frm_log, height=16, wrap="word")
        self.txt.pack(fill=tk.BOTH, expand=True)
        self.txt.insert("end", "ログ出力をここに表示します...")
        self.txt.configure(state="disabled")

        # UI ポーリング開始
        self.after(100, self.process_events)

    # ---------- UI handlers ----------
    def browse_outdir(self):
        path = filedialog.askdirectory(
            initialdir=self.var_outdir.get() or os.path.expanduser("~")
        )
        if path:
            self.var_outdir.set(path)
            self.settings = load_settings()
            self.settings["last_outdir"] = path
            save_settings(self.settings)

    def on_start(self):
        url = self.var_url.get().strip()
        outdir = self.var_outdir.get().strip()
        content = self.var_content.get()
        want_thumb = self.var_thumb.get()
        extra_audio = self.var_extra_audio.get()

        if not url:
            messagebox.showwarning("入力不足", "URL を入力してください")
            return
        if not outdir:
            messagebox.showwarning("入力不足", "保存先フォルダを指定してください")
            return

        # 保存先を設定ファイルに記録
        self.settings = load_settings()
        self.settings["last_outdir"] = outdir
        save_settings(self.settings)

        # URL から保存フォルダを決定
        savedir = derive_savedir_from_url(url, outdir)

        # 既存フォルダがある場合は確認
        if os.path.isdir(savedir):
            proceed = messagebox.askyesno(
                "確認",
                f"保存先「{savedir}」は既に存在します。\n"
                "このままダウンロードを続行しますか？\n"
                "（既存ファイルが上書き／追記される可能性があります）",
            )
            if not proceed:
                return

        os.makedirs(savedir, exist_ok=True)

        if self.worker and self.worker.is_alive():
            messagebox.showinfo("実行中", "前のダウンロードがまだ実行中です")
            return

        self.btn_start.configure(state="disabled")
        self.pb.configure(value=0)
        self.var_status.set("初期化中...")
        self._log(f"==== ダウンロード開始 ====\nURL: {url}\n保存先: {savedir}")

        # メタ情報 JSON 保存（失敗しても致命的ではない）
        try:
            self._json_download(url, savedir)
        except Exception as e:
            self._log(f"メタ情報取得に失敗しました: {e}")

        # ダウンロードワーカースレッド起動
        self.worker = threading.Thread(
            target=self.download_worker,
            args=(url, savedir, content, want_thumb, extra_audio),
            daemon=True,
        )
        self.worker.start()

    # ---------- Worker & yt-dlp ----------
    def download_worker(
        self, url: str, outdir: str, content: str, want_thumb: bool, extra_audio: bool
    ):
        def progress_hook(d: dict):
            self.events.put(("progress", d))

        parsed = urlparse(url)
        is_ragtag = "ragtag" in (parsed.netloc or "")

        # 共通オプション
        ydl_opts: dict = {
            "outtmpl": os.path.join(outdir, "%(title)s.%(ext)s"),
            "progress_hooks": [progress_hook],
            "cookiesfrombrowser": ("chrome",),
        }

        # ---- リトライ強化設定 ----
        ydl_opts.update({
            "retries": 20,                # 通常のリトライ
            "fragment_retries": 50,       # HLS/DASH の分割ファイル取得リトライ
            "file_access_retries": 10,    # ファイル破損時の再試行
            "extractor_retries": 10,      # メタ情報抽出の再試行
            "retry_sleep": 2,             # リトライ間隔（秒）
        })

        # ragtag 以外はプレイリスト無視
        if not is_ragtag:
            ydl_opts["noplaylist"] = True

        if want_thumb:
            ydl_opts["writethumbnail"] = True
            ydl_opts.setdefault("postprocessors", []).append(
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg"}
            )

        # --- ragtag / その他で format の決め方を分ける ---
        if is_ragtag:
            # ★ ragtag は CLI と同じく「デフォルトフォーマット & プレイリスト」
            #    → format を指定しないで yt-dlp に任せる
            pass
        else:
            # 通常サイト（YouTube, bilibili, niconico など）
            if content == "audio":
                ydl_opts["format"] = "bestaudio/best"
            elif content == "video_only":
                ydl_opts["format"] = "bestvideo/best"
            else:
                # 映像＋音声を別々に取りつつ、無理なら best
                ydl_opts["format"] = "bestvideo*+bestaudio*/best"

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get("title") or "(no title)"
                self.events.put(("info", {"title": title}))
        except Exception as e:
            self.events.put(("error", str(e)))
            return

        # 動画（音声付き）＋「別途 audio も保存」がONなら、音声だけもう1本取る
        if content == "video" and extra_audio:
            self.events.put(("note", "動画とは別に音声トラックも取得中..."))
            audio_opts = {
                "outtmpl": os.path.join(outdir, "%(title)s [audio].%(ext)s"),
                "progress_hooks": [progress_hook],
                "format": "bestaudio[protocol!=m3u8][vcodec=none]/bestaudio[vcodec=none]",
                "verbose": False,
                "cookiesfrombrowser": ("chrome",),
            }
            # リトライ設定も同様に付与
            audio_opts.update({
                "retries": 20,
                "fragment_retries": 50,
                "file_access_retries": 10,
                "extractor_retries": 10,
                "retry_sleep": 2,
            })
            if not is_ragtag:
                audio_opts["noplaylist"] = True

            try:
                with yt_dlp.YoutubeDL(audio_opts) as ydl2:
                    ydl2.download([url])
            except Exception as e:
                self.events.put(("error", f"追加オーディオ作成失敗: {e}"))

        # ダウンロード完了
        self.events.put(("done", {"outdir": outdir, "is_ragtag": is_ragtag}))

    # ---------- UI event processing ----------
    def process_events(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._handle_progress(payload)
                elif kind == "done":
                    self._on_done(payload)
                elif kind == "error":
                    self._on_error(payload)
                elif kind == "info":
                    self._on_info(payload)
                elif kind == "note":
                    self._log(str(payload))
                elif kind == "post_extract_log":
                    self._log(str(payload))
                elif kind == "post_extract_done":
                    self._log("（事後処理）完了しました。")
                    self.var_status.set("完了しました")
        except queue.Empty:
            pass
        finally:
            self.after(100, self.process_events)

    def _handle_progress(self, d: dict):
        status = d.get("status")
        if status == "downloading":
            percent_str = d.get("_percent_str", "0.0%").strip()
            try:
                percent_val = float(percent_str.replace("%", ""))
            except Exception:
                percent_val = 0.0
            self.pb.configure(value=percent_val)
            speed = d.get("_speed_str", "")
            eta = d.get("_eta_str", "")
            self.var_status.set(
                f"ダウンロード中... {percent_str}  速度:{speed}  残り:{eta}"
            )
        elif status == "finished":
            self.var_status.set("結合/後処理中...")
        else:
            self.var_status.set(status or "進行中...")

    def _json_download(self, url, path):
        ydl_opts = {
            "quiet": True,
            "skip_download": True,
            "writethumbnail": True,
            "writesubtitles": True,
            "subtitleslangs": ["ja", "en"],
            "cookiesfrombrowser": ("chrome",),
            "noplaylist": True,
        }
        os.makedirs(path, exist_ok=True)
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        filepath = os.path.join(path, "video_info.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=2)

    def _on_info(self, payload: dict):
        title = payload.get("title", "")
        if title:
            self._log(f"対象: {title}")

    def _on_done(self, payload: dict):
        self._log("ダウンロード完了")
        self.pb.configure(value=100)
        self.btn_start.configure(state="normal")
        self.var_status.set("ダウンロード完了")
        outdir = payload.get("outdir")
        is_ragtag = payload.get("is_ragtag", False)
        if self.var_post_extract.get() and outdir:
            self._log("（事後処理）ragtag結合 / 音声抽出 を開始します...")
            threading.Thread(
                target=self._post_extract_worker,
                args=(Path(outdir), bool(is_ragtag)),
                daemon=True,
            ).start()

    def _on_error(self, msg: str):
        self._log("エラー: " + str(msg))
        self.var_status.set("エラーが発生しました")
        self.btn_start.configure(state="normal")

    def _log(self, text: str):
        self.txt.configure(state="normal")
        self.txt.insert("end", text + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    # 事後処理スレッド
    def _post_extract_worker(self, outdir: Path, is_ragtag: bool):
        def log_cb(s: str):
            self.events.put(("post_extract_log", s))

        try:
            # 1) ragtag ならまず映像＋音声の結合を試みる
            if is_ragtag:
                mux_ragtag_av(outdir, log_cb=log_cb)

            # 2) そのあと共通の「音声抽出」
            extract_all_audios(outdir, log_cb=log_cb)
        except Exception as e:
            log_cb(f"（事後処理）エラー: {e}")
        finally:
            self.events.put(("post_extract_done", {}))


if __name__ == "__main__":
    app = YTDLPDownloaderGUI()
    app.mainloop()
