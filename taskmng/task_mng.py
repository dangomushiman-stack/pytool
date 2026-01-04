import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime, date
import sqlite3
import uuid

# --- SQLite セットアップ ---
conn = sqlite3.connect("tasks.db")
cur = conn.cursor()
# タスクテーブル
cur.execute("""
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    time TEXT,
    title TEXT
)
""")
# 既存テーブルに completed カラムがなければ追加
try:
    cur.execute("ALTER TABLE tasks ADD COLUMN completed INTEGER DEFAULT 0")
except sqlite3.OperationalError:
    pass
# 変更履歴テーブル
cur.execute("""
CREATE TABLE IF NOT EXISTS change_reasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    changed_at TEXT,
    reason TEXT,
    FOREIGN KEY(task_id) REFERENCES tasks(id)
)
""")
# 順序関係テーブル
cur.execute("""
CREATE TABLE IF NOT EXISTS task_orders (
    id TEXT,
    task_id INTEGER,
    idx INTEGER,
    PRIMARY KEY(id, task_id),
    FOREIGN KEY(task_id) REFERENCES tasks(id)
)
""")
conn.commit()

# キャッシュ変数
tasks = []                   # (id, date, time, title)
order_groups = {}            # task_id -> (group_id, idx)
group_members = {}           # group_id -> list of task_ids

# --- ヘルパー関数 ---
def is_today(date_str):
    """date_str が今日の日付と一致するかを判定して返す"""
    return date_str == date.today().strftime("%Y-%m-%d")

def normalize_date(input_str):
    """
    'YYYY-M-D' や 'YYYY-MM-DD' などを 'YYYY-MM-DD' に統一して返す。
    ValueError なら例外を投げる。
    """
    parts = input_str.split('-')
    if len(parts) != 3:
        raise ValueError("日付は「YYYY-M-D」形式で入力してください")
    year, month, day = parts
    y = int(year)
    m = int(month)
    d = int(day)
    return f"{y:04d}-{m:02d}-{d:02d}"

def normalize_time(input_str):
    """
    'H:M' や 'HH:MM' などを 'HH:MM' に統一して返す。
    ValueError なら例外を投げる。
    """
    parts = input_str.split(':')
    if len(parts) != 2:
        raise ValueError("時刻は「H:M」または「HH:MM」形式で入力してください")
    hour, minute = parts
    h = int(hour)
    m = int(minute)
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError("時刻が不正です")
    return f"{h:02d}:{m:02d}"

# --- DB読み込み ---
def load_orders():
    order_groups.clear()
    group_members.clear()
    cur.execute("SELECT id, task_id, idx FROM task_orders")
    for gid, tid, idx in cur.fetchall():
        order_groups[tid] = (gid, idx)
        group_members.setdefault(gid, []).append(tid)

def load_tasks():
    load_orders()
    # 完了フラグが0のタスクのみ取得
    cur.execute("SELECT id, date, time, title FROM tasks WHERE completed=0")
    rows = cur.fetchall()
    tasks.clear()
    tasks.extend(rows)
    refresh_tree()

# --- GUI更新 ---
def refresh_tree():
    # 全行クリア
    for item in tree.get_children():
        tree.delete(item)

    # tasks リストをソートして挿入
    sorted_tasks = sorted(
        tasks,
        key=lambda t: datetime.strptime(f"{t[1]} {t[2]}", "%Y-%m-%d %H:%M")
    )
    for tid, date_str, time_str, title in sorted_tasks:
        weekday = ["月","火","水","木","金","土","日"][datetime.strptime(date_str, "%Y-%m-%d").weekday()]
        disp_date = f"{date_str} ({weekday})"
        tags = []
        if is_today(date_str):
            tags.append("today")
        tree.insert("", tk.END, iid=str(tid), values=(disp_date, time_str, title), tags=tags)

    clear_related_highlight()

