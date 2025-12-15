import tkinter as tk
from tkinter import scrolledtext

def remove_newlines():
    """
    入力テキストボックスから内容を取得し、改行を削除して出力テキストボックスに表示する
    """
    try:
        # 1. 入力テキストボックスから文字列を取得
        input_text = input_box.get("1.0", tk.END)

        # 2. 改行コードを検出して削除
        # \r\n (CRLF), \n (LF), \r (CR) をすべて空文字に置換します。
        # replace('\r', '') の後に replace('\n', '') を実行することで、
        # \r\n も \n も確実に削除できます。
        cleaned_text = input_text.replace('\r', '').replace('\n', '')
        
        # 3. 出力テキストボックスの内容をクリアして、結果を表示
        output_box.delete("1.0", tk.END)
        output_box.insert(tk.END, cleaned_text)
        
    except Exception as e:
        # エラー処理 (念のため)
        output_box.delete("1.0", tk.END)
        output_box.insert(tk.END, f"エラーが発生しました: {e}")

# --- GUIのセットアップ ---

# メインウィンドウの作成
root = tk.Tk()
root.title("改行削除ツール")

# 1. 入力エリア (ScrolledTextでスクロール可能に)
input_label = tk.Label(root, text="① テキストをここに貼り付け (入力)")
input_label.pack(pady=5)
input_box = scrolledtext.ScrolledText(root, width=60, height=10)
input_box.pack(padx=10)

# 2. 実行ボタン
# commandに改行削除関数を指定
process_button = tk.Button(root, text="② 改行を削除する (実行)", command=remove_newlines, bg='lightblue', font=('Arial', 12, 'bold'))
process_button.pack(pady=10)

# 3. 出力エリア (ScrolledTextでスクロール可能に)
output_label = tk.Label(root, text="③ 処理結果 (改行削除済み)")
output_label.pack(pady=5)
output_box = scrolledtext.ScrolledText(root, width=60, height=10)
output_box.pack(padx=10, pady=(0, 10))

# GUIイベントループの開始
root.mainloop()