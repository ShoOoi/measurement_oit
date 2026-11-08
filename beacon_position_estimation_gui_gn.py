import math
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import csv

APP_TITLE = "Beacon 3点位置推定 - グリッドサーチ／ガウス・ニュートン法"

def rssi_to_distance(rssi_1m, rssi, n):
    """
    対数距離モデル
    d = 10 ^ ((A - RSSI) / (10n))
    A: 1m地点の基準RSSI
    """
    return 10 ** ((rssi_1m - rssi) / (10.0 * n))

def geometric_distance(x, y, bx, by):
    return math.hypot(x - bx, y - by)

def cost_function(x, y, beacons):
    """
    J(x,y) = Σ (幾何学距離 - RSSIからの推定距離)^2
    """
    total = 0.0
    residuals = []
    for b in beacons:
        r = geometric_distance(x, y, b["x"], b["y"])
        v = r - b["d"]
        residuals.append(v)
        total += v * v
    return total, residuals

def grid_search(beacons, xmin, xmax, ymin, ymax, step):
    if step <= 0:
        raise ValueError("グリッド幅は0より大きくしてください．")
    if xmax < xmin or ymax < ymin:
        raise ValueError("探索範囲の最大値は最小値以上にしてください．")

    best = None
    rows = []

    nx = int(round((xmax - xmin) / step)) + 1
    ny = int(round((ymax - ymin) / step)) + 1

    for iy in range(ny):
        y = ymin + iy * step
        if y > ymax + 1e-12:
            break
        for ix in range(nx):
            x = xmin + ix * step
            if x > xmax + 1e-12:
                break

            j, residuals = cost_function(x, y, beacons)
            rows.append((x, y, j, *residuals))

            if best is None or j < best["cost"]:
                best = {
                    "x": x,
                    "y": y,
                    "cost": j,
                    "residuals": residuals,
                }

    return best, rows

