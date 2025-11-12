#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HeyTeaAutoDrawer 简洁 GUI（Tkinter）。"""
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import tkinter.font  # 导入字体模块
from PIL import Image, ImageTk
import os
import sys

# Ensure project root is on sys.path so local imports like `utils.*` work
sys.path.append(os.path.dirname(__file__))


from utils.config_utils import load_config, save_config, reset_config_file
from utils.coord_utils import capture_screen_region
from core.auto_drawer_canny import AutoDrawerCanny
from core.auto_drawer_scan import AutoDrawerScan


class HeyTeaGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HeyTea AutoDrawer - GUI")
        self.geometry("1100x700")

        # Load configuration
        self.config_data = load_config()

        self.image_path = None
        self.image_pil = None
        self.image_tk = None

        self._build_menu()
        self._build_ui()

    def _build_menu(self):
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="打开文件", command=self.open_file)
        menubar.add_cascade(label="文件", menu=file_menu)

        settings_menu = tk.Menu(menubar, tearoff=0)
        settings_menu.add_command(label="修改当前配置", command=self.open_modify_config)
        settings_menu.add_command(label="重置为默认（保留画板/尺寸）", command=self.reset_config_action)
        settings_menu.add_command(label="选择画板范围", command=self.reselect_board)
        menubar.add_cascade(label="设置", menu=settings_menu)

        # --- 新增: 帮助菜单 ---
        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="查看帮助", command=self.show_help_image)
        menubar.add_cascade(label="帮助", menu=help_menu)
        # --- 帮助菜单结束 ---

        self.config(menu=menubar)

    def _build_ui(self):
        # Paned layout: left main area, right config panel
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True)

        left_frame = ttk.Frame(paned)
        right_frame = ttk.Frame(paned)
        paned.add(left_frame, weight=9)
        paned.add(right_frame, weight=1)

        # 增大控件字体
        style = ttk.Style(self)
        default_font = tkinter.font.nametofont("TkDefaultFont")
        family = default_font.cget("family")
        size = default_font.cget("size")
        large_size = int(size * 2.0)
        style.configure('Large.TLabel', font=(family, large_size))
        style.configure('Large.TButton', font=(family, large_size), padding=(10, 5))
        style.configure('Large.TMenubutton', font=(family, large_size), padding=(10, 5))

        # 左侧：图片显示区
        img_frame = ttk.Frame(left_frame)
        img_frame.pack(fill=tk.BOTH, expand=False)
        self.canvas = tk.Canvas(img_frame, background="#222", height=500, width=600)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind('<Configure>', self._on_canvas_resize)

        # 控件栏
        ctrl_frame = ttk.Frame(left_frame)
        ctrl_frame.pack(padx=6, pady=6)
        ttk.Label(ctrl_frame, text="算法:", style='Large.TLabel').pack(side=tk.LEFT)
        self.algorithm_var = tk.StringVar(value="边缘")
        algo_menu = ttk.OptionMenu(ctrl_frame, self.algorithm_var, "边缘", "边缘", "扫描线", style='Large.TMenubutton')
        algo_menu.pack(side=tk.LEFT, padx=(4, 30))
        self.start_btn = ttk.Button(ctrl_frame, text="开始绘画", command=self.start_drawing, style='Large.TButton')
        self.start_btn.pack(side=tk.LEFT)

        # 日志区（只读）
        log_frame = ttk.LabelFrame(left_frame, text="日志 / 提示")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0,6))
        self.log_text = tk.Text(log_frame, height=10, wrap=tk.WORD)
        self.log_text.configure(state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text['yscrollcommand'] = log_scroll.set

        # 转发 print_utils 的消息到 GUI（线程安全）
        try:
            from utils import print_utils as print_utils
            def _gui_log_proxy(msg: str) -> None:
                try:
                    self.log_text.after(0, lambda m=msg: self.append_log(m))
                except Exception:
                    try:
                        self.append_log(msg)
                    except Exception:
                        pass
            print_utils.register_gui_logger(_gui_log_proxy)
        except Exception:
            pass

        # 右侧：配置面板
        cfg_label = ttk.Label(right_frame, text="当前配置", font=("Arial", 12, 'bold'))
        cfg_label.pack(anchor=tk.NW, padx=6, pady=6)
        cfg_canvas = tk.Canvas(right_frame, width=200)
        cfg_scroll = ttk.Scrollbar(right_frame, orient=tk.VERTICAL, command=cfg_canvas.yview)
        cfg_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cfg_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        cfg_canvas.configure(yscrollcommand=cfg_scroll.set)
        self.cfg_inner = ttk.Frame(cfg_canvas)
        cfg_canvas.create_window((0,0), window=self.cfg_inner, anchor='nw')
        self.cfg_inner.bind('<Configure>', lambda e: cfg_canvas.configure(scrollregion=cfg_canvas.bbox('all')))
        self._render_config_panel()

    def _on_canvas_resize(self, event):
        # Re-center image when canvas size changes
        self._draw_image_on_canvas()

    def _draw_image_on_canvas(self):
        self.canvas.delete('all')
        
        # 检查是否有图片
        if self.image_pil is None:
            return

        # 2. 获取画布的当前实际尺寸
        canvas_w = self.canvas.winfo_width()
        canvas_h = self.canvas.winfo_height()

        # 3. 如果画布还未绘制 (尺寸为1)，则不执行任何操作
        if canvas_w <= 1 or canvas_h <= 1:
            return
            
    # 自适应缩放保持纵横比
        img_copy = self.image_pil.copy()
        
        #    thumbnail 会在原地修改 img_copy，使其缩小到适应 (canvas_w, canvas_h)
        #    Image.LANCZOS 是高质量的缩放算法
        img_copy.thumbnail((canvas_w, canvas_h), Image.LANCZOS)
        
    # 转换为 PhotoImage 并居中显示
        self.image_tk = ImageTk.PhotoImage(img_copy)

        # 6. (居中) 计算画布的中心点
        cx = canvas_w / 2
        cy = canvas_h / 2
        
        # 7. 在中心点 (cx, cy) 创建图像，并使用 'center' 锚点
        self.canvas.create_image(cx, cy, image=self.image_tk, anchor=tk.CENTER)

    # （不再需要设置 scrollregion）

    def open_file(self):
        file_path = filedialog.askopenfilename(initialdir=os.path.join(os.getcwd(), 'pic'),
                                               filetypes=[('Image files', '*.png;*.jpg;*.jpeg;*.bmp;*.gif'), ('All files', '*.*')])
        if not file_path:
            return
        try:
            pil = Image.open(file_path)
            self.image_pil = pil
            # self.image_tk = ImageTk.PhotoImage(pil)
            self.image_path = file_path
            self.append_log(f"已打开图片: {file_path}")
            self._draw_image_on_canvas()
        except Exception as e:
            messagebox.showerror("打开失败", f"无法打开图片: {e}")

    def start_drawing(self):
        if not self.image_path:
            messagebox.showwarning("未选择图片", "请先通过 文件 -> 打开文件 选择图片。")
            return

        algo = self.algorithm_var.get()
        self.append_log(f"开始绘画 - 算法: {algo}")
        # Disable start button while drawing
        self.start_btn.config(state=tk.DISABLED)

        def run_draw():
            try:
                if algo == '边缘':
                    drawer = AutoDrawerCanny(self.config_data)
                    drawer.run(self.image_path)
                else:
                    drawer = AutoDrawerScan(self.config_data)
                    drawer.run(self.image_path)
                self.append_log("绘画完成")
            except Exception as e:
                self.append_log(f"绘画出错: {e}")
            finally:
                self.start_btn.config(state=tk.NORMAL)

        t = threading.Thread(target=run_draw, daemon=True)
        t.start()

    def append_log(self, text):
        # Temporarily enable the widget, insert, then disable to prevent user edits
        try:
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, text + '\n')
            self.log_text.see(tk.END)
        finally:
            try:
                self.log_text.configure(state=tk.DISABLED)
            except Exception:
                pass

    def _render_config_panel(self):
        # Clear
        for child in self.cfg_inner.winfo_children():
            child.destroy()

        row = 0
        for section, params in self.config_data.items():
            lbl = ttk.Label(self.cfg_inner, text=section, font=("Arial", 10, 'bold'))
            lbl.grid(row=row, column=0, sticky='w', padx=4, pady=(6,2))
            row += 1
            for k, v in params.items():
                key_lbl = ttk.Label(self.cfg_inner, text=f"{k}:")
                val_lbl = ttk.Label(self.cfg_inner, text=str(v))
                key_lbl.grid(row=row, column=0, sticky='w', padx=8)
                val_lbl.grid(row=row, column=1, sticky='w', padx=8)
                row += 1

        # refresh button
        ttk.Button(self.cfg_inner, text="刷新", command=self.refresh_config).grid(row=row, column=0, pady=8, padx=6, sticky='w')

    def refresh_config(self):
        self.config_data = load_config()
        self._render_config_panel()
        self.append_log("配置已刷新")

    def open_modify_config(self):
        # 修改配置窗口
        top = tk.Toplevel(self)
        top.title("修改当前配置")
        top.minsize(300, 400)

        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        canvas = tk.Canvas(frm)
        sb = ttk.Scrollbar(frm, orient=tk.VERTICAL, command=canvas.yview)
        inner = ttk.Frame(canvas)

        canvas.create_window((0, 0), window=inner, anchor='nw')
        canvas.configure(yscrollcommand=sb.set)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # 修复内嵌滚动区域
        inner.bind('<Configure>', lambda e: canvas.configure(scrollregion=canvas.bbox('all')))

        entries = {}
        r = 0
        for section, params in self.config_data.items():
            ttk.Label(inner, text=section, font=("Arial", 10, 'bold')).grid(row=r, column=0, columnspan=2, sticky='w', pady=(6, 2), padx=4)
            r += 1
            for k, v in params.items():
                ttk.Label(inner, text=k).grid(row=r, column=0, sticky='w', padx=8)

                # 布尔值用下拉，否则用输入框
                if isinstance(v, bool):
                    cb = ttk.Combobox(inner, values=["True", "False"], state="readonly")
                    cb.set(str(v))
                    cb.grid(row=r, column=1, sticky='we', padx=6, pady=2)
                    entries[(section, k)] = cb
                else:
                    ent = ttk.Entry(inner)
                    ent.insert(0, str(v))
                    ent.grid(row=r, column=1, sticky='we', padx=6, pady=2)
                    entries[(section, k)] = ent

                r += 1

        def save_and_close():
            # 保存并关闭
            for (section, key), widget in entries.items():
                raw = widget.get()
                try:
                    val = eval(raw)
                except Exception:
                    val = raw
                self.config_data[section][key] = val

            save_config(self.config_data)
            self.append_log("配置已保存")
            self._render_config_panel()
            top.destroy()

        # 保存按钮
        btn_row = ttk.Frame(top)
        btn_row.pack(fill=tk.X, padx=6, pady=6)
        ttk.Button(btn_row, text="保存", command=save_and_close).pack(side=tk.RIGHT)

    def show_help_image(self):
        # 显示帮助图片（pic/help.png）
        help_image_path = os.path.join(os.getcwd(), 'pic', 'help.png') 
        
        if not os.path.exists(help_image_path):
            messagebox.showerror("帮助文件丢失", "未找到 'pic/help.png' 文件。")
            self.append_log("错误: 未找到 'pic/help.png'")
            return
            
        try:
            top = tk.Toplevel(self)
            top.title("帮助说明")
            
            pil_img = Image.open(help_image_path)
            img_tk = ImageTk.PhotoImage(pil_img)
            
            lbl = tk.Label(top, image=img_tk)
            lbl.image = img_tk  # 保持引用
            lbl.pack()
            
            top.resizable(False, False) # 不允许调整窗口大小
            
        except Exception as e:
            messagebox.showerror("打开帮助失败", f"无法打开帮助图片: {e}")
            self.append_log(f"错误: 打开帮助图片失败 {e}")
    # 帮助方法

    def reselect_board(self):
        self.append_log("开始重新选择画板区域，请根据提示操作...")

        def do_capture():
            try:
                X_A, Y_A, W, H = capture_screen_region("config/config.py")
                # update config
                if 'screen_config' not in self.config_data:
                    self.config_data['screen_config'] = {}
                self.config_data['screen_config'].update({'X_A': X_A, 'Y_A': Y_A, 'W': W, 'H': H})
                save_config(self.config_data)
                self.append_log(f"画板坐标已更新: ({X_A}, {Y_A}), 尺寸 {W}×{H}")
                self._render_config_panel()
            except Exception as e:
                self.append_log(f"重选画板失败: {e}")

        t = threading.Thread(target=do_capture, daemon=True)
        t.start()

    def reset_config_action(self):
        """Reset config to defaults while preserving special keys, with confirmation."""
        if not messagebox.askyesno("确认重置", "是否要将配置重置为默认值？\n（将保留 H_IMG、W_IMG、THRESHOLD_VALUE 与 screen_config）"):
            return

        def do_reset():
            try:
                reset_config_file()
                # reload into GUI
                self.config_data = load_config()
                self._render_config_panel()
                self.append_log("配置已重置为默认（保留指定项）并已刷新")
            except Exception as e:
                self.append_log(f"重置配置失败: {e}")

        t = threading.Thread(target=do_reset, daemon=True)
        t.start()


def main():
    app = HeyTeaGUI()
    app.mainloop()


if __name__ == '__main__':
    main()
