import asyncio
import csv
import queue
import re
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from bleak import BleakScanner

MAX_BEACONS = 5

class BeaconApp:
    def __init__(self, root):
        self.root = root
        root.title('Beacon RSSI Measurement')
        root.geometry('1050x690')
        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.rows = []
        self.ids = []
        self.count = tk.IntVar(value=1)
        self.n = tk.StringVar(value='30')
        self.interval = tk.StringVar(value='1.0')
        self.id_vars = [tk.StringVar() for _ in range(MAX_BEACONS)]
        self.status = tk.StringVar(value='待機中')
        self.goal = 0
        self.build()
        self.root.after(100, self.poll)

    def build(self):
        config = ttk.LabelFrame(self.root, text='測定設定', padding=12)
        config.pack(fill='x', padx=12, pady=8)
        ttk.Label(config, text='ビーコン台数').grid(row=0, column=0, sticky='w')
        count_box = ttk.Combobox(config, textvariable=self.count, values=list(range(1, 6)), width=5, state='readonly')
        count_box.grid(row=0, column=1, sticky='w', padx=8)
        count_box.bind('<<ComboboxSelected>>', lambda _: self.update_entries())
        ttk.Label(config, text='取得成功回数 N').grid(row=0, column=2, padx=(18, 3))
        ttk.Entry(config, textvariable=self.n, width=8).grid(row=0, column=3)
        ttk.Label(config, text='同一IDの取得間隔 M（秒）').grid(row=0, column=4, padx=(18, 3))
        ttk.Entry(config, textvariable=self.interval, width=8).grid(row=0, column=5)
        self.id_entries = []
        for i in range(MAX_BEACONS):
            ttk.Label(config, text=f'ID {i+1}').grid(row=1+i//3, column=(i%3)*2, sticky='e', pady=7)
            entry = ttk.Entry(config, textvariable=self.id_vars[i], width=12)
            entry.grid(row=1+i//3, column=(i%3)*2+1, sticky='w', padx=8)
            self.id_entries.append(entry)
        self.update_entries()
        actions = ttk.Frame(self.root)
        actions.pack(fill='x', padx=12)
        self.start_btn = ttk.Button(actions, text='測定開始', command=self.start)
        self.start_btn.pack(side='left', padx=3)
        self.stop_btn = ttk.Button(actions, text='中止', command=self.stop, state='disabled')
        self.stop_btn.pack(side='left', padx=3)
        ttk.Button(actions, text='CSV保存', command=self.save).pack(side='left', padx=3)
        ttk.Button(actions, text='結果クリア', command=self.clear).pack(side='left', padx=3)
        ttk.Label(actions, textvariable=self.status).pack(side='right')
        stats_frame = ttk.LabelFrame(self.root, text='統計結果（dBm）', padding=6)
        stats_frame.pack(fill='x', padx=12, pady=8)
        self.stats = ttk.Treeview(stats_frame, columns=('id','valid','min','max','mean','median'), show='headings', height=5)
        for key, title in [('id','ID'),('valid','取得成功回数'),('min','最小値'),('max','最大値'),('mean','平均値'),('median','中央値')]:
            self.stats.heading(key, text=title)
            self.stats.column(key, width=140, anchor='center')
        self.stats.pack(fill='x')
        history_controls = ttk.Frame(self.root)
        history_controls.pack(fill='x', padx=12, pady=(4, 0))
        self.history_visible = False
        self.history_button = ttk.Button(history_controls, text='▶ 取得成功履歴を表示', command=self.toggle_history)
        self.history_button.pack(side='left')
        self.history_count = tk.StringVar(value='0件の測定データ')
        ttk.Label(history_controls, textvariable=self.history_count).pack(side='left', padx=12)
        data_frame = ttk.LabelFrame(self.root, text='取得成功履歴（RSSI / dBm）', padding=6)
        self.data_frame = data_frame
        self.table = None
        self.rebuild_table([])
        # 初期状態では測定履歴を隠す．表示ボタンを押したときだけ展開する．

        self.note_label = ttk.Label(self.root, text='注：6桁IDはBLE広告データ内の識別子を想定しています．実機の広告形式に合わせて extract_id() を調整してください．', foreground='#555')
        self.note_label.pack(anchor='w', padx=14, pady=(0,8))

    def toggle_history(self):
        self.history_visible = not self.history_visible
        if self.history_visible:
            self.data_frame.pack(fill='both', expand=True, padx=12, pady=8, before=self.note_label)
            self.history_button.configure(text='▼ 取得成功履歴を隠す')
        else:
            self.data_frame.pack_forget()
            self.history_button.configure(text='▶ 取得成功履歴を表示')

    def update_entries(self):
        for i, entry in enumerate(self.id_entries):
            entry.configure(state='normal' if i < self.count.get() else 'disabled')

    def rebuild_table(self, ids):
        for widget in self.data_frame.winfo_children():
            widget.destroy()
        container = ttk.Frame(self.data_frame)
        container.pack(fill='both', expand=True)
        columns = ['no','elapsed','clock'] + ids
        self.table = ttk.Treeview(container, columns=columns, show='headings', height=12)
        scrollbar_y = ttk.Scrollbar(container, orient='vertical', command=self.table.yview)
        scrollbar_x = ttk.Scrollbar(container, orient='horizontal', command=self.table.xview)
        self.table.configure(yscrollcommand=scrollbar_y.set, xscrollcommand=scrollbar_x.set)
        for c in columns:
            title = {'no':'ID別成功回数','elapsed':'経過秒','clock':'時刻'}.get(c,c)
            self.table.heading(c, text=title)
            self.table.column(c, width=100 if c != 'clock' else 150, anchor='center')
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self.table.grid(row=0, column=0, sticky='nsew')
        scrollbar_y.grid(row=0, column=1, sticky='ns')
        scrollbar_x.grid(row=1, column=0, sticky='ew')

    @staticmethod
    def extract_id(device, advertisement):
        """Example: recognize a six-digit identifier in the BLE advertised name.
        Customize for manufacturer_data / service_data when required.
        """
        name = advertisement.local_name or device.name or ''
        match = re.search(r'(?<!\d)\d{6}(?!\d)', name)
        return match.group(0) if match else None

    def start(self):
        if self.running:
            return
        ids = [v.get().strip() for v in self.id_vars[:self.count.get()]]
        if any(not re.fullmatch(r'\d{6}', x) for x in ids):
            messagebox.showerror('入力エラー', 'ビーコンIDは6桁の数値で入力してください．')
            return
        if len(set(ids)) != len(ids):
            messagebox.showerror('入力エラー', 'IDが重複しています．')
            return
        try:
            n = int(self.n.get())
            interval = float(self.interval.get())
            if n < 1 or interval <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror('入力エラー', 'Nは1以上の整数，Mは0より大きい秒数にしてください．')
            return
        self.ids = ids
        self.rows = []
        self.history_count.set('0件の取得成功データ')
        self.goal = n
        self.rebuild_table(ids)
        for item in self.stats.get_children():
            self.stats.delete(item)
        self.stop_event.clear()
        self.running = True
        self.start_btn.configure(state='disabled')
        self.stop_btn.configure(state='normal')
        threading.Thread(target=self.worker, args=(ids,n,interval), daemon=True).start()

    def worker(self, ids, n, interval):
        try:
            asyncio.run(self.measure(ids,n,interval))
        except Exception as exc:
            self.events.put(('error', str(exc)))
        finally:
            self.events.put(('done', None))

    async def measure(self, ids, n, interval):
        """Record N fresh BLE advertisements PER beacon, at least M seconds apart.

        A BLE scanner receives asynchronous advertisements. Merely checking RSSI at
        fixed wall-clock slots loses observations; count only real advertisements.
        Each beacon has its own count and interval gate.
        """
        accepted = {beacon_id: 0 for beacon_id in ids}
        last_accepted = {beacon_id: float('-inf') for beacon_id in ids}
        finished = asyncio.Event()
        start = time.monotonic()
        total = len(ids) * n

        def callback(device, advertisement):
            if self.stop_event.is_set():
                finished.set()
                return
            beacon_id = self.extract_id(device, advertisement)
            if beacon_id not in accepted or accepted[beacon_id] >= n:
                return
            now = time.monotonic()
            if now - last_accepted[beacon_id] < interval:
                return
            # This value is from a real incoming advertisement, not cached RSSI.
            last_accepted[beacon_id] = now
            accepted[beacon_id] += 1
            row = {
                'no': accepted[beacon_id],
                'elapsed': round(now - start, 3),
                'clock': datetime.now().isoformat(timespec='milliseconds'),
                'beacon_id': beacon_id,
                'rssi': advertisement.rssi,
            }
            self.events.put(('row', row))
            if sum(accepted.values()) == total:
                finished.set()

        async with BleakScanner(detection_callback=callback):
            while not finished.is_set() and not self.stop_event.is_set():
                try:
                    await asyncio.wait_for(finished.wait(), timeout=0.2)
                except asyncio.TimeoutError:
                    continue

        if self.stop_event.is_set() and not finished.is_set():
            self.events.put(('status', '測定を中止しました（取得済みデータは保存可能）'))
        elif self.stop_event.is_set():
            self.events.put(('status', '測定を終了しました'))
        else:
            self.events.put(('status', f'測定完了：全{len(ids)}台で各{n}回取得'))

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == 'row':
                    self.rows.append(value)
                    self.history_count.set(f'{len(self.rows)}件の取得成功データ')
                    self.table.insert('', 'end', values=[value['no'],value['elapsed'],value['clock']] + [value['rssi'] if k == value['beacon_id'] else '' for k in self.ids])
                    self.table.yview_moveto(1)
                    self.refresh_stats()
                    self.status.set('取得成功：' + ' / '.join(f'{k}={sum(r["beacon_id"] == k for r in self.rows)}/{self.goal}' for k in self.ids))
                elif kind == 'status':
                    self.status.set(value)
                elif kind == 'error':
                    self.status.set('エラー')
                    messagebox.showerror('BLEスキャンエラー', value)
                elif kind == 'done':
                    self.running = False
                    self.start_btn.configure(state='normal')
                    self.stop_btn.configure(state='disabled')
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def refresh_stats(self):
        for item in self.stats.get_children():
            self.stats.delete(item)
        for beacon_id in self.ids:
            values = [r['rssi'] for r in self.rows if r['beacon_id'] == beacon_id]
            if values:
                self.stats.insert('', 'end', values=(beacon_id,len(values),min(values),max(values),round(statistics.mean(values),2),round(statistics.median(values),2)))
            else:
                self.stats.insert('', 'end', values=(beacon_id,0,'—','—','—','—'))

    def stop(self):
        self.stop_event.set()
        self.status.set('中止処理中')

    def clear(self):
        if self.running:
            messagebox.showwarning('測定中', '測定を中止してからクリアしてください．')
            return
        self.rows = []
        self.ids = []
        self.history_count.set('0件の取得成功データ')
        self.rebuild_table([])
        for item in self.stats.get_children():
            self.stats.delete(item)
        self.status.set('待機中')

    def save(self):
        if not self.rows:
            messagebox.showinfo('データなし', '保存する測定データがありません．')
            return
        path = filedialog.asksaveasfilename(defaultextension='.csv', filetypes=[('CSV','*.csv')], initialfile=f'beacon_{datetime.now():%Y%m%d_%H%M%S}.csv')
        if not path:
            return
        path = Path(path)
        with path.open('w',newline='',encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['ID別成功回数','経過秒','時刻']+self.ids)
            for r in self.rows:
                writer.writerow([r['no'],r['elapsed'],r['clock']]+[r['rssi'] if k == r['beacon_id'] else '' for k in self.ids])
        stats_path = path.with_name(path.stem+'_statistics.csv')
        with stats_path.open('w',newline='',encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            writer.writerow(['ID','取得成功回数','最小値','最大値','平均値','中央値'])
            for beacon_id in self.ids:
                vals = [r['rssi'] for r in self.rows if r['beacon_id'] == beacon_id]
                writer.writerow([beacon_id,len(vals),min(vals) if vals else '',max(vals) if vals else '',statistics.mean(vals) if vals else '',statistics.median(vals) if vals else ''])
        messagebox.showinfo('保存完了',f'{path.name}\n{stats_path.name}')

if __name__ == '__main__':
    root = tk.Tk()
    BeaconApp(root)
    root.mainloop()