def gauss_newton(beacons, x0, y0, max_iter=50, tol=1e-6):
    """ガウス・ニュートン法による非線形最小二乗推定．"""
    x = float(x0)
    y = float(y0)
    history = []
    converged = False

    for iteration in range(1, max_iter + 1):
        a11 = a12 = a22 = 0.0
        b1 = b2 = 0.0
        cost = 0.0

        for b in beacons:
            dx_b = x - b["x"]
            dy_b = y - b["y"]
            r = math.hypot(dx_b, dy_b)
            if r < 1e-12:
                r = 1e-12

            v = r - b["d"]
            jx = dx_b / r
            jy = dy_b / r
            cost += v * v

            a11 += jx * jx
            a12 += jx * jy
            a22 += jy * jy
            b1 += -jx * v
            b2 += -jy * v

        det = a11 * a22 - a12 * a12
        if abs(det) < 1e-12:
            raise ValueError(
                "ガウス・ニュートン法の正規方程式が特異になりました．"
                "Beaconが一直線上に近い，または初期値が不適切な可能性があります．"
            )

        delta_x = (b1 * a22 - a12 * b2) / det
        delta_y = (a11 * b2 - a12 * b1) / det

        history.append({
            "iteration": iteration, "x": x, "y": y, "cost": cost,
            "delta_x": delta_x, "delta_y": delta_y,
        })

        x += delta_x
        y += delta_y

        if math.hypot(delta_x, delta_y) < tol:
            converged = True
            break

    final_cost, final_residuals = cost_function(x, y, beacons)
    return {
        "x": x, "y": y, "cost": final_cost,
        "residuals": final_residuals,
        "iterations": len(history),
        "converged": converged,
        "history": history,
        "initial_x": x0, "initial_y": y0,
    }

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1080x760")
        self.minsize(1000, 700)

        self.last_grid_rows = []
        self.last_gn_history = []
        self.last_beacons = []
        self.last_method = None
        self._build_ui()

    def _build_ui(self):
        root = ttk.Frame(self, padding=10)
        root.pack(fill="both", expand=True)

        title = ttk.Label(
            root,
            text="3台のBeaconからPC／人の位置を推定",
            font=("", 18, "bold"),
        )
        title.pack(anchor="w", pady=(0, 8))

        note = ttk.Label(
            root,
            text=(
                "Beaconの座標は既知として入力します．PC／人は測定地点で静止したままです．"
                "通常はグリッドサーチで推定し，「ガウス・ニュートン法を使う」にチェックすると"
                "反復計算によって残差平方和が小さくなる位置を推定します．"
            ),
            wraplength=1020,
        )
        note.pack(anchor="w", pady=(0, 10))

        main = ttk.Frame(root)
        main.pack(fill="both", expand=True)

        left = ttk.Frame(main)
        left.pack(side="left", fill="both", expand=True)

        right = ttk.Frame(main, padding=(10, 0, 0, 0))
        right.pack(side="right", fill="y")

        # Beacon入力
        lf_b = ttk.LabelFrame(left, text="Beacon設定")
        lf_b.pack(fill="x", pady=(0, 10))

        headers = ["Beacon", "X [m]", "Y [m]", "1m基準RSSI A [dBm]", "測定RSSI [dBm]", "推定距離 d [m]"]
        for c, h in enumerate(headers):
            ttk.Label(lf_b, text=h, font=("", 10, "bold")).grid(row=0, column=c, padx=5, pady=5)

        defaults = [
            ("A", "0.0", "0.0", "-59", "-67"),
            ("B", "4.0", "0.0", "-59", "-69"),
            ("C", "0.0", "3.0", "-59", "-66"),
        ]

        self.beacon_vars = []
        self.distance_labels = []

        for i, vals in enumerate(defaults, start=1):
            name, x, y, a, rssi = vals
            ttk.Label(lf_b, text=name, width=8).grid(row=i, column=0, padx=5, pady=4)

            vx = tk.StringVar(value=x)
            vy = tk.StringVar(value=y)
            va = tk.StringVar(value=a)
            vr = tk.StringVar(value=rssi)

            ttk.Entry(lf_b, textvariable=vx, width=10).grid(row=i, column=1, padx=5, pady=4)
            ttk.Entry(lf_b, textvariable=vy, width=10).grid(row=i, column=2, padx=5, pady=4)
            ttk.Entry(lf_b, textvariable=va, width=15).grid(row=i, column=3, padx=5, pady=4)
            ttk.Entry(lf_b, textvariable=vr, width=15).grid(row=i, column=4, padx=5, pady=4)

            dl = ttk.Label(lf_b, text="—", width=14)
            dl.grid(row=i, column=5, padx=5, pady=4)

            self.beacon_vars.append((name, vx, vy, va, vr))
            self.distance_labels.append(dl)

        # モデル設定
        lf_model = ttk.LabelFrame(left, text="距離モデル・探索設定")
        lf_model.pack(fill="x", pady=(0, 10))

        self.n_var = tk.StringVar(value="2.0")
        self.xmin_var = tk.StringVar(value="0.0")
        self.xmax_var = tk.StringVar(value="4.0")
        self.ymin_var = tk.StringVar(value="0.0")
        self.ymax_var = tk.StringVar(value="3.0")
        self.step_var = tk.StringVar(value="0.1")

        fields = [
            ("減衰係数 n", self.n_var),
            ("X 最小 [m]", self.xmin_var),
            ("X 最大 [m]", self.xmax_var),
            ("Y 最小 [m]", self.ymin_var),
            ("Y 最大 [m]", self.ymax_var),
            ("グリッド幅 [m]", self.step_var),
        ]

        for idx, (label, var) in enumerate(fields):
            r = idx // 3
            c = (idx % 3) * 2
            ttk.Label(lf_model, text=label).grid(row=r, column=c, padx=5, pady=5, sticky="e")
            ttk.Entry(lf_model, textvariable=var, width=12).grid(row=r, column=c+1, padx=5, pady=5, sticky="w")

        ttk.Label(
            lf_model,
            text="授業では0.5m刻みで考え方を説明し，実際の計算では0.1m程度から試すのがおすすめです．",
            wraplength=700,
        ).grid(row=2, column=0, columnspan=6, padx=5, pady=(4, 8), sticky="w")

        self.use_gn_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            lf_model,
            text="ガウス・ニュートン法を使う",
            variable=self.use_gn_var,
        ).grid(row=3, column=0, columnspan=2, padx=5, pady=(2, 6), sticky="w")

        ttk.Label(
            lf_model,
            text="※チェック時：探索範囲の中央を初期値として反復計算します．グリッド幅は使用しません．",
            wraplength=560,
        ).grid(row=3, column=2, columnspan=4, padx=5, pady=(2, 6), sticky="w")

        # Ground Truth
        lf_gt = ttk.LabelFrame(left, text="Ground Truth（任意）")
        lf_gt.pack(fill="x", pady=(0, 10))

        self.gt_enable = tk.BooleanVar(value=True)
        self.gt_x = tk.StringVar(value="2.0")
        self.gt_y = tk.StringVar(value="1.5")

        ttk.Checkbutton(lf_gt, text="実位置と比較する", variable=self.gt_enable).grid(row=0, column=0, padx=5, pady=5)
        ttk.Label(lf_gt, text="X [m]").grid(row=0, column=1, padx=5)
        ttk.Entry(lf_gt, textvariable=self.gt_x, width=10).grid(row=0, column=2, padx=5)
        ttk.Label(lf_gt, text="Y [m]").grid(row=0, column=3, padx=5)
        ttk.Entry(lf_gt, textvariable=self.gt_y, width=10).grid(row=0, column=4, padx=5)

        # Buttons
        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(0, 10))

        ttk.Button(btns, text="距離を計算", command=self.calc_distances).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="位置推定を実行", command=self.run_estimation).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="結果CSV保存", command=self.save_csv).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="結果クリア", command=self.clear_result).pack(side="left")

        # Result
        lf_res = ttk.LabelFrame(left, text="推定結果")
        lf_res.pack(fill="both", expand=True)

        self.result_text = tk.Text(lf_res, height=18, wrap="word")
        self.result_text.pack(fill="both", expand=True, padx=6, pady=6)

        # Right explanatory panel
        lf_help = ttk.LabelFrame(right, text="計算の流れ")
        lf_help.pack(fill="both", expand=True)

        help_text = (
            "1．Beacon A/B/C の座標を入力\n\n"
            "2．各Beaconの1m基準RSSI Aを入力\n\n"
            "3．測定地点で30秒程度RSSIを取得し，中央値などの代表値を入力\n\n"
            "4．RSSIから距離 dA，dB，dC を推定\n\n"
            "5．推定方法を選ぶ\n"
            "　・OFF：グリッドサーチ\n"
            "　・ON：ガウス・ニュートン法\n\n"
            "6．J(x,y)=Σ(幾何学距離－RSSI推定距離)^2 を小さくする位置を求める\n\n"
            "グリッドサーチ：候補点を順に評価\n"
            "ガウス・ニュートン法：初期値から反復更新\n\n"
            "※ どちらの場合も，人がグリッド状に歩くわけではありません．"
        )
        ttk.Label(lf_help, text=help_text, wraplength=260, justify="left").pack(
            fill="both", expand=True, padx=10, pady=10, anchor="n"
        )

    def _read_inputs(self):
        n = float(self.n_var.get())
        if n <= 0:
            raise ValueError("減衰係数 n は0より大きくしてください．")

        beacons = []
        for name, vx, vy, va, vr in self.beacon_vars:
            bx = float(vx.get())
            by = float(vy.get())
            a = float(va.get())
            rssi = float(vr.get())
            d = rssi_to_distance(a, rssi, n)
            beacons.append({
                "name": name,
                "x": bx,
                "y": by,
                "A": a,
                "rssi": rssi,
                "d": d,
            })

        xmin = float(self.xmin_var.get())
        xmax = float(self.xmax_var.get())
        ymin = float(self.ymin_var.get())
        ymax = float(self.ymax_var.get())
        step = float(self.step_var.get())

        return beacons, xmin, xmax, ymin, ymax, step

    def calc_distances(self):
        try:
            beacons, *_ = self._read_inputs()
            for i, b in enumerate(beacons):
                self.distance_labels[i].config(text=f"{b['d']:.3f}")
        except Exception as e:
            messagebox.showerror("入力エラー", str(e))

    def run_estimation(self):
        try:
            beacons, xmin, xmax, ymin, ymax, step = self._read_inputs()

            for i, b in enumerate(beacons):
                self.distance_labels[i].config(text=f"{b['d']:.3f}")

            self.result_text.delete("1.0", "end")
            self.result_text.insert("end", "【RSSIから推定した距離】\n")
            for b in beacons:
                self.result_text.insert(
                    "end",
                    f"Beacon {b['name']}：座標=({b['x']:.2f}, {b['y']:.2f})，"
                    f"A={b['A']:.1f} dBm，RSSI={b['rssi']:.1f} dBm，"
                    f"d={b['d']:.3f} m\n"
                )

            self.last_beacons = beacons

            if self.use_gn_var.get():
                x0 = (xmin + xmax) / 2.0
                y0 = (ymin + ymax) / 2.0
                result = gauss_newton(beacons, x0, y0, max_iter=50, tol=1e-6)

                self.last_method = "gauss_newton"
                self.last_grid_rows = []
                self.last_gn_history = result["history"]

                self.result_text.insert("end", "\n【ガウス・ニュートン法の結果】\n")
                self.result_text.insert(
                    "end",
                    f"初期値：({x0:.3f}, {y0:.3f}) m\n"
                    f"推定位置：({result['x']:.3f}, {result['y']:.3f}) m\n"
                    f"最小残差平方和 J：{result['cost']:.6f}\n"
                    f"反復回数：{result['iterations']} 回\n"
                    f"収束：{'はい' if result['converged'] else 'いいえ'}\n"
                )

                for i, r in enumerate(result["residuals"]):
                    self.result_text.insert(
                        "end",
                        f"Beacon {beacons[i]['name']} の距離残差：{r:+.3f} m\n"
                    )

                if not (xmin <= result["x"] <= xmax and ymin <= result["y"] <= ymax):
                    self.result_text.insert(
                        "end",
                        "\n※注意：推定位置が入力した探索範囲の外にあります．"
                        "初期値，Beacon配置，RSSI距離を確認してください．\n"
                    )

                est_x, est_y = result["x"], result["y"]

            else:
                best, rows = grid_search(beacons, xmin, xmax, ymin, ymax, step)
                self.last_method = "grid_search"
                self.last_grid_rows = rows
                self.last_gn_history = []

                self.result_text.insert("end", "\n【グリッドサーチ結果】\n")
                self.result_text.insert(
                    "end",
                    f"推定位置：({best['x']:.3f}, {best['y']:.3f}) m\n"
                    f"最小残差平方和 J：{best['cost']:.6f}\n"
                )
                for i, r in enumerate(best["residuals"]):
                    self.result_text.insert(
                        "end",
                        f"Beacon {beacons[i]['name']} の距離残差：{r:+.3f} m\n"
                    )
                self.result_text.insert(
                    "end",
                    f"\n探索範囲：X={xmin}～{xmax} m，Y={ymin}～{ymax} m\n"
                    f"グリッド幅：{step} m\n"
                    f"評価候補数：{len(rows)} 点\n"
                )
                est_x, est_y = best["x"], best["y"]

            if self.gt_enable.get():
                x_true = float(self.gt_x.get())
                y_true = float(self.gt_y.get())
                pos_err = math.hypot(est_x - x_true, est_y - y_true)
                self.result_text.insert("end", "\n【Ground Truthとの比較】\n")
                self.result_text.insert(
                    "end",
                    f"実位置：({x_true:.3f}, {y_true:.3f}) m\n"
                    f"位置誤差：{pos_err:.3f} m\n"
                )

        except Exception as e:
            messagebox.showerror("計算エラー", str(e))

    def save_csv(self):
        if self.last_method is None:
            messagebox.showinfo("保存", "先に位置推定を実行してください．")
            return

        path = filedialog.asksaveasfilename(
            title="位置推定結果を保存",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                if self.last_method == "grid_search":
                    writer.writerow([
                        "x_m", "y_m", "cost",
                        "residual_A_m", "residual_B_m", "residual_C_m"
                    ])
                    writer.writerows(self.last_grid_rows)
                else:
                    writer.writerow([
                        "iteration", "x_m", "y_m", "cost",
                        "delta_x_m", "delta_y_m"
                    ])
                    for h in self.last_gn_history:
                        writer.writerow([
                            h["iteration"], h["x"], h["y"], h["cost"],
                            h["delta_x"], h["delta_y"]
                        ])
            messagebox.showinfo("保存完了", f"保存しました．\n{path}")
        except Exception as e:
            messagebox.showerror("保存エラー", str(e))

    def clear_result(self):
        self.result_text.delete("1.0", "end")
        self.last_grid_rows = []
        self.last_gn_history = []
        self.last_method = None
        for lbl in self.distance_labels:
            lbl.config(text="—")

if __name__ == "__main__":
    app = App()
    app.mainloop()