# --- Treeview選択時処理 ---
def on_select(event):
    sel = tree.selection()
    clear_related_highlight()
    if not sel:
        return

    # 選択したタスク情報を入力欄にセット
    tid = int(sel[0])
    for t in tasks:
        if t[0] == tid:
            _, d, tm, ttl = t
            entry_title.delete(0, tk.END); entry_title.insert(0, ttl)
            entry_date.delete(0, tk.END); entry_date.insert(0, d)
            entry_time.delete(0, tk.END); entry_time.insert(0, tm)
            break

    # 関連タスクを「related」タグのみでハイライト
    related_ids = set()
    for iid in sel:
        tid2 = int(iid)
        if tid2 in order_groups:
            gid, _ = order_groups[tid2]
            for member in group_members.get(gid, []):
                if str(member) not in sel:
                    related_ids.add(member)
    for other in related_ids:
        if tree.exists(str(other)):
            # ここでは「related」だけをつけ、todayタグは削除
            tree.item(str(other), tags=('related',))

    # その後、全行を回って today タグを再設定
    # （「related」がついていない行にのみ「today」を付与）
    for iid in tree.get_children():
        tid_i = int(iid)
        # tasks リストから生の date_str を取得
        raw_date = None
        for t in tasks:
            if t[0] == tid_i:
                raw_date = t[1]
                break
        if raw_date and is_today(raw_date):
            current_tags = list(tree.item(iid, 'tags'))
            if 'related' not in current_tags:
                tree.item(iid, tags=('today',))

    tree.update_idletasks()

# --- 関連強調クリア ---
def clear_related_highlight():
    for iid in tree.get_children():
        tags = [t for t in tree.item(iid, 'tags') if t not in ('related','today_related')]
        tree.item(iid, tags=tuple(tags))

# --- 完了処理 ---
def complete_task():
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("警告", "完了するタスクを選択してください")
        return
    tid = int(sel[0])
    # 完了フラグを立てる
    cur.execute("UPDATE tasks SET completed=1 WHERE id=?", (tid,))
    conn.commit()
    # 未完了タスクを再読み込み＋TreeView再描画
    load_tasks()
    refresh_tree()
    clear_entries()

# --- ソート機能 ---
sort_order = {"date": True, "time": True, "title": True}
def sort_by_column(col):
    rev = sort_order[col]
    sort_order[col] = not rev
    idx_map = {"date":1, "time":2, "title":3}
    if col in ('date','time'):
        tasks.sort(key=lambda t: datetime.strptime(f"{t[1]} {t[2]}", "%Y-%m-%d %H:%M"), reverse=rev)
    else:
        tasks.sort(key=lambda t: t[idx_map[col]], reverse=rev)
    refresh_tree()

# --- CRUD機能 ---
def insert_task():
    title = entry_title.get().strip()
    date_in = entry_date.get().strip()
    time_in = entry_time.get().strip()
    if not title:
        messagebox.showwarning("警告","タスク名が必要です")
        return
    try:
        # 日付フォーマットを正規化（YYYY-MM-DD）
        date_norm = normalize_date(date_in)
        # 時刻フォーマットを正規化（HH:MM）
        time_norm = normalize_time(time_in)
    except ValueError as e:
        messagebox.showerror("入力エラー", str(e))
        return

    cur.execute("INSERT INTO tasks(date,time,title) VALUES(?,?,?)", (date_norm, time_norm, title))
    conn.commit()
    # 未完了タスクを再読み込み＋TreeView再描画
    load_tasks()
    refresh_tree()
    clear_entries()

def update_task():
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("警告","タスクを選択してください")
        return
    tid = int(sel[0])
    ttl = entry_title.get().strip()
    d = entry_date.get().strip()
    tm = entry_time.get().strip()
    reason = simpledialog.askstring("変更理由","理由を入力してください")
    if reason:
        try:
            # フォーマット正規化
            date_norm = normalize_date(d)
            time_norm = normalize_time(tm)
        except ValueError as e:
            messagebox.showerror("入力エラー", str(e))
            return

        cur.execute("UPDATE tasks SET date=?,time=?,title=? WHERE id=?", (date_norm, time_norm, ttl, tid))
        cur.execute(
            "INSERT INTO change_reasons(task_id,changed_at,reason) VALUES(?,?,?)",
            (tid, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), reason)
        )
        conn.commit()
        # 未完了タスクを再読み込み＋TreeView再描画
        load_tasks()
        refresh_tree()
        clear_entries()

