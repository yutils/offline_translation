import os
import sys
import threading
import contextlib
import io
import re
from pathlib import Path
import tkinter as tk
import customtkinter as ctk 
import pyperclip

# =================配置环境=================
CURRENT_DIR = Path.cwd()
os.environ["SNAP"] = "Argos_Wrapper"
os.environ["SNAP_USER_DATA"] = str(CURRENT_DIR / ".home_dir")

import argostranslate.package
import argostranslate.translate

# =================后端逻辑部分=================

@contextlib.contextmanager
def suppress_stderr():
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()
    try:
        yield
    finally:
        sys.stderr = old_stderr

class TranslatorBackend:
    def __init__(self):
        self.is_ready = False
        # 通用分隔符：匹配中文字符 (\u4e00-\u9fff) 或 英文字母 ([a-zA-Z])
        # \s 包含空格和换行符，它被包含在英文/非中文块中，确保格式被保留。
        self.split_pattern = re.compile(r'([a-zA-Z\s]+|[\u4e00-\u9fff\u3002\uff1f\uff01\uff0c\u3001\uff1b\uff1a\u201c\u201d\u2018\u2019\uff08\uff09\u300a\u300b]+)')

    def check_and_install_models(self, status_callback):
        try:
            status_callback("正在检查离线模型...")
            if self.model_installed("zh", "en") and self.model_installed("en", "zh"):
                status_callback("模型已就绪")
                self.is_ready = True
                return

            status_callback("首次运行，正在下载模型(可能需几分钟)...")
            with suppress_stderr():
                argostranslate.package.update_package_index()
                available_packages = argostranslate.package.get_available_packages()

            target_pkgs = [
                pkg for pkg in available_packages
                if (pkg.from_code == "zh" and pkg.to_code == "en") or
                   (pkg.from_code == "en" and pkg.to_code == "zh")
            ]

            for pkg in target_pkgs:
                status_callback(f"下载安装：{pkg.from_code} → {pkg.to_code} ...")
                with suppress_stderr():
                    pkg.install()
            
            status_callback("模型安装完成！")
            self.is_ready = True

        except Exception as e:
            status_callback(f"模型加载失败: {str(e)}")
            print(e)

    def model_installed(self, from_code, to_code):
        installed = argostranslate.package.get_installed_packages()
        for pkg in installed:
            if pkg.from_code == from_code and pkg.to_code == to_code:
                return True
        return False

    def detect_language(self, text: str) -> str:
        """简单的语言检测：如果有中文字符，就认为是中文"""
        if any('\u4e00' <= c <= '\u9fff' for c in text):
            return "zh"
        return "en"

    def is_chunk_language(self, text: str, lang_code: str) -> bool:
        """判断一个片段主要是否属于给定语言 (zh 或 en)"""
        if lang_code == "zh":
            return bool(re.search(r'[\u4e00-\u9fff]', text))
        elif lang_code == "en":
            letters = sum(1 for c in text if c.isalpha())
            # 提高阈值，以避免纯标点符号或数字被误判为英文
            return letters > len(text.strip()) * 0.5 
        return False

    def translate(self, text: str, from_mode: str, to_mode: str) -> str:
        # 使用 .strip() 检查是否为空，但保留原始 text 的换行
        if not text.strip(): 
            return text 
        if not self.is_ready:
            return "模型尚未加载完毕，请稍候..."
        
        try:
            # 1. 确定源语言和目标语言
            if from_mode == "auto":
                src = self.detect_language(text)
            else:
                src = from_mode

            if to_mode == "auto":
                tgt = "en" if src == "zh" else "zh"
            else:
                tgt = to_mode

            if src == tgt:
                return text 

            # 2. 调用通用混合文本翻译器
            return self._mixed_language_split_and_translate(text, src, tgt)

        except Exception as e:
            # 尝试回退到直接翻译
            try:
                return self._do_translate(text, src, tgt)
            except Exception as e_fallback:
                return f"翻译出错: {str(e)} 或 {str(e_fallback)}"

    def _mixed_language_split_and_translate(self, text: str, src: str, tgt: str) -> str:
        """
        通用混合文本翻译逻辑：
        根据源语言 (src) 进行切分，只翻译源语言块，保留非源语言块。
        """
        segments = self.split_pattern.split(text)
        
        translated_segments = []
        for seg in segments:
            # 保留所有片段，包括空格和换行符，但跳过纯空字符串
            if seg == "":
                continue
            
            # 判断当前段落是否为源语言 (src)
            is_source_language = self.is_chunk_language(seg, src)
            
            if is_source_language:
                # 认为是源语言，进行翻译
                try:
                    trans = self._do_translate(seg, src, tgt)
                    translated_segments.append(trans)
                except Exception:
                    # 如果翻译失败，保留原文（保护）
                    translated_segments.append(seg)
            else:
                # 非源语言（即混合的另一语言，或只是空格/换行），直接保留原文
                translated_segments.append(seg)
        
        result = "".join(translated_segments)
        
        # 关键修改：只移除文本末尾可能存在的换行符（通常来自 CTkTextbox 的 get("0.0", "end")）
        # 不再对内部的空格和换行符进行统一替换
        return result.rstrip()

    def _do_translate(self, text, src, tgt):
        """执行原本的 Argos 翻译"""
        with suppress_stderr():
            translator = argostranslate.translate.get_translation_from_codes(src, tgt)
            result = translator.translate(text)
        return result

