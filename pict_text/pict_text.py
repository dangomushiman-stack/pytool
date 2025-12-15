import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import os
import threading
from PIL import Image, ImageTk
from datetime import datetime

# Google Gemini API関連
from google import genai
from google.genai.errors import APIError

# --- 設定 ---
MODEL_NAME = "gemini-2.5-flash"
IMAGE_EXTENSIONS = ('.jpg', '.jpeg', '.png')
OUTPUT_FILENAME_BASE = "gemini_results"
# 💡 【変更点 1】APIキーのパスを保持するための設定ファイル
KEY_CONFIG_FILE = "key_path_config.txt" 
# -------------

class GeminiImageProcessorApp:
    def __init__(self, master):
        self.master = master
        master.title("Gemini 画像プロセッサ (シーケンシャル/キャッシュ対応)")
        master.geometry("1200x650") 

        self.current_folder = ""
        self.current_image_path = None
        self.file_paths = []
        self.is_processing = False 
        self.response_cache = {}
        # 💡 APIキーファイルのパスを保持する変数
        self.api_key_path = "" 

        self._setup_ui(master)
        
        # 💡 【変更点 3】設定ファイルからAPIキーのパスを読み込む
        self._load_key_path_config()
        
        # 💡 【変更点 4】起動時にAPIクライアントの初期化を試みる
        self.client = self._initialize_client(show_error=True)
        
        master.bind("<Configure>", self.on_window_resize) 
    
    def _setup_ui(self, master):
        # メインフレーム
        main_frame = ttk.Frame(master, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # 1. フォルダ選択エリア (上部)
        folder_frame = ttk.Frame(main_frame)
        folder_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(folder_frame, text="画像フォルダ:").pack(side=tk.LEFT, padx=5)
        self.folder_path_var = tk.StringVar()
        ttk.Entry(folder_frame, textvariable=self.folder_path_var, width=50, state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(folder_frame, text="フォルダ選択", command=self.select_folder).pack(side=tk.LEFT)
        
        # 💡 【変更点 2】APIキーファイル設定エリア
        key_frame = ttk.Frame(main_frame)
        key_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(key_frame, text="APIキーファイル:").pack(side=tk.LEFT, padx=5)
        self.api_key_path_var = tk.StringVar()
        ttk.Entry(key_frame, textvariable=self.api_key_path_var, width=50, state='readonly').pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(key_frame, text="ファイル選択/設定", command=self.select_api_key_file).pack(side=tk.LEFT)

        # 2. メインコンテンツエリア (3分割)
        content_frame = ttk.Frame(main_frame)
        content_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        # Grid設定: 3つの列を設定し、中央と右側を伸縮可能にする
        content_frame.grid_columnconfigure(0, weight=0)
        content_frame.grid_columnconfigure(1, weight=1)
        content_frame.grid_columnconfigure(2, weight=1)
        content_frame.grid_rowconfigure(0, weight=1)

        # --- Column 0: 左側 - 画像リスト ---
        list_container = ttk.Frame(content_frame, width=250)
        list_container.grid(row=0, column=0, sticky="nswe", padx=(0, 10))
        list_container.grid_rowconfigure(1, weight=1)
        
        ttk.Label(list_container, text="画像ファイルリスト").grid(row=0, column=0, sticky="w")
        
        list_scrollbar = ttk.Scrollbar(list_container, orient=tk.VERTICAL)
        self.image_listbox = tk.Listbox(list_container, height=25, yscrollcommand=list_scrollbar.set)
        list_scrollbar.config(command=self.image_listbox.yview)
        
        list_scrollbar.grid(row=1, column=1, sticky="ns")
        self.image_listbox.grid(row=1, column=0, sticky="nswe")
        self.image_listbox.bind("<<ListboxSelect>>", self.on_listbox_select)
        
        # --- Column 1: 中央 - 画像表示とボタン ---
        center_frame = ttk.Frame(content_frame)
        center_frame.grid(row=0, column=1, sticky="nswe", padx=10)
        
        center_frame.grid_columnconfigure(0, weight=1)
        center_frame.grid_rowconfigure(1, weight=1)

        ttk.Label(center_frame, text="選択中の画像").grid(row=0, column=0, pady=(0, 5), sticky="n")
        
        self.image_display_canvas = tk.Canvas(center_frame, bg="lightgray", relief="solid", bd=1)
        self.image_display_canvas.grid(row=1, column=0, sticky="nswe", pady=(0, 10))
        
        self.canvas_text = self.image_display_canvas.create_text(200, 175, text="画像がありません", fill="black", anchor="center")
        self.canvas_image_id = None
        
        self.process_button = ttk.Button(center_frame, text="画像をGeminiでテキスト化 (個別)", command=self.start_processing_thread, state=tk.DISABLED)
        self.process_button.grid(row=2, column=0, sticky="ew", pady=(5, 2))
        
        self.process_all_button = ttk.Button(center_frame, text="リストのすべてをシーケンシャル処理", command=self.start_all_processing_thread, state=tk.DISABLED)
        self.process_all_button.grid(row=3, column=0, sticky="ew", pady=(2, 5))
        
        # --- Column 2: 右側 - 結果テキスト/進捗 ---
        right_frame = ttk.Frame(content_frame)
        right_frame.grid(row=0, column=2, sticky="nswe")
        right_frame.grid_rowconfigure(2, weight=1)

        progress_frame = ttk.Frame(right_frame)
        progress_frame.grid(row=0, column=0, sticky="ew", pady=(0, 5))
        progress_frame.grid_columnconfigure(0, weight=1)
        
        self.progress_bar = ttk.Progressbar(progress_frame, orient='horizontal', mode='determinate')
        self.progress_bar.grid(row=0, column=0, sticky="ew")
        
        self.status_label = ttk.Label(progress_frame, text="ステータス: 待機中")
        self.status_label.grid(row=1, column=0, sticky="w")
        
        ttk.Label(right_frame, text="Geminiからの結果").grid(row=1, column=0, pady=(5, 0), sticky="w")
        
        self.result_text = tk.Text(right_frame, height=15, wrap=tk.WORD)
        self.result_text.grid(row=2, column=0, sticky="nswe")


    # --- APIキーファイル設定ロジック ---
    
    def _load_key_path_config(self):
        """設定ファイルからAPIキーファイルのパスを読み込み、UIに反映する"""
        try:
            with open(KEY_CONFIG_FILE, 'r', encoding='utf-8') as f:
                path = f.readline().strip()
                if path and os.path.exists(path):
                    self.api_key_path = path
                    self.api_key_path_var.set(self.api_key_path)
                    return True
                else:
                    self.api_key_path_var.set("未設定 (ファイルを選択してください)")
                    return False
        except FileNotFoundError:
            self.api_key_path_var.set("未設定 (ファイルを選択してください)")
            return False
        except Exception as e:
            messagebox.showerror("設定エラー", f"設定ファイルの読み込み中にエラーが発生しました: {e}")
            self.api_key_path_var.set("エラーが発生しました")
            return False
            
    def _save_key_path_config(self, path):
        """APIキーファイルのパスを設定ファイルに保存する"""
        try:
            with open(KEY_CONFIG_FILE, 'w', encoding='utf-8') as f:
                f.write(path)
            self.api_key_path = path
        except Exception as e:
            messagebox.showerror("設定エラー", f"設定ファイルの書き込み中にエラーが発生しました: {e}")

    def select_api_key_file(self):
        """GUIからAPIキーファイルを選択し、パスを保存・反映する"""
        file_selected = filedialog.askopenfilename(
            title="APIキーファイルを選択してください",
            filetypes=(("テキストファイル", "*.txt"), ("すべてのファイル", "*.*"))
        )
        if file_selected:
            self._save_key_path_config(file_selected)
            self.api_key_path_var.set(file_selected)
            # クライアントを再初期化して、処理ボタンの状態を更新
            self.client = self._initialize_client(show_error=True)
            self.reset_ui_state() # 状態をリセットし、ボタンの状態を再評価させる

    def _load_api_key_from_file(self):
        """設定されているパスからAPIキーを読み込む"""
        if not self.api_key_path or not os.path.exists(self.api_key_path):
            return None # パスが設定されていない、またはファイルが存在しない

        try:
            with open(self.api_key_path, 'r', encoding='utf-8') as f:
                # 最初の行を読み込み、前後の空白や改行を除去
                key = f.readline().strip() 
                if not key:
                    return None
                return key
        except Exception:
            return None

    def _initialize_client(self, show_error=False):
        """APIキーを読み込み、Geminiクライアントを初期化する"""
        api_key = self._load_api_key_from_file()
        
        if not api_key:
            if show_error:
                messagebox.showerror("APIエラー", "APIキーが設定されていません。\nGUIの「APIキーファイル」でテキストファイルを選択し、キーを記述してください。")
            return None
            
        try:
            # APIクライアントの初期化 (読み込んだAPIキーを使用)
            return genai.Client(api_key=api_key)
        except Exception as e:
            if show_error:
                messagebox.showerror("APIエラー", f"APIクライアントの初期化に失敗しました。\nキーが不正である可能性があります。\nエラー: {e}")
            return None

    # --- GUI 操作とイベント処理 (変更なし) ---
    
    def on_window_resize(self, event):
        if self.current_image_path:
            self.master.after(100, self.redraw_image_on_canvas)

    def redraw_image_on_canvas(self):
        # ... (元のロジックを保持)
        if not self.current_image_path:
            return

        try:
            original_img = Image.open(self.current_image_path)
            
            # 描画を強制し、最新のサイズを取得
            self.image_display_canvas.update_idletasks()
            canvas_width = self.image_display_canvas.winfo_width()
            canvas_height = self.image_display_canvas.winfo_height()
            
            if canvas_width < 50 or canvas_height < 50: return

            original_width, original_height = original_img.size
            ratio = min(canvas_width / original_width, canvas_height / original_height)
            new_width = int(original_width * ratio)
            new_height = int(original_height * ratio)

            resized_img = original_img.resize((new_width, new_height), Image.Resampling.LANCZOS)
            self.tk_image = ImageTk.PhotoImage(resized_img)
            
            self.image_display_canvas.delete("all")
            self.canvas_image_id = self.image_display_canvas.create_image(
                canvas_width / 2, canvas_height / 2, 
                image=self.tk_image, anchor="center"
            )
            # 中央のダミーテキストは削除する
            self.image_display_canvas.delete(self.canvas_text)

        except Exception as e:
            print(f"再描画エラー: {e}")
            pass

    def select_folder(self):
        folder_selected = filedialog.askdirectory()
        if folder_selected:
            self.current_folder = folder_selected
            self.folder_path_var.set(self.current_folder)
            self.load_image_list()

    def load_image_list(self):
        self.image_listbox.delete(0, tk.END)
        self.file_paths = []
        
        try:
            for entry in sorted(os.scandir(self.current_folder), key=lambda e: e.name):
                if entry.is_file() and entry.name.lower().endswith(IMAGE_EXTENSIONS):
                    self.file_paths.append(entry.path)
                    self.image_listbox.insert(tk.END, entry.name)
            
            self.reset_ui_state()
            if self.file_paths:
                self.image_listbox.select_set(0)
                self.on_listbox_select(None)
                self.process_all_button.config(state=tk.NORMAL)
            else:
                self._clear_image_display("フォルダに画像ファイルがありません")
                self.process_button.config(state=tk.DISABLED)
                self.process_all_button.config(state=tk.DISABLED)
        
        except FileNotFoundError:
            messagebox.showerror("エラー", "指定されたフォルダが見つかりません。")

    def _clear_image_display(self, text="画像がありません"):
        # ... (元のロジックを保持)
        if self.canvas_image_id:
            self.image_display_canvas.delete(self.canvas_image_id)
            self.canvas_image_id = None
        
        center_x = self.image_display_canvas.winfo_width() / 2
        center_y = self.image_display_canvas.winfo_height() / 2
        
        if not self.canvas_text:
             self.canvas_text = self.image_display_canvas.create_text(center_x, center_y, text=text, fill="black", anchor="center")
        else:
             self.image_display_canvas.coords(self.canvas_text, center_x, center_y)
             self.image_display_canvas.itemconfig(self.canvas_text, text=text)

        self.tk_image = None
        self.image_display_canvas.delete(self.canvas_image_id)


    def on_listbox_select(self, event):
        # ... (元のロジックを保持)
        if self.is_processing: return

        try:
            selected_indices = self.image_listbox.curselection()
            if not selected_indices:
                self._clear_image_display("画像がありません")
                self.current_image_path = None
                self.process_button.config(state=tk.DISABLED)
                return

            index = selected_indices[0]
            self.current_image_path = self.file_paths[index]
            
            # APIクライアントが有効な場合にのみボタンを有効化
            if self.client:
                 self.process_button.config(state=tk.NORMAL)
            else:
                 self.process_button.config(state=tk.DISABLED)
            
            # 💡 キャッシュ確認と表示
            if self.current_image_path in self.response_cache:
                description = self.response_cache[self.current_image_path]
                self.update_result_text(description, is_cached=True) 
            else:
                self.update_result_text("未処理、または処理結果がキャッシュされていません。", is_error=False)

            self.master.update_idletasks()
            self.redraw_image_on_canvas()

        except Exception as e:
            self._clear_image_display(f"画像の読み込みエラー: {e}")
            self.process_button.config(state=tk.DISABLED)
            self.current_image_path = None
            messagebox.showerror("エラー", f"画像のプレビュー中にエラーが発生しました: {e}")
    
    # --- UI ユーティリティ (変更なし) ---

    def update_result_text(self, text, is_error=False, is_cached=False):
        """結果テキストボックスを更新する (メインスレッド)"""
        # ... (元のロジックを保持)
        self.result_text.delete('1.0', tk.END)
        
        if is_cached:
            self.result_text.insert(tk.END, "--- キャッシュ済み結果 ---\n", 'cached')
            self.result_text.insert(tk.END, text)
            self.result_text.tag_config('cached', foreground='blue')
        elif is_error:
            self.result_text.insert(tk.END, f"エラー:\n{text}", 'error')
            self.result_text.tag_config('error', foreground='red')
        else:
            self.result_text.insert(tk.END, text)

    def reset_button(self, all_processed=False):
        # APIクライアントが有効な場合にのみボタンを有効化
        state = tk.NORMAL if self.client else tk.DISABLED
        
        if self.current_image_path:
            self.process_button.config(text="画像をGeminiでテキスト化 (個別)", state=state)
            
        if not all_processed and self.file_paths:
            self.process_all_button.config(text="リストのすべてをシーケンシャル処理", state=state)
    
    def reset_ui_state(self):
        # ... (元のロジックを保持)
        self.is_processing = False
        self.progress_bar['value'] = 0
        self.status_label.config(text="ステータス: 待機中")
        
        # APIクライアントが有効な場合にのみボタンを有効化
        state = tk.NORMAL if self.client else tk.DISABLED
        
        self.process_button.config(text="画像をGeminiでテキスト化 (個別)", state=state if self.current_image_path else tk.DISABLED)
        self.process_all_button.config(text="リストのすべてをシーケンシャル処理", state=state if self.file_paths else tk.DISABLED)
        
        self.image_listbox.selection_clear(0, tk.END)

    # --- 個別処理ロジック (変更なし) ---
    def start_processing_thread(self):
        if not self.current_image_path or self.is_processing or not self.client: 
             messagebox.showwarning("警告", "APIクライアントが初期化されていないため、処理を開始できません。APIキーファイルを設定してください。")
             return

        self.process_button.config(text="処理中...", state=tk.DISABLED)
        self.result_text.delete('1.0', tk.END)
        self.result_text.insert(tk.END, "Geminiにリクエストを送信しています...\n")
        
        thread = threading.Thread(target=self.process_single_image, daemon=True)
        thread.start()

    def process_single_image(self):
        # ... (元のロジックを保持)
        image_path = self.current_image_path
        
        try:
            img_to_send = Image.open(image_path)
            prompt_parts = [img_to_send, "テキストに変換してください。"]
            response = self.client.models.generate_content(model=MODEL_NAME, contents=prompt_parts)
            description = response.text.strip()
            
            # 💡 キャッシュに保存
            self.response_cache[image_path] = description
            
            self.master.after(0, self.update_result_text, description)

        except APIError as e:
            error_msg = f"APIエラーが発生しました: {e}\n\n無料枠の上限に達している可能性があります。"
            self.master.after(0, self.update_result_text, error_msg, is_error=True)
        except Exception as e:
            error_msg = f"予期せぬエラーが発生しました: {e}"
            self.master.after(0, self.update_result_text, error_msg, is_error=True)
            
        finally:
            self.master.after(0, self.reset_button)

    # --- シーケンシャル処理ロジック (変更なし) ---

    def start_all_processing_thread(self):
        if not self.file_paths or self.is_processing or not self.client: 
            messagebox.showwarning("警告", "APIクライアントが初期化されていないため、処理を開始できません。APIキーファイルを設定してください。")
            return

        self.is_processing = True
        self.process_button.config(state=tk.DISABLED)
        self.process_all_button.config(text="処理を停止", command=self.stop_all_processing)
        self.result_text.delete('1.0', tk.END)
        self.result_text.insert(tk.END, "--- シーケンシャル処理開始 ---\n結果はファイルに保存されます。\n\n")

        thread = threading.Thread(target=self.process_all_images_with_gemini, daemon=True)
        thread.start()

    def stop_all_processing(self):
        # ... (元のロジックを保持)
        self.is_processing = False
        self.status_label.config(text="ステータス: 停止中...")
        messagebox.showinfo("情報", "処理を中断しています。現在のファイルが完了後、停止します。")


    def process_all_images_with_gemini(self):
        # ... (元のロジックを保持)
        total_files = len(self.file_paths)
        processed_count = 0
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(self.current_folder, f"{OUTPUT_FILENAME_BASE}_{timestamp}.txt")
        
        try:
            with open(output_path, 'w', encoding='utf-8') as outfile:
                outfile.write(f"--- Gemini シーケンシャル処理結果 ({timestamp}) ---\n")
                outfile.write(f"モデル: {MODEL_NAME}\n")
                outfile.write(f"プロンプト: テキストに変換してください。\n\n")
                
                for i, image_path in enumerate(self.file_paths):
                    if not self.is_processing:
                        outfile.write("\n--- ユーザーにより処理が中断されました ---\n")
                        break
                    
                    filename = os.path.basename(image_path)
                    
                    self.master.after(0, self.update_progress, i + 1, total_files, filename)
                    self.master.after(0, self.highlight_listbox, i)

                    description = ""
                    status = "成功"
                    is_error = False

                    try:
                        img_to_send = Image.open(image_path)
                        prompt_parts = [img_to_send, "テキストに変換してください。"]
                        
                        response = self.client.models.generate_content(
                            model=MODEL_NAME,
                            contents=prompt_parts
                        )
                        description = response.text.strip()
                        
                        # 💡 キャッシュに保存
                        self.response_cache[image_path] = description
                        # 処理結果をテキストボックスに表示 (キャッシュではないので is_cached=False)
                        self.master.after(0, self.update_result_text, description, False)

                    except APIError as e:
                        description = f"【APIエラー】: {e}"
                        status = "失敗 (API)"
                        is_error = True
                        self.master.after(0, self.update_result_text, description, True)
                    except Exception as e:
                        description = f"【エラー】: {e}"
                        status = "失敗 (その他)"
                        is_error = True
                        self.master.after(0, self.update_result_text, description, True)

                    outfile.write(f"--- {i+1}/{total_files} | ファイル名: {filename} ({status}) ---\n")
                    outfile.write(f"{description}\n\n")
                    
                    processed_count += 1
            
            final_status = f"処理完了: {processed_count} / {total_files} ファイル | 結果ファイル: {os.path.basename(output_path)}"
            self.master.after(0, self.update_status_and_finish, final_status, output_path)

        except Exception as e:
            self.master.after(0, self.update_status_and_finish, f"致命的なエラーが発生しました: {e}", output_path, is_error=True)
            
        finally:
            self.master.after(0, self.reset_ui_state)

    def update_progress(self, current, total, filename):
        # ... (元のロジックを保持)
        self.progress_bar['value'] = (current / total) * 100
        self.status_label.config(text=f"ステータス: 処理中 {current}/{total} - {filename}")

    def highlight_listbox(self, index):
        # ... (元のロジックを保持)
        self.image_listbox.selection_clear(0, tk.END)
        self.image_listbox.select_set(index)
        self.image_listbox.activate(index)

    def update_status_and_finish(self, status_text, output_path, is_error=False):
        # ... (元のロジックを保持)
        self.status_label.config(text=f"ステータス: {status_text}")
        if is_error:
            messagebox.showerror("エラー", status_text)
        else:
            messagebox.showinfo("処理完了", f"シーケンシャル処理が完了しました。\n結果は以下のファイルに保存されました。\n\n{output_path}")

# --- メイン実行 ---
if __name__ == "__main__":
    root = tk.Tk()
    app = GeminiImageProcessorApp(root)
    root.mainloop()