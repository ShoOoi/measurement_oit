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
        root.title("Beacon RSSI Measurement")
        root.geometry("1220x800")

        self.events = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False

        self.rows = []
        self.ids = []
        self.measure_duration = 0.0

        # 基本設定
        self.beacon_count = tk.IntVar(value=1)
        self.measure_mode = tk.StringVar(value="time")  # time / count
        self.duration = tk.StringVar(value="30")
        self.target_samples = tk.StringVar(value="30")

        # 測定制御には使用しない．発信設定との比較・統計表示のための参考値
        self.adv_interval_ms = tk.StringVar(value="1000")

        self.id_vars = [tk.StringVar() for _ in range(MAX_BEACONS)]
        self.status = tk.StringVar(value="待機中")
        self.history_count = tk.StringVar(value="0件の受信データ")

        # 実行時の設定を記録
        self.current_mode = ""
        self.current_duration_setting = None
        self.current_target_samples = None

        self.build()
        self.root.after(100, self.poll)

    def build(self):
        config = ttk.LabelFrame(self.root, text="測定設定", padding=12)
        config.pack(fill="x", padx=12, pady=8)

        # 1段目：Beacon台数
        ttk.Label(config, text="ビーコン台数").grid(row=0, column=0, sticky="w")
        count_box = ttk.Combobox(
            config,
            textvariable=self.beacon_count,
            values=list(range(1, MAX_BEACONS + 1)),
            width=5,
            state="readonly",
        )
        count_box.grid(row=0, column=1, sticky="w", padx=(8, 20))
        count_box.bind("<<ComboboxSelected>>", lambda _: self.update_entries())

        # 1段目：測定モード
        ttk.Label(config, text="測定方法").grid(row=0, column=2, sticky="e")
        ttk.Radiobutton(
            config,
            text="時間指定",
            variable=self.measure_mode,
            value="time",
            command=self.update_mode,
        ).grid(row=0, column=3, sticky="w", padx=(8, 6))

        ttk.Radiobutton(
            config,
            text="回数指定",
            variable=self.measure_mode,
            value="count",
            command=self.update_mode,
        ).grid(row=0, column=4, sticky="w", padx=(6, 20))

        # 1段目：Advertising Interval（参考値）
        ttk.Label(config, text="Beacon発信間隔（参考・ms）").grid(
            row=0, column=5, sticky="e"
        )
        self.adv_entry = ttk.Entry(config, textvariable=self.adv_interval_ms, width=8)
        self.adv_entry.grid(row=0, column=6, sticky="w", padx=8)

        # 2段目：時間・回数
        ttk.Label(config, text="測定時間（秒）").grid(row=1, column=0, sticky="e", pady=(10, 0))
        self.duration_entry = ttk.Entry(config, textvariable=self.duration, width=8)
        self.duration_entry.grid(row=1, column=1, sticky="w", padx=(8, 20), pady=(10, 0))

        ttk.Label(config, text="取得回数（各Beacon）").grid(row=1, column=2, sticky="e", pady=(10, 0))
        self.samples_entry = ttk.Entry(config, textvariable=self.target_samples, width=8)
        self.samples_entry.grid(row=1, column=3, sticky="w", padx=8, pady=(10, 0))

        ttk.Label(
            config,
            text=(
                "時間指定：指定時間に受信できたAdvertisementをすべて保存 ／ "
                "回数指定：各Beaconが指定件数に達した時点で終了"
            ),
            foreground="#555",
        ).grid(row=2, column=0, columnspan=7, sticky="w", pady=(8, 4))

        ttk.Label(
            config,
            text=(
                "※ Beacon発信間隔は測定終了条件には使いません．"
                "OS・Bluetoothアダプタ・無線環境により全Advertisementを受信できるとは限りません．"
            ),
            foreground="#555",
        ).grid(row=3, column=0, columnspan=7, sticky="w", pady=(0, 8))

        # Beacon ID
        self.id_entries = []
        for i in range(MAX_BEACONS):
            r = 4 + i // 3
            c = (i % 3) * 2
            ttk.Label(config, text=f"ID {i + 1}").grid(row=r, column=c, sticky="e", pady=7)
            entry = ttk.Entry(config, textvariable=self.id_vars[i], width=14)
            entry.grid(row=r, column=c + 1, sticky="w", padx=8)
            self.id_entries.append(entry)

        self.update_entries()
        self.update_mode()

        # 操作ボタン
        actions = ttk.Frame(self.root)
        actions.pack(fill="x", padx=12)

        self.start_btn = ttk.Button(actions, text="測定開始", command=self.start)
        self.start_btn.pack(side="left", padx=3)

        self.stop_btn = ttk.Button(actions, text="中止", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=3)

        ttk.Button(actions, text="CSV保存", command=self.save).pack(side="left", padx=3)
        ttk.Button(actions, text="結果クリア", command=self.clear).pack(side="left", padx=3)

        ttk.Label(actions, textvariable=self.status).pack(side="right")

        # 統計結果
        stats_frame = ttk.LabelFrame(self.root, text="統計結果", padding=6)
        stats_frame.pack(fill="x", padx=12, pady=8)

        columns = (
            "id",
            "count",
            "min",
            "max",
            "mean",
            "median",
            "stdev",
            "mean_dt",
            "rate",
            "receive_ratio",
        )
        self.stats = ttk.Treeview(
            stats_frame,
            columns=columns,
            show="headings",
            height=5,
        )

        headers = [
            ("id", "ID", 100),
            ("count", "受信数", 75),
            ("min", "最小", 65),
            ("max", "最大", 65),
            ("mean", "平均", 75),
            ("median", "中央値", 75),
            ("stdev", "標準偏差", 80),
            ("mean_dt", "平均受信間隔(ms)", 120),
            ("rate", "実測受信頻度(Hz)", 110),
            ("receive_ratio", "受信率(参考)", 100),
        ]

        for key, title, width in headers:
            self.stats.heading(key, text=title)
            self.stats.column(key, width=width, anchor="center")

        self.stats.pack(fill="x")

        # 受信履歴
        history_controls = ttk.Frame(self.root)
        history_controls.pack(fill="x", padx=12, pady=(4, 0))

        self.history_visible = False
        self.history_button = ttk.Button(
            history_controls,
            text="▶ 受信履歴を表示",
            command=self.toggle_history,
        )
        self.history_button.pack(side="left")
        ttk.Label(history_controls, textvariable=self.history_count).pack(side="left", padx=12)

        self.data_frame = ttk.LabelFrame(
            self.root,
            text="受信履歴（Advertisement / RSSI）",
            padding=6,
        )
        self.table = None
        self.rebuild_table([])

        self.note_label = ttk.Label(
            self.root,
            text=(
                "授業推奨：条件比較は「時間指定」で同一時間（例：30秒）測定する．"
                "「回数指定」は，各Beaconから同じ件数のRSSIを集めたい場合に使用する．"
            ),
            foreground="#555",
        )
        self.note_label.pack(anchor="w", padx=14, pady=(0, 8))

    def update_mode(self):
        """選択中の測定方法に応じて入力欄を切り替える．"""
        if self.measure_mode.get() == "time":
            self.duration_entry.configure(state="normal")
            self.samples_entry.configure(state="disabled")
        else:
            self.duration_entry.configure(state="disabled")
            self.samples_entry.configure(state="normal")

    def toggle_history(self):
        self.history_visible = not self.history_visible
        if self.history_visible:
            self.data_frame.pack(
                fill="both",
                expand=True,
                padx=12,
                pady=8,
                before=self.note_label,
            )
            self.history_button.configure(text="▼ 受信履歴を隠す")
        else:
            self.data_frame.pack_forget()
            self.history_button.configure(text="▶ 受信履歴を表示")

    def update_entries(self):
        for i, entry in enumerate(self.id_entries):
            entry.configure(
                state="normal" if i < self.beacon_count.get() else "disabled"
            )

    def rebuild_table(self, ids):
        for widget in self.data_frame.winfo_children():
            widget.destroy()

        container = ttk.Frame(self.data_frame)
        container.pack(fill="both", expand=True)

        columns = ["seq", "elapsed", "clock", "beacon_id", "address", "rssi"]
        self.table = ttk.Treeview(
            container,
            columns=columns,
            show="headings",
            height=12,
        )

        scrollbar_y = ttk.Scrollbar(
            container,
            orient="vertical",
            command=self.table.yview,
        )
        scrollbar_x = ttk.Scrollbar(
            container,
            orient="horizontal",
            command=self.table.xview,
        )
        self.table.configure(
            yscrollcommand=scrollbar_y.set,
            xscrollcommand=scrollbar_x.set,
        )

        titles = {
            "seq": "通番",
            "elapsed": "経過秒",
            "clock": "時刻",
            "beacon_id": "ID",
            "address": "BLEアドレス",
            "rssi": "RSSI(dBm)",
        }
        widths = {
            "seq": 65,
            "elapsed": 90,
            "clock": 180,
            "beacon_id": 100,
            "address": 150,
            "rssi": 90,
        }

        for c in columns:
            self.table.heading(c, text=titles[c])
            self.table.column(c, width=widths[c], anchor="center")

        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)

        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar_y.grid(row=0, column=1, sticky="ns")
        scrollbar_x.grid(row=1, column=0, sticky="ew")

    @staticmethod
    def extract_id(device, advertisement):
        """
        KBeaconProのローカル名（例：KBPro_638111）から6桁IDを抽出する．
        """
        name = advertisement.local_name or device.name or ""

        m = re.fullmatch(r"KBPro_(\d{6})", name)
        if m:
            return m.group(1)

        # 将来，ローカル名の形式を変更した場合のフォールバック
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", name)
        return m.group(1) if m else None

    def start(self):
        if self.running:
            return

        ids = [
            v.get().strip()
            for v in self.id_vars[: self.beacon_count.get()]
        ]

        if any(not re.fullmatch(r"\d{6}", x) for x in ids):
            messagebox.showerror(
                "入力エラー",
                "ビーコンIDは6桁の数値で入力してください．",
            )
            return

        if len(set(ids)) != len(ids):
            messagebox.showerror(
                "入力エラー",
                "IDが重複しています．",
            )
            return

        mode = self.measure_mode.get()

        duration = None
        target_samples = None

        try:
            if mode == "time":
                duration = float(self.duration.get())
                if duration <= 0:
                    raise ValueError
            elif mode == "count":
                target_samples = int(self.target_samples.get())
                if target_samples <= 0:
                    raise ValueError
            else:
                raise ValueError

            # 発信間隔は統計用の参考値．空欄も許容する．
            adv_text = self.adv_interval_ms.get().strip()
            if adv_text:
                adv_interval_ms = float(adv_text)
                if adv_interval_ms <= 0:
                    raise ValueError
        except ValueError:
            if mode == "time":
                msg = "測定時間には0より大きい数値を入力してください．"
            else:
                msg = "取得回数には1以上の整数を入力してください．"
            msg += "\nBeacon発信間隔を入力する場合は0より大きい数値にしてください．"
            messagebox.showerror("入力エラー", msg)
            return

        self.ids = ids
        self.rows = []
        self.measure_duration = 0.0

        self.current_mode = mode
        self.current_duration_setting = duration
        self.current_target_samples = target_samples

        self.history_count.set("0件の受信データ")
        self.rebuild_table(ids)

        for item in self.stats.get_children():
            self.stats.delete(item)

        self.stop_event.clear()
        self.running = True

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        threading.Thread(
            target=self.worker,
            args=(ids, mode, duration, target_samples),
            daemon=True,
        ).start()

    def worker(self, ids, mode, duration, target_samples):
        try:
            asyncio.run(
                self.measure(
                    ids=ids,
                    mode=mode,
                    duration=duration,
                    target_samples=target_samples,
                )
            )
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("done", None))

    async def measure(self, ids, mode, duration, target_samples):
        """
        time:
            指定時間内に受信したAdvertisementをすべて記録する．
        count:
            各Beaconについて指定件数のAdvertisementを記録し，
            全Beaconが目標件数に達した時点で終了する．

        いずれもプログラム側で時間間引きは行わない．
        """
        counts = {beacon_id: 0 for beacon_id in ids}
        seq = 0
        start = time.monotonic()

        if mode == "time":
            end_at = start + duration
        else:
            end_at = None

        def callback(device, advertisement):
            nonlocal seq

            if self.stop_event.is_set():
                return

            beacon_id = self.extract_id(device, advertisement)
            if beacon_id not in counts:
                return

            # 回数指定モードでは，各Beaconが目標数に達した後の余分なデータは保存しない
            if mode == "count" and counts[beacon_id] >= target_samples:
                return

            now = time.monotonic()

            counts[beacon_id] += 1
            seq += 1

            self.events.put(
                (
                    "row",
                    {
                        "seq": seq,
                        "elapsed": round(now - start, 4),
                        "clock": datetime.now().isoformat(timespec="milliseconds"),
                        "beacon_id": beacon_id,
                        "address": device.address or "",
                        "rssi": advertisement.rssi,
                    },
                )
            )

        async with BleakScanner(detection_callback=callback):
            while not self.stop_event.is_set():
                if mode == "time":
                    remaining = end_at - time.monotonic()
                    if remaining <= 0:
                        break
                    await asyncio.sleep(min(0.05, remaining))
                else:
                    if all(counts[x] >= target_samples for x in ids):
                        break
                    await asyncio.sleep(0.05)

        actual_duration = max(0.0, time.monotonic() - start)
        self.measure_duration = actual_duration

        if self.stop_event.is_set():
            self.events.put(
                (
                    "status",
                    f"測定を中止しました（実測時間 {actual_duration:.1f}秒）",
                )
            )
        elif mode == "time":
            self.events.put(
                (
                    "status",
                    f"測定完了：{actual_duration:.1f}秒間に受信したAdvertisementを記録",
                )
            )
        else:
            self.events.put(
                (
                    "status",
                    f"測定完了：各Beacon {target_samples}件を取得 "
                    f"（所要時間 {actual_duration:.1f}秒）",
                )
            )

    def poll(self):
        try:
            while True:
                kind, value = self.events.get_nowait()

                if kind == "row":
                    self.rows.append(value)
                    self.history_count.set(f"{len(self.rows)}件の受信データ")

                    self.table.insert(
                        "",
                        "end",
                        values=[
                            value["seq"],
                            value["elapsed"],
                            value["clock"],
                            value["beacon_id"],
                            value["address"],
                            value["rssi"],
                        ],
                    )
                    self.table.yview_moveto(1)
                    self.refresh_stats()

                    counts = [
                        f'{beacon_id}={sum(r["beacon_id"] == beacon_id for r in self.rows)}件'
                        for beacon_id in self.ids
                    ]

                    if self.current_mode == "count" and self.current_target_samples:
                        self.status.set(
                            "受信中："
                            + " / ".join(counts)
                            + f" （目標 各{self.current_target_samples}件）"
                        )
                    else:
                        self.status.set("受信中：" + " / ".join(counts))

                elif kind == "status":
                    self.status.set(value)

                elif kind == "error":
                    self.status.set("エラー")
                    messagebox.showerror("BLEスキャンエラー", value)

                elif kind == "done":
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.stop_btn.configure(state="disabled")
                    self.refresh_stats()

        except queue.Empty:
            pass

        self.root.after(100, self.poll)

    def get_adv_interval(self):
        text = self.adv_interval_ms.get().strip()
        if not text:
            return None
        try:
            value = float(text)
            return value if value > 0 else None
        except ValueError:
            return None

    def refresh_stats(self):
        for item in self.stats.get_children():
            self.stats.delete(item)

        adv_interval_ms = self.get_adv_interval()

        elapsed_now = max(
            [r["elapsed"] for r in self.rows],
            default=0.0,
        )
        duration = (
            self.measure_duration
            if self.measure_duration > 0
            else elapsed_now
        )

        for beacon_id in self.ids:
            rows = [
                r for r in self.rows
                if r["beacon_id"] == beacon_id
            ]
            values = [r["rssi"] for r in rows]
            times = [r["elapsed"] for r in rows]

            if not values:
                self.stats.insert(
                    "",
                    "end",
                    values=(
                        beacon_id,
                        0,
                        "—",
                        "—",
                        "—",
                        "—",
                        "—",
                        "—",
                        "—",
                        "—",
                    ),
                )
                continue

            stdev = (
                statistics.stdev(values)
                if len(values) >= 2
                else 0.0
            )

            if len(times) >= 2:
                intervals_ms = [
                    (times[i] - times[i - 1]) * 1000
                    for i in range(1, len(times))
                    if times[i] > times[i - 1]
                ]
                mean_dt = (
                    statistics.mean(intervals_ms)
                    if intervals_ms
                    else None
                )
            else:
                mean_dt = None

            rate = (
                len(values) / duration
                if duration > 0
                else None
            )

            # 理論発信回数に対する観測回数．
            # OSが全広告を通知する保証はないため「受信率（参考）」とする．
            if (
                duration > 0
                and adv_interval_ms is not None
            ):
                expected = duration / (adv_interval_ms / 1000.0)
                receive_ratio = (
                    100.0 * len(values) / expected
                    if expected > 0
                    else None
                )
            else:
                receive_ratio = None

            self.stats.insert(
                "",
                "end",
                values=(
                    beacon_id,
                    len(values),
                    min(values),
                    max(values),
                    round(statistics.mean(values), 2),
                    round(statistics.median(values), 2),
                    round(stdev, 2),
                    round(mean_dt, 1) if mean_dt is not None else "—",
                    round(rate, 2) if rate is not None else "—",
                    f"{receive_ratio:.1f}%" if receive_ratio is not None else "—",
                ),
            )

    def stop(self):
        self.stop_event.set()
        self.status.set("中止処理中")

    def clear(self):
        if self.running:
            messagebox.showwarning(
                "測定中",
                "測定を中止してからクリアしてください．",
            )
            return

        self.rows = []
        self.ids = []
        self.measure_duration = 0.0
        self.current_mode = ""
        self.current_duration_setting = None
        self.current_target_samples = None

        self.history_count.set("0件の受信データ")
        self.rebuild_table([])

        for item in self.stats.get_children():
            self.stats.delete(item)

        self.status.set("待機中")

    def save(self):
        if not self.rows:
            messagebox.showinfo(
                "データなし",
                "保存する測定データがありません．",
            )
            return

        path = filedialog.asksaveasfilename(
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
            initialfile=f"beacon_{datetime.now():%Y%m%d_%H%M%S}.csv",
        )

        if not path:
            return

        path = Path(path)

        # 生データ：long形式
        with path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.writer(f)
            writer.writerow(
                [
                    "通番",
                    "経過秒",
                    "時刻",
                    "ID",
                    "BLEアドレス",
                    "RSSI_dBm",
                ]
            )
            for r in self.rows:
                writer.writerow(
                    [
                        r["seq"],
                        r["elapsed"],
                        r["clock"],
                        r["beacon_id"],
                        r["address"],
                        r["rssi"],
                    ]
                )

        stats_path = path.with_name(
            path.stem + "_statistics.csv"
        )

        adv_interval_ms = self.get_adv_interval()

        with stats_path.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:
            writer = csv.writer(f)

            writer.writerow(
                [
                    "測定モード",
                    "設定値",
                    "ID",
                    "受信数",
                    "最小RSSI",
                    "最大RSSI",
                    "平均RSSI",
                    "中央値RSSI",
                    "標準偏差",
                    "平均受信間隔_ms",
                    "実測受信頻度_Hz",
                    "Beacon発信間隔設定_ms",
                    "受信率_参考_percent",
                    "実測時間_s",
                ]
            )

            mode_label = (
                "時間指定"
                if self.current_mode == "time"
                else "回数指定"
            )
            if self.current_mode == "time":
                setting_label = (
                    f"{self.current_duration_setting}秒"
                    if self.current_duration_setting is not None
                    else ""
                )
            else:
                setting_label = (
                    f"各{self.current_target_samples}件"
                    if self.current_target_samples is not None
                    else ""
                )

            for beacon_id in self.ids:
                rows = [
                    r for r in self.rows
                    if r["beacon_id"] == beacon_id
                ]
                vals = [r["rssi"] for r in rows]
                times = [r["elapsed"] for r in rows]

                if len(times) >= 2:
                    intervals_ms = [
                        (times[i] - times[i - 1]) * 1000
                        for i in range(1, len(times))
                        if times[i] > times[i - 1]
                    ]
                    mean_dt = (
                        statistics.mean(intervals_ms)
                        if intervals_ms
                        else ""
                    )
                else:
                    mean_dt = ""

                stdev = (
                    statistics.stdev(vals)
                    if len(vals) >= 2
                    else (0.0 if vals else "")
                )

                rate = (
                    len(vals) / self.measure_duration
                    if self.measure_duration > 0
                    else ""
                )

                if (
                    self.measure_duration > 0
                    and adv_interval_ms is not None
                ):
                    expected = (
                        self.measure_duration
                        / (adv_interval_ms / 1000.0)
                    )
                    ratio = (
                        100.0 * len(vals) / expected
                        if expected > 0
                        else ""
                    )
                else:
                    ratio = ""

                writer.writerow(
                    [
                        mode_label,
                        setting_label,
                        beacon_id,
                        len(vals),
                        min(vals) if vals else "",
                        max(vals) if vals else "",
                        statistics.mean(vals) if vals else "",
                        statistics.median(vals) if vals else "",
                        stdev,
                        mean_dt,
                        rate,
                        adv_interval_ms if adv_interval_ms is not None else "",
                        ratio,
                        self.measure_duration,
                    ]
                )

        messagebox.showinfo(
            "保存完了",
            f"{path.name}\n{stats_path.name}",
        )


if __name__ == "__main__":
    root = tk.Tk()
    BeaconApp(root)
    root.mainloop()