# =================GUI 界面部分=================

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.lang_map = {
            "自动检测": "auto",
            "自动匹配": "auto",
            "中文": "zh",
            "English": "en"
        }

        self.title("离线翻译助手（雨季）")
        self.geometry("700x600")
        self.backend = TranslatorBackend()

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1) 
        self.grid_rowconfigure(4, weight=1) 

        # --- Row 0 ---
        self.top_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.top_frame.grid(row=0, column=0, padx=20, pady=(20, 5), sticky="ew")
        self.logo_label = ctk.CTkLabel(self.top_frame, text="雨季离线翻译软件", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.pack(side="left")
        self.status_label = ctk.CTkLabel(self.top_frame, text="初始化中...", text_color="gray")
        self.status_label.pack(side="right")

        # --- Row 1 ---
        self.lang_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.lang_frame.grid(row=1, column=0, padx=20, pady=(0, 5), sticky="ew")

        self.lbl_from = ctk.CTkLabel(self.lang_frame, text="源语言:")
        self.lbl_from.pack(side="left", padx=(0, 5))
        self.combo_source = ctk.CTkComboBox(self.lang_frame, values=["自动检测", "中文", "English"], width=110)
        self.combo_source.pack(side="left")
        self.combo_source.set("自动检测")

        self.lbl_arrow = ctk.CTkLabel(self.lang_frame, text=" ➜ ", font=ctk.CTkFont(size=16))
        self.lbl_arrow.pack(side="left", padx=10)

        self.lbl_to = ctk.CTkLabel(self.lang_frame, text="目标语言:")
        self.lbl_to.pack(side="left", padx=(0, 5))
        self.combo_target = ctk.CTkComboBox(self.lang_frame, values=["自动匹配", "English", "中文"], width=110)
        self.combo_target.pack(side="left")
        self.combo_target.set("自动匹配")

        # --- Row 2 ---
        self.input_textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(size=14))
        self.input_textbox.grid(row=2, column=0, padx=20, pady=(5, 5), sticky="nsew")
        self.input_textbox.insert("0.0", "请输入或粘贴文字...")
        self.input_textbox.bind("<FocusIn>", self.clear_placeholder)

        # --- Row 3 ---
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=3, column=0, padx=20, pady=5, sticky="ew")

        self.btn_paste = ctk.CTkButton(self.btn_frame, text="粘贴并翻译", command=self.paste_and_translate, 
                                     fg_color="transparent", border_width=1, text_color=("gray10", "#DCE4EE"))
        self.btn_paste.pack(side="left", padx=5)
        self.btn_clear = ctk.CTkButton(self.btn_frame, text="清空", command=self.clear_text, width=60, fg_color="gray", hover_color="darkgray")
        self.btn_clear.pack(side="left", padx=5)
        self.btn_translate = ctk.CTkButton(self.btn_frame, text="开始翻译", command=self.run_translation, font=ctk.CTkFont(weight="bold"))
        self.btn_translate.pack(side="right", padx=5)
        self.btn_translate.configure(state="disabled")

        # --- Row 4 ---
        self.output_textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(size=14))
        self.output_textbox.grid(row=4, column=0, padx=20, pady=(5, 0), sticky="nsew")
        self.output_textbox.configure(state="disabled")

        # --- Row 5 ---
        self.bottom_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.bottom_frame.grid(row=5, column=0, padx=20, pady=10, sticky="ew")
        self.btn_copy = ctk.CTkButton(self.bottom_frame, text="复制结果", command=self.copy_result, fg_color="#2CC985", hover_color="#229965", width=100)
        self.btn_copy.pack(side="right")

        self.placeholder_active = True
        self.start_loading_thread()

    def start_loading_thread(self):
        thread = threading.Thread(target=self.backend.check_and_install_models, args=(self.update_status,))
        thread.daemon = True
        thread.start()

    def update_status(self, msg):
        self.status_label.configure(text=msg)
        if "完成" in msg or "就绪" in msg:
            self.btn_translate.configure(state="normal")
            self.status_label.configure(text_color="#2CC985")

    def clear_placeholder(self, event):
        if self.placeholder_active:
            self.input_textbox.delete("0.0", "end")
            self.placeholder_active = False

    def clear_text(self):
        self.input_textbox.delete("0.0", "end")
        self.output_textbox.configure(state="normal")
        self.output_textbox.delete("0.0", "end")
        self.output_textbox.configure(state="disabled")
        self.status_label.configure(text="已清空", text_color="gray")

    def paste_and_translate(self):
        content = pyperclip.paste()
        if content:
            if self.placeholder_active:
                self.clear_placeholder(None)
            self.input_textbox.delete("0.0", "end")
            self.input_textbox.insert("0.0", content)
            self.run_translation()

    def run_translation(self):
        # 关键修改：获取文本时不再使用 .strip()
        text = self.input_textbox.get("0.0", "end")
        
        # 使用 strip() 检查是否真的为空，以忽略纯空格和换行符的输入
        if not text.strip(): 
            self.status_label.configure(text="请输入内容！", text_color="orange")
            return

        source_selection = self.combo_source.get()
        target_selection = self.combo_target.get()
        from_code = self.lang_map.get(source_selection, "auto")
        to_code = self.lang_map.get(target_selection, "auto")

        self.status_label.configure(text="正在翻译...", text_color="white")
        
        result = self.backend.translate(text, from_code, to_code)
        
        self.output_textbox.configure(state="normal")
        self.output_textbox.delete("0.0", "end")
        self.output_textbox.insert("0.0", result)
        self.output_textbox.configure(state="disabled")
        
        self.status_label.configure(text="翻译完成", text_color="#2CC985")

    def copy_result(self):
        result = self.output_textbox.get("0.0", "end").rstrip() # 使用 rstrip 移除末尾换行
        if result:
            pyperclip.copy(result)
            self.status_label.configure(text="已复制", text_color="#2CC985")

if __name__ == "__main__":
    app = App()
    app.mainloop()