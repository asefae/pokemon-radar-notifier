"""Russian Tkinter user interface for the radar notifier."""

from __future__ import annotations

import time
import tkinter as tk
import threading
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from config_manager import AppConfig, MatchTarget, default_config_path, load_config, save_config
from detection_tracker import DetectionTracker
from matching import find_matches
from notifications import notify
from ocr_engine import OCRResult, OCRScan, SimpleOCRWorker
from screen_capture import (
    ScreenCapture,
    detect_tesseract,
    screen_diagnostics,
    show_region_preview,
    validate_tesseract,
)
from screen_capture import select_regions
from telegram_notifier import TelegramNotifier


class RadarApp(tk.Tk):
    def __init__(self, config_path: Path | str | None = None) -> None:
        super().__init__()
        self.title("Pokémon Radar — уведомления")
        self.geometry("920x720")
        self.minsize(760, 600)
        self.config_path = Path(config_path) if config_path else default_config_path()
        self.settings: AppConfig = load_config(self.config_path)
        self.capture = ScreenCapture()
        self.worker: SimpleOCRWorker | None = None
        self.tracker = DetectionTracker(lost_confirmation_scans=2, cooldown_seconds=self.settings.cooldown_seconds)
        self.last_scan: OCRScan | None = None
        self._diagnostics_running = False
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._build()
        self._refresh_lists()
        self.after(250, self._poll_results)

    def _build(self) -> None:
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        self.start_button = ttk.Button(toolbar, text="▶ Запустить", command=self.start)
        self.start_button.pack(side="left", padx=3)
        self.stop_button = ttk.Button(toolbar, text="■ Остановить", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=3)
        ttk.Button(toolbar, text="Экспортировать лог", command=self.export_log).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Тестовое уведомление Telegram", command=self.test_telegram).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Показать Telegram-диагностику", command=self.telegram_diagnostics).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Открыть папку настроек", command=self.open_settings_folder).pack(side="left", padx=3)
        ttk.Button(toolbar, text="Тест OCR сейчас", command=self.test_ocr_now).pack(side="left", padx=3)

        targets_frame = ttk.LabelFrame(self, text="Имена Pokémon для поиска", padding=8)
        targets_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.targets_var = tk.StringVar()
        ttk.Entry(targets_frame, textvariable=self.targets_var).pack(side="left", fill="x", expand=True)
        ttk.Label(targets_frame, text="через запятую или с новой строки").pack(side="left", padx=6)

        regions_frame = ttk.LabelFrame(self, text="Области экрана", padding=8)
        regions_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.region_list = tk.Listbox(regions_frame, height=7)
        self.region_list.pack(side="left", fill="both", expand=True)
        region_buttons = ttk.Frame(regions_frame)
        region_buttons.pack(side="left", fill="y", padx=(8, 0))
        ttk.Button(region_buttons, text="Выбрать область заново", command=self.add_regions).pack(fill="x", pady=2)
        ttk.Button(region_buttons, text="Проверить область", command=self.test_region).pack(fill="x", pady=2)
        ttk.Button(region_buttons, text="Удалить выбранную", command=self.remove_region).pack(fill="x", pady=2)

        options = ttk.LabelFrame(self, text="Параметры", padding=8)
        options.pack(fill="x", padx=8, pady=(0, 8))
        self.threshold_var = tk.IntVar(value=self.settings.threshold)
        self.ocr_threshold_var = tk.IntVar(value=self.settings.ocr_threshold)
        self.capture_interval_var = tk.IntVar(value=self.settings.capture_interval_ms)
        self.ocr_interval_var = tk.IntVar(value=self.settings.ocr_interval_ms)
        self.preprocessing_var = tk.StringVar(value=self.settings.preprocessing_mode)
        self.debug_var = tk.BooleanVar(value=self.settings.debug_frames)
        self.pipeline_diagnostics_var = tk.BooleanVar(value=self.settings.pipeline_diagnostics)
        self.cooldown_var = tk.IntVar(value=self.settings.cooldown_seconds)
        self.lost_scans_var = tk.IntVar(value=self.settings.lost_confirmation_scans)
        ttk.Label(options, text="Порог совпадения:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(options, from_=50, to=100, textvariable=self.threshold_var, width=6).grid(row=0, column=1)
        ttk.Label(options, text="OCR threshold:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(options, from_=0, to=255, textvariable=self.ocr_threshold_var, width=6).grid(row=0, column=3)
        ttk.Label(options, text="Захват (мс):").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(options, from_=100, to=1000, increment=10, textvariable=self.capture_interval_var, width=7).grid(row=0, column=5)
        ttk.Label(options, text="OCR (мс):").grid(row=0, column=6, sticky="w")
        ttk.Spinbox(options, from_=200, to=2000, increment=10, textvariable=self.ocr_interval_var, width=7).grid(row=0, column=7)
        ttk.Label(options, text="Cooldown:").grid(row=1, column=0, sticky="w")
        ttk.Spinbox(options, from_=0, to=86400, textvariable=self.cooldown_var, width=7).grid(row=1, column=1)
        ttk.Label(options, text="Подтверждений потери:").grid(row=1, column=2, sticky="w")
        ttk.Spinbox(options, from_=1, to=5, textvariable=self.lost_scans_var, width=6).grid(row=1, column=3)
        ttk.Label(options, text="Обработка:").grid(row=1, column=4, sticky="w")
        ttk.Combobox(options, textvariable=self.preprocessing_var,
                     values=("fast", "balanced", "accurate"), state="readonly", width=9).grid(row=1, column=5)
        self.telegram_var = tk.BooleanVar(value=self.settings.telegram_enabled)
        self.sound_var = tk.BooleanVar(value=self.settings.sound_enabled)
        self.windows_var = tk.BooleanVar(value=self.settings.windows_notifications)
        ttk.Checkbutton(options, text="Telegram", variable=self.telegram_var).grid(row=2, column=0, padx=4, sticky="w")
        ttk.Checkbutton(options, text="Звук", variable=self.sound_var).grid(row=2, column=1, sticky="w")
        ttk.Checkbutton(options, text="Windows toast", variable=self.windows_var).grid(row=2, column=2, sticky="w")
        ttk.Checkbutton(options, text="Сохранять debug-варианты", variable=self.debug_var).grid(row=2, column=3, columnspan=3, sticky="w")
        ttk.Checkbutton(options, text="Периодические diagnostics", variable=self.pipeline_diagnostics_var).grid(
            row=2, column=6, columnspan=2, sticky="w"
        )
        ttk.Button(options, text="Сохранить", command=self.save).grid(row=2, column=7, padx=5, sticky="e")
        self.tesseract_var = tk.StringVar(value=self.settings.tesseract_cmd)
        self.token_var = tk.StringVar(value=self.settings.telegram_token)
        self.chat_id_var = tk.StringVar(value=self.settings.telegram_chat_id)
        ttk.Label(options, text="Tesseract:").grid(row=3, column=0, sticky="w", pady=(5, 0))
        ttk.Entry(options, textvariable=self.tesseract_var, width=42).grid(
            row=3, column=1, columnspan=4, sticky="ew", pady=(5, 0)
        )
        ttk.Label(options, text="Bot token:").grid(row=4, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.token_var, show="*", width=42).grid(
            row=4, column=1, columnspan=4, sticky="ew"
        )
        ttk.Label(options, text="Chat ID:").grid(row=4, column=5, sticky="e")
        ttk.Entry(options, textvariable=self.chat_id_var, width=18).grid(row=4, column=6, columnspan=2, sticky="ew")

        status_frame = ttk.LabelFrame(self, text="Последнее распознавание", padding=6)
        status_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.last_text = tk.StringVar(value="—")
        self.last_lines = tk.StringVar(value="Строки: —")
        self.last_scores = tk.StringVar(value="Оценки: —")
        self.timing_var = tk.StringVar(value="Тайминги: —")
        self.queue_var = tk.StringVar(value="Очередь: —")
        self.metrics_var = tk.StringVar(value="Метрики: processed=0; text=0; errors=0")
        self.error_var = tk.StringVar(value="Последняя ошибка: —")
        ttk.Label(status_frame, textvariable=self.last_text, anchor="w").pack(fill="x")
        ttk.Label(status_frame, textvariable=self.last_lines, anchor="w").pack(fill="x")
        ttk.Label(status_frame, textvariable=self.last_scores, anchor="w").pack(fill="x")
        ttk.Label(status_frame, textvariable=self.timing_var, anchor="w").pack(fill="x")
        ttk.Label(status_frame, textvariable=self.queue_var, anchor="w").pack(fill="x")
        ttk.Label(status_frame, textvariable=self.metrics_var, anchor="w").pack(fill="x")
        ttk.Label(status_frame, textvariable=self.error_var, anchor="w", justify="left").pack(fill="x")

        log_frame = ttk.LabelFrame(self, text="Журнал", padding=6)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.log = tk.Text(log_frame, height=8, state="disabled", wrap="word")
        self.log.pack(fill="both", expand=True)
        detection_frame = ttk.LabelFrame(self, text="Обнаружения", padding=6)
        detection_frame.pack(fill="x", padx=8, pady=(0, 8))
        self.active_var = tk.StringVar(value="Активные: —")
        self.lost_var = tk.StringVar(value="Потеряны: —")
        self.notification_var = tk.StringVar(value="Последние уведомления: —")
        ttk.Label(detection_frame, textvariable=self.active_var, anchor="w").pack(fill="x")
        ttk.Label(detection_frame, textvariable=self.lost_var, anchor="w").pack(fill="x")
        ttk.Label(detection_frame, textvariable=self.notification_var, anchor="w").pack(fill="x")
        self.status = tk.StringVar(value="Готово. Добавьте хотя бы одну область.")
        ttk.Label(self, textvariable=self.status, anchor="w", padding=(8, 0, 8, 6)).pack(fill="x")
        self._last_diagnostics_at = 0.0

    def _refresh_lists(self) -> None:
        self.region_list.delete(0, tk.END)
        for region in self.settings.regions:
            self.region_list.insert(tk.END, f"{region.name}: {region.left},{region.top}  {region.width}×{region.height}")
        self.targets_var.set(", ".join(target.name for target in self.settings.targets))

    def _write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def save(self) -> bool:
        names = [x.strip() for line in self.targets_var.get().splitlines() for x in line.split(",") if x.strip()]
        old_aliases = {target.name: target.aliases for target in self.settings.targets}
        self.settings.targets = [MatchTarget(name, old_aliases.get(name, [])) for name in names]
        try:
            threshold = int(self.threshold_var.get())
            ocr_threshold = int(self.ocr_threshold_var.get())
            cooldown = int(self.cooldown_var.get())
            lost_scans = int(self.lost_scans_var.get())
            capture_interval = int(self.capture_interval_var.get())
            ocr_interval = int(self.ocr_interval_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("Параметры", "Пороги, интервалы, cooldown и подтверждения потери должны быть числами.", parent=self)
            return False
        self.settings.threshold = max(0, min(100, threshold))
        self.settings.ocr_threshold = max(0, min(255, ocr_threshold))
        self.settings.cooldown_seconds = max(0, cooldown)
        self.settings.lost_confirmation_scans = max(1, min(5, lost_scans))
        self.settings.capture_interval_ms = max(100, min(1000, capture_interval))
        self.settings.ocr_interval_ms = max(200, min(2000, ocr_interval))
        self.settings.preprocessing_mode = self.preprocessing_var.get()
        self.settings.debug_frames = self.debug_var.get()
        self.settings.pipeline_diagnostics = self.pipeline_diagnostics_var.get()
        self.settings.telegram_enabled = self.telegram_var.get()
        self.settings.sound_enabled = self.sound_var.get()
        self.settings.windows_notifications = self.windows_var.get()
        self.tracker.cooldown_seconds = self.settings.cooldown_seconds
        self.tracker.lost_confirmation_scans = self.settings.lost_confirmation_scans
        self.settings.tesseract_cmd = self.tesseract_var.get().strip() or detect_tesseract() or ""
        ok, message = validate_tesseract(self.settings.tesseract_cmd)
        if not ok:
            self._write_log(f"Tesseract validation failed: {message}")
        else:
            self._write_log(f"Tesseract ready: {message}")
        # An empty masked field must not erase a previously saved secret while
        # the user changes unrelated settings.
        token = self.token_var.get().strip()
        chat_id = self.chat_id_var.get().strip()
        if token:
            self.settings.telegram_token = token
        if chat_id:
            self.settings.telegram_chat_id = chat_id
        save_config(self.settings, self.config_path)
        self.status.set("Настройки сохранены")
        self._write_log(f"Конфигурация сохранена: {self.config_path}")
        return True

    def _telegram_client(self) -> TelegramNotifier | None:
        token = self.settings.telegram_token.strip()
        chat_id = str(self.settings.telegram_chat_id).strip()
        if not token:
            messagebox.showerror("Telegram", "Заполните Bot Token.", parent=self)
            return None
        if not chat_id:
            messagebox.showerror("Telegram", "Заполните Chat ID.", parent=self)
            return None
        return TelegramNotifier(token, chat_id, timeout=10.0)

    def _log_telegram_result(self, prefix: str, result) -> None:
        self._write_log(
            f"{prefix}: HTTP={result.status_code}; ok={result.ok}; "
            f"description={result.description}"
        )

    def test_telegram(self) -> None:
        if not self.save():
            return
        notifier = self._telegram_client()
        if notifier is None:
            return
        self._write_log(
            f"Telegram send start; token length={notifier.token_length}; "
            f"chat_id={notifier.chat_id}"
        )

        def run() -> None:
            result = notifier.send_message("Тестовое уведомление Pokemon Radar работает ✅")
            self.after(0, lambda: self._finish_telegram_test(result))

        threading.Thread(target=run, name="telegram-test", daemon=True).start()

    def _finish_telegram_test(self, result) -> None:
        self._log_telegram_result("Telegram test", result)
        if result.success:
            messagebox.showinfo("Telegram", "Тестовое сообщение отправлено.", parent=self)
        else:
            messagebox.showerror("Telegram", f"Не удалось отправить сообщение:\n{result.description}", parent=self)

    def telegram_diagnostics(self) -> None:
        if not self.save():
            return
        notifier = self._telegram_client()
        if notifier is None:
            return
        self._write_log(
            f"Telegram diagnostics start; token length={notifier.token_length}; "
            f"chat_id={notifier.chat_id}"
        )

        def run() -> None:
            auth, message = notifier.diagnose()
            self.after(0, lambda: self._finish_telegram_diagnostics(auth, message))

        threading.Thread(target=run, name="telegram-diagnostics", daemon=True).start()

    def _finish_telegram_diagnostics(self, auth, message) -> None:
        self._log_telegram_result("Telegram getMe", auth)
        if message is not None:
            self._log_telegram_result("Telegram sendMessage", message)
        if auth.success and message and message.success:
            text = "getMe и sendMessage успешны."
            messagebox.showinfo("Telegram", text, parent=self)
        else:
            failed = message if message is not None and not message.success else auth
            messagebox.showerror("Telegram", f"Диагностика не пройдена:\n{failed.description}", parent=self)

    def add_regions(self) -> None:
        count = simpledialog.askinteger("Области", "Сколько областей выбрать?", parent=self, minvalue=1, maxvalue=20)
        if not count:
            return
        names: list[str] = []
        for index in range(count):
            name = simpledialog.askstring("Название области", f"Название области {index + 1}:", parent=self,
                                          initialvalue=f"Область {index + 1}")
            if name:
                names.append(name.strip())
        if not names:
            return
        try:
            selected = select_regions(self, names, self.settings.regions)
        except RuntimeError as exc:
            messagebox.showerror("Выбор области", str(exc), parent=self)
            return
        self.settings.regions.extend(selected)
        save_config(self.settings, self.config_path)
        self._refresh_lists()
        self.status.set(f"Добавлено областей: {len(selected)}")

    def remove_region(self) -> None:
        selected = self.region_list.curselection()
        if selected:
            del self.settings.regions[selected[0]]
            save_config(self.settings, self.config_path)
            self._refresh_lists()

    def start(self) -> None:
        if not self.settings.regions:
            messagebox.showwarning("Нет областей", "Сначала добавьте области экрана.", parent=self)
            return
        if not self.save():
            return
        self.worker = SimpleOCRWorker(
            self.capture, list(self.settings.regions), "eng",
            self.settings.tesseract_cmd, interval_ms=1000,
            debug_path=self.config_path.parent / "debug_raw.png",
        )
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Простой OCR запущен")
        self._write_log(f"Простой OCR: interval=1000 мс; координаты: {screen_diagnostics()}")

    def stop(self) -> None:
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.start_button.configure(state="normal")
        self.stop_button.configure(state="disabled")
        self.status.set("Сканирование остановлено")

    def _poll_results(self) -> None:
        if self.worker:
            for result in self.worker.drain():
                self._handle_simple_result(result)
        self.after(250, self._poll_results)

    def _handle_simple_result(self, result: OCRResult) -> None:
        coords = result.region.as_mss_dict()
        if result.error:
            self.status.set("Ошибка OCR")
            self.error_var.set(f"Последняя ошибка OCR:\n{result.error[-1200:]}")
            self._write_log(f"OCR error; coords={coords}\n{result.error}")
            return
        self.status.set("OCR processed")
        self.last_text.set(result.text or "OCR text is empty")
        self.last_lines.set("Строки: " + (" | ".join(result.lines) if result.lines else "—"))
        self.metrics_var.set(
            f"OCR processed; text length={len(result.text)}; "
            f"size={coords['width']}x{coords['height']}"
        )
        self._write_log(
            f"coords={coords}; screenshot={coords['width']}x{coords['height']}; "
            f"pytesseract called; OCR text length={len(result.text)}; "
            f"full OCR text: {result.text!r}"
        )
        target_names = [target.name for target in self.settings.targets] or ["Tentacool"]
        found = [name for name in target_names if name.casefold() in result.text.casefold()]
        self.last_scores.set("Найдено точно: " + (", ".join(found) if found else "нет"))
        if found:
            for name in found:
                self._write_log(f"Найдено имя: {name}; fuzzy matching disabled")
                if self.settings.windows_notifications:
                    notify("Pokémon Radar", f"Найден Pokémon: {name}", self.settings.sound_enabled)
                if self.settings.telegram_enabled:
                    notifier = TelegramNotifier(
                        self.settings.telegram_token, self.settings.telegram_chat_id, timeout=10.0
                    )
                    if notifier.enabled:
                        threading.Thread(
                            target=self._send_detection_telegram,
                            args=(notifier, f"Найден Pokémon: {name}"),
                            name="telegram-detection",
                            daemon=True,
                        ).start()
        else:
            self._write_log("Найденное имя: нет; fuzzy matching disabled")

    def _send_detection_telegram(self, notifier: TelegramNotifier, text: str) -> None:
        result = notifier.send_message(text)
        self.after(0, lambda: self._log_telegram_result("Telegram detection", result))

    def _handle_scan(self, scan: OCRScan) -> None:
        self.last_scan = scan
        self._handle_cycle(scan.results, scan)

    def _handle_cycle(self, results: list[OCRResult], scan: OCRScan | None = None) -> None:
        valid_results = [result for result in results if not result.error]
        for result in results:
            if result.error:
                self.status.set("Ошибка")
                self._write_log(f"{result.region.name}: ошибка OCR: {result.error}")
                self.error_var.set(f"Последняя ошибка OCR: {result.error[-1200:]}")
        if not valid_results:
            self.last_text.set("OCR processed, но текст не получен")
            self.last_lines.set("Строки: —")
            return
        self.status.set("Сканирование запущено")
        self.last_text.set(" | ".join(result.text or "—" for result in valid_results))
        lines = [line for result in valid_results for line in result.lines]
        self.last_lines.set("Строки: " + (" | ".join(lines) if lines else "—"))
        targets = [(target.name, target.aliases) for target in self.settings.targets]
        all_matches = []
        score_details: list[str] = []
        for result in valid_results:
            scored_matches = find_matches(result.text, targets, 0)
            result_matches = [match for match in scored_matches if match.score >= self.settings.threshold]
            all_matches.extend(
                replace(match, area=result.region.name)
                for match in result_matches
            )
            score_details.extend(f"{match.target} {match.score:.0f}%" for match in scored_matches)
        self.last_scores.set("Оценки: " + (", ".join(score_details) if score_details else "нет совпадений"))
        if scan:
            self.timing_var.set(
                f"Тайминги: захват {scan.capture_started:.3f}; OCR start {scan.ocr_started:.3f}; "
                f"OCR finish {scan.ocr_finished:.3f}"
            )
        events = self.tracker.update(all_matches)
        for event in events:
            if event.kind == "notify":
                message = f"Найдено: {event.name} ({event.score:.0f}%)"
                self._write_log(f"{message}; область: {event.area}; причина: новое появление")
                if self.settings.windows_notifications:
                    notify("Pokémon Radar", message, self.settings.sound_enabled)
                if self.settings.telegram_enabled:
                    TelegramNotifier(self.settings.telegram_token, self.settings.telegram_chat_id).send(message)
                if scan:
                    scan.notification_sent = time.perf_counter()
                    self.timing_var.set(self.timing_var.get() + f"; notification sent {scan.notification_sent:.3f}")
            elif event.kind == "lost":
                self._write_log(f"Потерян: {event.name} после {self.tracker.lost_confirmation_scans} сканирований")
            else:
                reason = "already active" if event.reason == "active" else event.reason
                self._write_log(f"{event.name}: уведомление не отправлено; причина: {reason}")
        if not all_matches:
            reason = "не распознано" if not any(result.text.strip() for result in valid_results) else "score too low"
            self._write_log(f"Уведомление не отправлено; причина: {reason}")
        self._refresh_detection_status()

    def _refresh_detection_status(self) -> None:
        active = sorted(state.name for state in self.tracker.active_detections.values())
        lost = [name for name, _timestamp in self.tracker.lost_detections[-10:]]
        notifications = [
            f"{state.name} ({time.strftime('%H:%M:%S', time.localtime(state.last_notification))})"
            for state in self.tracker.active_detections.values()
            if state.last_notification is not None
        ]
        self.active_var.set(f"Активные: {', '.join(active) if active else '—'}")
        self.lost_var.set(f"Потеряны: {', '.join(lost) if lost else '—'}")
        self.notification_var.set(
            f"Последние уведомления: {', '.join(notifications) if notifications else '—'}"
        )

    def _handle_result(self, result: OCRResult) -> None:
        """Compatibility wrapper for callers that provide a single result."""
        if result.error:
            self.status.set("Ошибка")
            self._write_log(f"{result.region.name}: ошибка OCR: {result.error}")
            return
        self._handle_cycle([result])

    def test_region(self) -> None:
        selected = self.region_list.curselection()
        if not selected:
            messagebox.showinfo("Область", "Выберите область в списке.", parent=self)
            return
        try:
            region = self.settings.regions[selected[0]]
            image = self.capture.grab(region)
            if image.getbbox() is None:
                raise RuntimeError("Получен пустой screenshot. Проверьте координаты области.")
            show_region_preview(self, image)
            self._write_log(f"Проверка области: {region.as_mss_dict()}; размер={image.size}")
        except Exception as exc:
            messagebox.showerror("Область", f"Не удалось снять скриншот: {exc}", parent=self)

    def test_ocr_now(self) -> None:
        selected = self.region_list.curselection()
        if not selected:
            messagebox.showinfo("Тест OCR", "Сначала выберите область.", parent=self)
            return
        region = self.settings.regions[selected[0]]
        worker = SimpleOCRWorker(
            self.capture, [region], "eng", self.settings.tesseract_cmd,
            interval_ms=1000, debug_path=self.config_path.parent / "debug_raw.png",
        )
        self._write_log(f"Тест OCR: screenshot области {region.as_mss_dict()}")

        def run() -> None:
            result = worker._ocr(region)
            self.after(0, lambda: self._show_test_ocr_result(result))

        threading.Thread(target=run, name="ocr-test", daemon=True).start()

    def _show_test_ocr_result(self, result: OCRResult) -> None:
        if result.error:
            self.error_var.set(f"Последняя ошибка OCR:\n{result.error[-1200:]}")
            self._write_log(f"Тест OCR ошибка:\n{result.error}")
        else:
            self.last_text.set(result.text or "OCR text is empty")
            self.last_lines.set("Строки: " + (" | ".join(result.lines) if result.lines else "—"))
            self.metrics_var.set(f"OCR processed; text length={len(result.text)}; raw=debug_raw.png")
            self._write_log(
                f"Тест OCR: pytesseract called; OCR text length={len(result.text)}; "
                f"full OCR text: {result.text!r}; debug_raw.png сохранён"
            )
        self._show_debug_results([result], "Тест OCR сейчас")

    def test_telegram(self) -> None:
        if not self.save():
            return
        if not self.settings.telegram_token or not self.settings.telegram_chat_id:
            messagebox.showwarning("Telegram", "Заполните token и chat_id в config.json.", parent=self)
            return
        ok = TelegramNotifier(self.settings.telegram_token, self.settings.telegram_chat_id).send(
            "Тестовое уведомление Pokémon Radar"
        )
        messagebox.showinfo("Telegram", "Сообщение отправлено." if ok else "Не удалось отправить сообщение.", parent=self)

    def export_log(self) -> None:
        path = filedialog.asksaveasfilename(parent=self, title="Экспорт лога",
                                            defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            Path(path).write_text(self.log.get("1.0", tk.END), encoding="utf-8")
            self._write_log(f"Лог экспортирован: {path}")

    def open_settings_folder(self) -> None:
        save_config(self.settings, self.config_path)
        try:
            import os
            os.startfile(self.config_path.parent)  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            messagebox.showinfo("Настройки", f"Папка настроек:\n{self.config_path.parent}", parent=self)

    def open_debug_folder(self) -> None:
        directory = Path(self.settings.debug_directory)
        if not directory.is_absolute():
            directory = self.config_path.parent / directory
        directory.mkdir(parents=True, exist_ok=True)
        try:
            import os
            os.startfile(directory)  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            messagebox.showinfo("Debug", f"Папка обработанных кадров:\n{directory}", parent=self)

    def save_latest_frame(self) -> None:
        if not self.worker:
            messagebox.showinfo("Debug OCR", "Сначала запустите мониторинг.", parent=self)
            return
        directory = Path(self.settings.debug_directory)
        if not directory.is_absolute():
            directory = self.config_path.parent / directory
        try:
            saved = self.worker.save_latest_frame(directory)
        except Exception as exc:
            messagebox.showerror("Debug OCR", f"Не удалось сохранить кадр: {exc}", parent=self)
            return
        if saved:
            self._write_log(f"Сохранены последние кадры: {len(saved)}")
        else:
            messagebox.showinfo("Debug OCR", "Кадр ещё не захвачен.", parent=self)

    def save_debug_variants(self) -> None:
        if not self.worker:
            messagebox.showinfo("Debug OCR", "Сначала запустите мониторинг.", parent=self)
            return
        directory = Path(self.settings.debug_directory)
        if not directory.is_absolute():
            directory = self.config_path.parent / directory
        try:
            saved = self.worker.save_latest_variants(directory)
        except Exception as exc:
            messagebox.showerror("Debug OCR", f"Не удалось сохранить варианты: {exc}", parent=self)
            return
        self._write_log(f"Сохранены raw/upscaled/threshold варианты: {len(saved)} файлов")

    def ocr_latest_frame(self) -> None:
        if not self.worker:
            messagebox.showinfo("Debug OCR", "Сначала запустите мониторинг.", parent=self)
            return
        try:
            results = self.worker.recognize_latest_frames()
        except Exception as exc:
            messagebox.showerror("Debug OCR", f"Не удалось распознать кадр: {exc}", parent=self)
            return
        self._show_debug_results(results, "OCR последнего кадра")

    def ocr_saved_frame(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Выберите сохранённый кадр",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*.*")]
        )
        if not path:
            return
        worker = self.worker or OCRWorker(
            None, [], self.settings.language, self.settings.tesseract_cmd,
            threshold=self.settings.ocr_threshold,
            preprocessing_mode=self.settings.preprocessing_mode,
        )
        try:
            result = worker.recognize_saved_frame(path)
        except Exception as exc:
            messagebox.showerror("Debug OCR", f"Не удалось распознать сохранённый кадр: {exc}", parent=self)
            return
        self._show_debug_results([result], "OCR сохранённого кадра")

    def _show_debug_results(self, results: list[OCRResult], title: str) -> None:
        lines = []
        for result in results:
            # Show raw OCR text and traceback, rather than hiding failures in a
            # short status label.
            lines.append(f"[{result.region.name}] text length={len(result.text)}")
            lines.append(result.text or "—")
            if result.error:
                lines.append(f"ERROR:\n{result.error}")
                self.error_var.set(f"Последняя ошибка OCR: {result.error[-1200:]}")
        text = "\n".join(lines) or "Кадр ещё не захвачен."
        self.last_text.set(text[:2000])
        self.last_lines.set(f"Raw OCR: {text[:1800]}")
        self._write_log(f"{title}: {text[:1000]}")
        window = tk.Toplevel(self)
        window.title(title)
        window.geometry("760x500")
        output = tk.Text(window, wrap="word")
        output.pack(fill="both", expand=True, padx=8, pady=8)
        output.insert("1.0", text)
        output.configure(state="disabled")

    def _run_periodic_diagnostics(self) -> None:
        if not self.worker:
            return
        if self._diagnostics_running:
            return
        self._diagnostics_running = True
        directory = Path(self.settings.debug_directory)
        if not directory.is_absolute():
            directory = self.config_path.parent / directory
        worker = self.worker

        def run() -> None:
            try:
                results = worker.run_diagnostics(directory)
                diagnostics = worker.diagnostics()
                self.after(0, lambda: self._finish_diagnostics(results, diagnostics, "Периодическая диагностика pipeline"))
            except Exception as exc:
                self.after(0, lambda: self._finish_diagnostics_error(exc))

        threading.Thread(target=run, name="ocr-diagnostics", daemon=True).start()

    def _finish_diagnostics(self, results, diagnostics, title: str) -> None:
        self._diagnostics_running = False
        self._show_debug_results(results, title)
        self._write_log(f"pipeline diagnostics: {diagnostics}")

    def _finish_diagnostics_error(self, error: Exception) -> None:
        self._diagnostics_running = False
        self._write_log(f"pipeline diagnostics exception:\n{error}")

    def run_pipeline_diagnostics(self) -> None:
        if not self.worker:
            messagebox.showinfo("Диагностика", "Сначала запустите мониторинг.", parent=self)
            return
        self._run_periodic_diagnostics()

    def _close(self) -> None:
        self.stop()
        self.capture.close()
        self.destroy()
