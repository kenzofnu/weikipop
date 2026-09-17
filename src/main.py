# src/main.py
import signal
import sys
import threading

from PyQt6.QtCore import qInstallMessageHandler
from PyQt6.QtWidgets import QApplication

from src.config.config import config, APP_NAME, APP_VERSION
from src.dictionary.lookup import Lookup
from src.gui.input import InputLoop
from src.gui.popup import Popup
from src.gui.tray import TrayIcon
from src.ocr.hit_scan import HitScanner
from src.ocr.ocr import OcrProcessor
from src.screenshot.screenmanager import ScreenManager
from src.utils.latest_queue import LatestValueQueue
from src.utils.logger import setup_logging


def qt_message_handler(mode, context, message):
    # Check if the message is the specific warning we want to suppress.
    if "QWindowsWindow::setGeometry" in message and "Unable to set geometry" in message:
        return  # Silently ignore this specific warning.
    if original_handler:
        original_handler(mode, context, message)

# This global variable will hold the original message handler.
original_handler = None

class SharedState:
    def __init__(self):
        self.running = True

        # events and queues
        self.screenshot_trigger_event = threading.Event()
        self.ocr_queue = LatestValueQueue()
        self.hit_scan_queue = LatestValueQueue()
        self.lookup_queue = LatestValueQueue()

        # screen lock - used by screen manager and popup
        self.screen_lock = threading.RLock()
        
def main():
    setup_logging()
    shared_state = SharedState()

    global original_handler
    original_handler = qInstallMessageHandler(qt_message_handler)

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    input_loop = InputLoop(shared_state)
    popup_window = Popup(shared_state, input_loop)

    # Start rolling system-audio capture for sentence-audio mining (Windows).
    if getattr(config, "enable_sentence_audio", False):
        try:
            from src.utils.audio_capture import recorder
            buf = max(15.0, float(getattr(config, "sentence_audio_duration", 6.0))
                      + float(getattr(config, "sentence_audio_offset", 0.3)) + 10.0)
            recorder.buffer_seconds = buf
            recorder._max_frames = int(recorder.samplerate * buf)
            recorder.start()
        except Exception as e:
            print(f"Could not start system audio capture: {e}")

    screen_manager = ScreenManager(shared_state, input_loop)  # trigger region selection
    lookup = Lookup(shared_state, popup_window)  # load dictionary

    ocr_processor = OcrProcessor(shared_state, screen_manager)
    hit_scanner = HitScanner(shared_state, input_loop, screen_manager)
    tray_icon = TrayIcon(screen_manager, ocr_processor, popup_window, input_loop, lookup)

    for t in [lookup, hit_scanner, ocr_processor, screen_manager, input_loop]:
        t.start()

    ready_message = f"""
    --------------------------------------------------
    {APP_NAME}.{APP_VERSION} is running in the background.

      - To use: Press and hold '{config.hotkey}' over Japanese text. 
      - To configure or change scan area: Right-click the icon in your system tray.
      - Make sure to checkout the auto scan mode!
      - To exit: Press Ctrl+C in this terminal.

    --------------------------------------------------
    """
    print(ready_message)

    def signal_handler(sig, frame):
        QApplication.quit()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    exit_code = app.exec()

    shared_state.running = False
    try:
        from src.utils.audio_capture import recorder
        recorder.stop()
    except Exception:
        pass
    shared_state.screenshot_trigger_event.set()
    shared_state.ocr_queue.put(None)
    shared_state.hit_scan_queue.trigger()
    shared_state.lookup_queue.put(None)
    sys.exit(exit_code)


if __name__ == '__main__':
    main()