def delete_task():
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("警告","タスクを選択してください")
        return
    tid = int(sel[0])
    if messagebox.askyesno("確認","削除しますか？"):
        cur.execute("DELETE FROM tasks WHERE id=?", (tid,))
        conn.commit()
        load_tasks()
        clear_entries()

# --- 変更履歴表示 ---
def show_history():
    sel = tree.selection()
    if not sel:
        messagebox.showwarning("警告","タスクを選択してください")
        return
    tid = int(sel[0])
    cur.execute("SELECT changed_at,reason FROM change_reasons WHERE task_id=? ORDER BY changed_at", (tid,))
    rows = cur.fetchall()
    if not rows:
        messagebox.showinfo("履歴なし","履歴がありません")
        return
    win = tk.Toplevel(root)
    win.title("変更履歴")
    th = ttk.Treeview(win, columns=('time','reason'), show='headings')
    th.heading('time', text='日時')
    th.heading('reason', text='理由')
    th.pack(fill=tk.BOTH, expand=True)
    for r in rows:
        th.insert('', tk.END, values=r)

# --- 順序設定 ---
def set_order():
    sel = tree.selection()
    if len(sel) < 2:
        messagebox.showwarning("警告","複数選択してください")
        return
    gid = uuid.uuid4().hex
    for idx, iid in enumerate(sel):
        cur.execute("REPLACE INTO task_orders(id,task_id,idx) VALUES(?,?,?)", (gid, int(iid), idx))
    conn.commit()
    load_tasks()

# --- 入力欄クリア ---
def clear_entries():
    entry_title.delete(0, tk.END)
    entry_date.delete(0, tk.END)
    entry_time.delete(0, tk.END)

# --- GUIセットアップ ---
root = tk.Tk()
root.title("タスクスケジューラー")
root.columnconfigure(1, weight=1)
root.rowconfigure(4, weight=1)
style = ttk.Style()
style.configure("Treeview", rowheight=24)
style.map("Treeview",
    background=[('selected','#000080')],
    foreground=[('selected','white')]
)

# 入力エリア
for txt, r in [("タスク名",0), ("日付",1), ("時間",2)]:
    tk.Label(root, text=txt).grid(row=r, column=0, sticky='w')
entry_title = tk.Entry(root); entry_title.grid(row=0, column=1, sticky='ew')
entry_date  = tk.Entry(root); entry_date.grid(row=1, column=1, sticky='ew')
entry_time  = tk.Entry(root); entry_time.grid(row=2, column=1, sticky='ew')

# ボタンフレーム
bf = tk.Frame(root)
bf.grid(row=3, column=0, columnspan=2, sticky='ew', pady=5)
buttons = [
    ("追加",   insert_task),
    ("変更",   update_task),
    ("削除",   delete_task),
    ("完了",   complete_task),
    ("履歴",   show_history),
    ("順序",   set_order)
]
for i, (txt, cmd) in enumerate(buttons):
    bf.columnconfigure(i, weight=1)
    tk.Button(bf, text=txt, command=cmd).grid(row=0, column=i, padx=5)

# タスク一覧（スクロールバー付きフレーム）
tree_frame = tk.Frame(root)
tree_frame.grid(row=4, column=0, columnspan=2, sticky='nsew', padx=5, pady=5)

# Treeview
tree = ttk.Treeview(tree_frame, columns=('date','time','title'), show='headings', selectmode='extended')
for col, hd in zip(('date','time','title'), ['日付','時刻','タスク名']):
    tree.heading(col, text=hd, command=lambda c=col: sort_by_column(c))
    tree.column(col, width=130 if col=='date' else 80 if col=='time' else 200)
# 縦スクロールバー
sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
tree.configure(yscrollcommand=sb.set)
# レイアウト
tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
sb.pack(side=tk.RIGHT, fill=tk.Y)

# タグ定義
tree.tag_configure('today', background='#d0f0c0')
tree.tag_configure('related', background='#ffc0cb', foreground='black')
tree.tag_configure('today_related', background='yellow', foreground='black')

tree.bind('<<TreeviewSelect>>', on_select)

# 初期化
load_tasks()
root.mainloop()
conn.close()
