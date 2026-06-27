import ctypes
import ctypes.wintypes
import io
import json
import os
import queue
import secrets
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import urllib.error
import urllib.request
import zlib
from PIL import Image, ImageDraw, ImageFont, ImageTk


APP_VERSION = "1.0.2"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/GianCarlozxc/Palabas/main/update.json"
DEFAULT_DOWNLOAD_URL = "https://github.com/GianCarlozxc/Palabas/raw/main/dist/Watch.exe"
UPDATE_CHECK_INTERVAL_MS = 5 * 60 * 1000
FRAME_HEADER = struct.Struct("!III")
DEFAULT_PORT = 5050
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watch_settings.json")
STREAM_FPS = 8
VIEWER_RENDER_FPS = 12
CAPTURE_INTERVAL_MS = int(1000 / STREAM_FPS)
VIEWER_RENDER_INTERVAL_MS = int(1000 / VIEWER_RENDER_FPS)
PREVIEW_INTERVAL_MS = 500
MAX_PREVIEW_WIDTH = 1280
MAX_PREVIEW_HEIGHT = 720
WAITING_WIDTH = 1280
WAITING_HEIGHT = 720
MAX_RENDER_WIDTH = 1600
MAX_RENDER_HEIGHT = 900
MAX_FULLSCREEN_RENDER_WIDTH = 1280
MAX_FULLSCREEN_RENDER_HEIGHT = 720
JPEG_QUALITY = 58
QUALITY_PROFILES = {
    "Low": {"quality": 38, "width": 854, "height": 480},
    "Balanced": {"quality": 58, "width": 1152, "height": 648},
    "High": {"quality": 72, "width": 1280, "height": 720},
}
MAX_FRAME_WIDTH = 4096
MAX_FRAME_HEIGHT = 2160
MAX_PAYLOAD_SIZE = 8 * 1024 * 1024
SOCKET_BUFFER_SIZE = 262144
SEND_TIMEOUT_SECONDS = 0.03
RECONNECT_DELAY_MS = 3000
WAITING_FRAME = bytes([8, 8, 8]) * WAITING_WIDTH * WAITING_HEIGHT
BG = "#050810"
PANEL = "#0B1020"
PANEL_2 = "#111827"
SURFACE = "#1B2333"
INPUT = "#0A0F1C"
ACCENT = "#22C55E"
ACCENT_HOVER = "#16A34A"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
BORDER = "#263148"
WARNING = "#F59E0B"

# Dark Mode Colors
DARK_BG = "#050810"
DARK_PANEL = "#0B1020"
DARK_PANEL_2 = "#111827"
DARK_SURFACE = "#1B2333"
DARK_INPUT = "#0A0F1C"
DARK_TEXT = "#F9FAFB"
DARK_MUTED = "#94A3B8"
DARK_BORDER = "#2A3142"

# Light Mode Colors
LIGHT_BG = "#F6F7FB"
LIGHT_PANEL = "#FFFFFF"
LIGHT_PANEL_2 = "#EEF2F7"
LIGHT_SURFACE = "#D8DEE8"
LIGHT_INPUT = "#FFFFFF"
LIGHT_TEXT = "#0F172A"
LIGHT_MUTED = "#64748B"
LIGHT_BORDER = "#CBD5E1"


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32),
        ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32),
        ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16),
        ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32),
        ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32),
        ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", ctypes.c_uint32 * 3),
    ]


class ScreenCapture:
    SRCCOPY = 0x00CC0020
    DIB_RGB_COLORS = 0
    BI_RGB = 0
    PW_RENDERFULLCONTENT = 0x00000002

    def __init__(self):
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.user32.SetProcessDPIAware()

    def screen_size(self):
        return self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)

    def visible_windows(self):
        windows = []
        current_pid = ctypes.windll.kernel32.GetCurrentProcessId()

        def callback(hwnd, _):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            length = self.user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            self.user32.GetWindowTextW(hwnd, title, length + 1)
            text = title.value.strip()
            if not text:
                return True
            pid = ctypes.c_ulong()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == current_pid:
                return True
            rect = ctypes.wintypes.RECT()
            if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return True
            width = rect.right - rect.left
            height = rect.bottom - rect.top
            if width < 120 or height < 90:
                return True
            windows.append((hwnd, text))
            return True

        enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(callback)
        self.user32.EnumWindows(enum_proc, 0)
        return windows

    def capture(self, max_width=MAX_PREVIEW_WIDTH, max_height=MAX_PREVIEW_HEIGHT):
        src_width, src_height = self.screen_size()
        return self._capture_from_dc(
            self.user32.GetDC(0),
            src_width,
            src_height,
            max_width,
            max_height,
            src_x=0,
            src_y=0,
            release=lambda dc: self.user32.ReleaseDC(0, dc),
        )

    def capture_window(self, hwnd, max_width=MAX_PREVIEW_WIDTH, max_height=MAX_PREVIEW_HEIGHT):
        return self.capture_window_from_screen(hwnd, max_width, max_height)

    def capture_window_from_screen(self, hwnd, max_width=MAX_PREVIEW_WIDTH, max_height=MAX_PREVIEW_HEIGHT):
        rect = ctypes.wintypes.RECT()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            raise RuntimeError("Selected window is not available")
        src_width = max(1, rect.right - rect.left)
        src_height = max(1, rect.bottom - rect.top)

        return self._capture_from_dc(
            self.user32.GetDC(0),
            src_width,
            src_height,
            max_width,
            max_height,
            src_x=rect.left,
            src_y=rect.top,
            release=lambda dc: self.user32.ReleaseDC(0, dc),
        )

    def bring_to_front(self, hwnd):
        if hwnd:
            self.user32.ShowWindow(hwnd, 9)
            self.user32.SetForegroundWindow(hwnd)

    def _capture_from_dc(self, source_dc, src_width, src_height, max_width, max_height, src_x, src_y, release):
        scale = min(max_width / src_width, max_height / src_height, 1.0)
        width = max(1, int(src_width * scale))
        height = max(1, int(src_height * scale))

        src_dc = self.gdi32.CreateCompatibleDC(source_dc)
        dst_dc = self.gdi32.CreateCompatibleDC(source_dc)
        src_bmp = self.gdi32.CreateCompatibleBitmap(source_dc, src_width, src_height)
        self.gdi32.SelectObject(src_dc, src_bmp)

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = width
        bmi.bmiHeader.biHeight = -height
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = self.BI_RGB

        bits = ctypes.c_void_p()
        dst_bmp = self.gdi32.CreateDIBSection(
            source_dc,
            ctypes.byref(bmi),
            self.DIB_RGB_COLORS,
            ctypes.byref(bits),
            None,
            0,
        )
        self.gdi32.SelectObject(dst_dc, dst_bmp)

        try:
            self.gdi32.BitBlt(src_dc, 0, 0, src_width, src_height, source_dc, src_x, src_y, self.SRCCOPY)
            self.gdi32.SetStretchBltMode(dst_dc, 4)
            self.gdi32.StretchBlt(dst_dc, 0, 0, width, height, src_dc, 0, 0, src_width, src_height, self.SRCCOPY)

            raw = ctypes.string_at(bits, width * height * 4)
            rgb = bytearray(width * height * 3)
            rgb[0::3] = raw[2::4]
            rgb[1::3] = raw[1::4]
            rgb[2::3] = raw[0::4]
            return width, height, bytes(rgb)
        finally:
            self.gdi32.DeleteObject(dst_bmp)
            self.gdi32.DeleteObject(src_bmp)
            self.gdi32.DeleteDC(dst_dc)
            self.gdi32.DeleteDC(src_dc)
            release(source_dc)


def make_ppm(width, height, rgb):
    return b"P6\n%d %d\n255\n" % (width, height) + rgb


def scale_rgb_frame(width, height, rgb, target_width, target_height):
    if width <= 0 or height <= 0:
        return width, height, rgb
    scale = min(target_width / width, target_height / height)
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    if new_width == width and new_height == height:
        return width, height, rgb

    try:
        from PIL import Image
        img = Image.frombytes("RGB", (width, height), rgb)
        try:
            resample_mode = Image.Resampling.BILINEAR
        except AttributeError:
            resample_mode = Image.BILINEAR
        img_resized = img.resize((new_width, new_height), resample_mode)
        return new_width, new_height, img_resized.tobytes()
    except Exception:
        pass

    scaled = bytearray(new_width * new_height * 3)
    for y in range(new_height):
        src_y = min(height - 1, int(y / scale))
        src_row = src_y * width * 3
        out_row = y * new_width * 3
        for x in range(new_width):
            src_x = min(width - 1, int(x / scale))
            src = src_row + src_x * 3
            dst = out_row + x * 3
            scaled[dst:dst + 3] = rgb[src:src + 3]
    return new_width, new_height, bytes(scaled)


class ProtocolError(ConnectionError):
    pass


def validate_frame_header(width, height, payload_size):
    if width <= 0 or height <= 0:
        raise ProtocolError("Stream sent an invalid frame size")
    if width > MAX_FRAME_WIDTH or height > MAX_FRAME_HEIGHT:
        raise ProtocolError("Stream frame is too large")
    if payload_size <= 0 or payload_size > MAX_PAYLOAD_SIZE:
        raise ProtocolError("Stream payload is too large")


def make_packet(width, height, rgb, jpeg_quality=JPEG_QUALITY):
    image = Image.frombytes("RGB", (width, height), rgb)
    payload_buffer = io.BytesIO()
    image.save(payload_buffer, format="JPEG", quality=jpeg_quality, optimize=False, progressive=False, subsampling=2)
    payload = payload_buffer.getvalue()
    return FRAME_HEADER.pack(width, height, len(payload)) + payload


def decode_packet_payload(width, height, payload):
    try:
        image = Image.open(io.BytesIO(payload))
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image.size[0], image.size[1], image.tobytes()
    except Exception:
        try:
            return width, height, zlib.decompress(payload)
        except zlib.error as exc:
            raise ValueError(f"Could not decode frame payload: {exc}") from exc


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


def recv_frame(sock):
    header = recv_exact(sock, FRAME_HEADER.size)
    width, height, payload_size = FRAME_HEADER.unpack(header)
    validate_frame_header(width, height, payload_size)
    return width, height, recv_exact(sock, payload_size)


def recv_line(sock, limit=4096):
    data = bytearray()
    while len(data) < limit:
        chunk = sock.recv(1)
        if not chunk:
            raise ConnectionError("Connection closed")
        if chunk == b"\n":
            return bytes(data)
        data.extend(chunk)
    raise ConnectionError("Handshake too large")


def send_line(sock, payload):
    sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")


class ShareServer:
    def __init__(self, port, room_code, packet_source, status_callback, failure_callback=None, viewers_callback=None):
        self.port = port
        self.room_code = room_code
        self.packet_source = packet_source
        self.status_callback = status_callback
        self.failure_callback = failure_callback
        self.viewers_callback = viewers_callback
        self.stop_event = threading.Event()
        self.clients = []
        self.client_versions = {}
        self.client_names = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _status(self, value):
        try:
            self.status_callback(value)
        except RuntimeError:
            pass

    def _failure(self, value):
        if not self.failure_callback:
            return
        try:
            self.failure_callback(value)
        except RuntimeError:
            pass

    def _viewers_changed(self):
        if not self.viewers_callback:
            return
        try:
            with self.lock:
                viewers = list(self.client_names.values())
            self.viewers_callback(viewers)
        except RuntimeError:
            pass

    def stop(self):
        self.stop_event.set()
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
            self.client_versions.clear()
            self.client_names.clear()
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
                client.close()
            except OSError:
                pass
        self._viewers_changed()

    def _run(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("", self.port))
            listener.listen()
            listener.settimeout(0.25)
        except (OSError, OverflowError) as exc:
            self.stop_event.set()
            try:
                listener.close()
            except OSError:
                pass
            self._status(f"Could not share on port {self.port}: {exc}")
            self._failure(str(exc))
            return
        self._status(f"Sharing on port {self.port}")

        accept_thread = threading.Thread(target=self._accept_clients, args=(listener,), daemon=True)
        accept_thread.start()

        try:
            while not self.stop_event.is_set():
                packet_info = self.packet_source()
                if packet_info is None:
                    time.sleep(0.05)
                    continue
                packet_version, packet = packet_info

                with self.lock:
                    clients = list(self.clients)

                dead_clients = []
                for client in clients:
                    if self.client_versions.get(client) == packet_version:
                        continue
                    try:
                        client.sendall(packet)
                        self.client_versions[client] = packet_version
                    except (socket.timeout, OSError):
                        dead_clients.append(client)

                if dead_clients:
                    with self.lock:
                        for client in dead_clients:
                            if client in self.clients:
                                self.clients.remove(client)
                            self.client_versions.pop(client, None)
                            self.client_names.pop(client, None)
                            try:
                                client.close()
                            except OSError:
                                pass
                    self._viewers_changed()

                time.sleep(max(0.01, 1 / STREAM_FPS))
        finally:
            self.stop_event.set()
            try:
                listener.close()
            except OSError:
                pass

    def _accept_clients(self, listener):
        while not self.stop_event.is_set():
            client = None
            try:
                client, addr = listener.accept()
                client.settimeout(5)
                hello = json.loads(recv_line(client).decode("utf-8"))
                if hello.get("room", "").upper() != self.room_code:
                    send_line(client, {"ok": False, "reason": "Wrong room code"})
                    client.close()
                    self._status(f"Rejected wrong room code from {addr[0]}")
                    continue
                viewer_name = hello.get("name", "Viewer").strip() or "Viewer"
                send_line(client, {"ok": True})
                client.settimeout(SEND_TIMEOUT_SECONDS)
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
                with self.lock:
                    self.clients.append(client)
                    self.client_versions[client] = None
                    self.client_names[client] = viewer_name
                    count = len(self.clients)
                self._status(f"{viewer_name} connected from {addr[0]} ({count})")
                self._viewers_changed()
            except socket.timeout:
                if client:
                    client.close()
                continue
            except (ValueError, ConnectionError):
                if client:
                    client.close()
                continue
            except OSError:
                if client:
                    client.close()
                if self.stop_event.is_set():
                    break
                continue


class ViewerClient:
    def __init__(self, host, port, room_code, viewer_name, frame_queue, status_callback, stopped_callback=None):
        self.host = host
        self.port = port
        self.room_code = room_code
        self.viewer_name = viewer_name
        self.frame_queue = frame_queue
        self.status_callback = status_callback
        self.stopped_callback = stopped_callback
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.sock = None

    def start(self):
        self.thread.start()

    def _status(self, value):
        try:
            self.status_callback(value)
        except RuntimeError:
            pass

    def _stopped(self, reason):
        if not self.stopped_callback:
            return
        try:
            self.stopped_callback(reason)
        except RuntimeError:
            pass

    def stop(self):
        self.stop_event.set()
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
                self.sock.close()
            except OSError:
                pass

    def _run(self):
        try:
            self._status(f"Connecting to {self.host}:{self.port}")
            self.sock = socket.create_connection((self.host, self.port), timeout=6)
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, SOCKET_BUFFER_SIZE)
            hello = json.dumps({"room": self.room_code, "name": self.viewer_name}).encode("utf-8") + b"\n"
            self.sock.sendall(hello)
            response = json.loads(recv_line(self.sock).decode("utf-8"))
            if not response.get("ok"):
                raise ProtocolError(response.get("reason", "Host rejected the connection"))
            self.sock.settimeout(None)
            self._status("Watching shared screen")
            while not self.stop_event.is_set():
                width, height, payload = recv_frame(self.sock)
                self._push_frame(width, height, payload, True)
        except Exception as exc:
            if not self.stop_event.is_set():
                reason = str(exc)
                self._status(f"Viewer stopped: {reason}")
                self._stopped(reason)

    def _push_frame(self, width, height, frame_data, encoded=False):
        try:
            while True:
                self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        self.frame_queue.put((width, height, frame_data, encoded))


class ScreenShareApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Watch")
        self._center_window(1180, 820)
        self.minsize(760, 560)
        self.overrideredirect(True)
        self._drag_start = None

        self.capture = ScreenCapture()
        self.latest_frame = None
        self.latest_packet = None
        self.latest_packet_version = 0
        self.packet_lock = threading.Lock()
        self.capture_stop_event = None
        self.capture_thread = None
        self.capture_error = None
        self.current_rgb_frame = None
        self.last_preview_time = 0
        self.last_viewer_render_time = 0
        self.waiting_packet = make_packet(WAITING_WIDTH, WAITING_HEIGHT, WAITING_FRAME)
        self.photo = None
        self.placeholder_photo = None
        self.placeholder_size = None
        self.display_message = None
        self.fullscreen_photo = None
        self.server = None
        self.client = None
        self.page = "name"
        self.viewer_frames = queue.Queue(maxsize=1)
        self.mode = tk.StringVar(value="share")
        self.host = tk.StringVar(value="")
        self.port = tk.IntVar(value=DEFAULT_PORT)
        self.user_name = tk.StringVar()
        self.room_code = tk.StringVar()
        self.join_code = tk.StringVar()
        self.host_share_screen = tk.BooleanVar(value=False)
        self.share_enabled = False
        self.share_paused = tk.BooleanVar(value=False)
        self.share_source = tk.StringVar(value="Entire screen")
        self.quality_profile = tk.StringVar(value="Balanced")
        self.selected_source_name = "Entire screen"
        self.window_sources = {}
        self.current_source_label = tk.StringVar(value="Source: Entire screen")
        self.viewer_count = tk.StringVar(value="No viewers connected")
        self.viewer_list = tk.StringVar(value="Waiting for viewers")
        self.status = tk.StringVar(value="Ready")
        self.frames = {}
        self.display = None
        self.fullscreen_active = False
        self.fullscreen_window = None
        self.fullscreen_display = None
        self.fullscreen_rendering = False
        self.fullscreen_hint_id = None
        self.fullscreen_cursor_job = None
        self.start_button = None
        self.stop_button = None
        self.reconnect_button = None
        self.pause_button = None
        self.maximized = False
        self.normal_geometry = None
        self.auto_reconnect_enabled = tk.BooleanVar(value=True)
        self.reconnect_job = None
        self.reconnect_attempts = 0
        self.session_body = None
        self.controls_border = None
        self.update_info = None
        self.update_checking = False
        self.update_button = None
        self.settings_button = None

        self._build_ui()
        self._load_settings()
        self.bind("<Escape>", lambda _event: self.exit_fullscreen())
        self.bind("<Map>", self._restore_borderless)
        self.bind("<Configure>", self._sync_responsive_layout)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._schedule_preview()
        self.after(1500, self.check_for_updates)

    def _build_ui(self):
        self.nav_labels = []
        self.custom_buttons = []
        
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL_2)
        style.configure("Rail.TFrame", background=PANEL, borderwidth=1, relief="flat")
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Card.TLabel", background=PANEL_2, foreground=TEXT)
        style.configure(
            "TRadiobutton",
            background=PANEL_2,
            foreground=TEXT,
            indicatorcolor=INPUT,
            padding=(0, 6),
        )
        style.map("TRadiobutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        style.configure(
            "TCheckbutton",
            background=PANEL_2,
            foreground=TEXT,
            indicatorcolor=INPUT,
            padding=(0, 6),
        )
        style.map("TCheckbutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        style.configure(
            "TEntry",
            fieldbackground=INPUT,
            background=INPUT,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            insertcolor=TEXT,
            padding=(12, 9),
        )
        style.configure(
            "Source.TCombobox",
            fieldbackground=INPUT,
            background=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            arrowcolor=TEXT,
            padding=(10, 8),
        )
        style.map(
            "Source.TCombobox",
            fieldbackground=[("readonly", INPUT)],
            foreground=[("readonly", TEXT)],
            background=[("readonly", SURFACE), ("active", BORDER)],
        )
        style.configure(
            "TScrollbar",
            gripcount=0,
            background=PANEL_2,
            troughcolor=INPUT,
            bordercolor=BORDER,
            arrowcolor=TEXT,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.map(
            "TScrollbar",
            background=[("active", ACCENT), ("pressed", ACCENT_HOVER)],
            arrowcolor=[("active", "white")],
        )

        self.shell_border = tk.Frame(self, bg="#142033", padx=1, pady=1)
        self.shell_border.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        self.root = tk.Frame(self.shell_border, bg=BG)
        self.root.pack(fill=tk.BOTH, expand=True)

        self.header = tk.Frame(self.root, bg="#070B15", height=86)
        self.header.pack(fill=tk.X, padx=14, pady=(14, 0))
        self.header.pack_propagate(False)
        self.header.columnconfigure(0, weight=0)
        self.header.columnconfigure(1, weight=0)
        self.header.columnconfigure(2, weight=0)
        self.header.columnconfigure(3, weight=0)
        self.header.columnconfigure(4, weight=1)
        self.header.columnconfigure(5, weight=0)
        self.header.columnconfigure(6, weight=0)
        self.header.columnconfigure(7, weight=0)
        self.header.columnconfigure(8, weight=0)
        self.header.columnconfigure(9, weight=0)
        self.header.columnconfigure(10, weight=0)
        self.header.bind("<ButtonPress-1>", self._start_window_drag)
        self.header.bind("<B1-Motion>", self._drag_window)

        self.brand_frame = tk.Frame(self.header, bg="#070B15")
        self.brand_frame.grid(row=0, column=0, sticky="w", padx=(26, 18), pady=18)
        self.brand_frame.bind("<ButtonPress-1>", self._start_window_drag)
        self.brand_frame.bind("<B1-Motion>", self._drag_window)

        self.logo_icon = tk.Canvas(
            self.brand_frame,
            width=42,
            height=36,
            bg="#070B15",
            highlightthickness=0,
            bd=0,
        )
        self.logo_icon.pack(side=tk.LEFT, padx=(0, 12))
        self.logo_icon.create_rectangle(3, 3, 39, 33, outline=ACCENT, width=2)
        self.logo_icon.create_polygon(17, 12, 17, 24, 27, 18, fill=TEXT, outline=TEXT)
        
        self.logo_label = tk.Label(
            self.brand_frame,
            text="WATCH",
            bg="#070B15",
            fg=TEXT,
            font=("Segoe UI", 20, "bold"),
            padx=0,
        )
        self.logo_label.pack(side=tk.LEFT)
        self.logo_label.bind("<ButtonPress-1>", self._start_window_drag)
        self.logo_label.bind("<B1-Motion>", self._drag_window)

        self.logo_mark = tk.Label(
            self.brand_frame,
            text="● LIVE",
            bg="#0D342A",
            fg="#31F287",
            font=("Segoe UI", 10, "bold"),
            padx=10,
            pady=7,
        )
        self.logo_mark.pack(side=tk.LEFT, padx=(14, 0))
        self.logo_mark.bind("<ButtonPress-1>", self._start_window_drag)
        self.logo_mark.bind("<B1-Motion>", self._drag_window)
        
        self.nav_frame = tk.Frame(self.header, bg=BG)
        self.nav_frame.grid(row=0, column=1, sticky="w", pady=18)
        for label in ("Profile", "Rooms", "Session"):
            lbl = tk.Label(
                self.nav_frame,
                text=label,
                bg="#070B15",
                fg="#C7D2E6",
                font=("Segoe UI", 12, "bold"),
                cursor="hand2",
            )
            lbl.pack(side=tk.LEFT, padx=(0, 34))
            def make_hover(l):
                return lambda e: l.config(fg=TEXT)
            def make_leave(l):
                return lambda e: l.config(fg="#C7D2E6")
            lbl.bind("<Enter>", make_hover(lbl))
            lbl.bind("<Leave>", make_leave(lbl))
            self.nav_labels.append(lbl)
            
        self.theme_mode = tk.StringVar(value="dark")
        self.theme_toggle_btn = tk.Canvas(
            self.header,
            width=58,
            height=48,
            bg=SURFACE,
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        self.theme_toggle_btn.grid(row=0, column=7, sticky="e", padx=(12, 0), pady=18)
        self.theme_toggle_btn.bind("<Button-1>", lambda _event: self.toggle_theme())
        self.theme_toggle_btn.bind("<Enter>", lambda _event: self._draw_theme_icon(hover=True))
        self.theme_toggle_btn.bind("<Leave>", lambda _event: self._draw_theme_icon(hover=False))
        self._draw_theme_icon()

        self.update_button = tk.Button(
            self.header,
            text="Update available",
            command=self.show_update_dialog,
            bg="#0D342A",
            activebackground="#14532D",
            fg="#31F287",
            activeforeground="#F8FAFC",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=10,
        )
        self.update_button.grid(row=0, column=4, sticky="e", padx=(12, 0), pady=18)
        self.update_button.grid_remove()

        self.settings_button = self._window_control_button("SET", self.show_settings_dialog)
        self.settings_button.configure(font=("Segoe UI", 9, "bold"), width=4)
        self.settings_button.grid(row=0, column=6, sticky="e", padx=(12, 0), pady=18)

        self.minimize_button = self._window_control_button("-", self._minimize_window)
        self.minimize_button.grid(row=0, column=8, sticky="e", padx=(12, 0), pady=18)

        self.maximize_button = self._window_control_button("□", self._toggle_maximize)
        self.maximize_button.grid(row=0, column=9, sticky="e", padx=(12, 0), pady=18)

        self.close_button = self._window_control_button("X", self.destroy, danger=True)
        self.close_button.grid(row=0, column=10, sticky="e", padx=(12, 28), pady=18)
        
        self.status_label = tk.Label(
            self.header,
            textvariable=self.status,
            bg="#0E1525",
            fg=TEXT,
            font=("Segoe UI", 11, "bold"),
            padx=18,
            pady=10,
        )
        self.status_label.grid(row=0, column=5, sticky="e", pady=18)

        self.bottom_bar = tk.Frame(self.root, bg="#070B15", height=70)
        self.bottom_bar.pack(side=tk.BOTTOM, fill=tk.X, padx=30, pady=(0, 18))
        self.bottom_bar.pack_propagate(False)
        tk.Label(
            self.bottom_bar,
            text="●  Connected to LAN",
            bg="#070B15",
            fg="#31F287",
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT, padx=(28, 36))
        tk.Label(
            self.bottom_bar,
            text="0 peers online",
            bg="#070B15",
            fg="#C7D2E6",
            font=("Segoe UI", 11),
        ).pack(side=tk.LEFT)

        self.content = tk.Frame(self.root, bg=BG)
        self.content.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=30, pady=0)

        self._build_name_page()
        self._build_room_page()
        self._build_session_page()
        self._show_page("name")

    def _window_control_button(self, text, command, danger=False):
        bg = "#7F1D1D" if danger else SURFACE
        hover = "#991B1B" if danger else BORDER
        btn = tk.Button(
            self.header,
            text=text,
            command=command,
            bg=bg,
            activebackground=hover,
            fg=TEXT,
            activeforeground=TEXT,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 11, "bold"),
            width=3,
            pady=6,
        )
        btn.bind("<Enter>", lambda _event: btn.configure(bg=hover))
        btn.bind("<Leave>", lambda _event: btn.configure(bg=bg))
        return btn

    def _center_window(self, width, height):
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")

    def _start_window_drag(self, event):
        self._drag_start = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_window(self, event):
        if not self._drag_start:
            return
        if self.maximized:
            self._toggle_maximize()
        x_offset, y_offset = self._drag_start
        self.geometry(f"+{event.x_root - x_offset}+{event.y_root - y_offset}")

    def _minimize_window(self):
        self.overrideredirect(False)
        self.iconify()

    def _toggle_maximize(self):
        if self.maximized:
            if self.normal_geometry:
                self.geometry(self.normal_geometry)
            self.maximized = False
            if hasattr(self, "maximize_button"):
                self.maximize_button.configure(text="□")
            return

        self.normal_geometry = self.geometry()
        work_width = self.winfo_screenwidth()
        work_height = self.winfo_screenheight()
        self.geometry(f"{work_width}x{work_height}+0+0")
        self.maximized = True
        if hasattr(self, "maximize_button"):
            self.maximize_button.configure(text="❐")

    def _restore_borderless(self, _event=None):
        if self.state() == "normal":
            self.after(10, lambda: self.overrideredirect(True))

    def _sync_responsive_layout(self, _event=None):
        width = max(1, self.winfo_width())
        if hasattr(self, "controls_border") and self.controls_border and self.controls_border.winfo_exists():
            sidebar_width = max(318, min(390, int(width * 0.18)))
            self.controls_border.configure(width=sidebar_width)
            if self.session_body and self.session_body.winfo_exists():
                self.session_body.columnconfigure(1, minsize=sidebar_width)
        if self.display_message and self.display:
            self.after_idle(lambda: self._set_display_message(self.display_message))

    def toggle_theme(self):
        if self.theme_mode.get() == "dark":
            self.theme_mode.set("light")
        else:
            self.theme_mode.set("dark")
        self._apply_theme()

    def _draw_theme_icon(self, hover=False):
        if not hasattr(self, "theme_toggle_btn") or not self.theme_toggle_btn.winfo_exists():
            return
        bg = BORDER if hover else SURFACE
        fg = "#FACC15" if self.theme_mode.get() == "dark" else "#334155"
        self.theme_toggle_btn.configure(bg=bg)
        self.theme_toggle_btn.delete("all")
        if self.theme_mode.get() == "dark":
            self.theme_toggle_btn.create_oval(24, 19, 34, 29, fill=fg, outline=fg)
            for x1, y1, x2, y2 in (
                (29, 8, 29, 14),
                (29, 34, 29, 40),
                (13, 24, 19, 24),
                (39, 24, 45, 24),
                (17, 12, 21, 16),
                (37, 32, 41, 36),
                (17, 36, 21, 32),
                (37, 16, 41, 12),
            ):
                self.theme_toggle_btn.create_line(x1, y1, x2, y2, fill=fg, width=2, capstyle=tk.ROUND)
        else:
            self.theme_toggle_btn.create_oval(20, 14, 38, 32, fill=fg, outline=fg)
            self.theme_toggle_btn.create_oval(28, 10, 45, 28, fill=bg, outline=bg)

    def _apply_theme(self):
        is_dark = (self.theme_mode.get() == "dark")
        bg = DARK_BG if is_dark else LIGHT_BG
        panel = DARK_PANEL if is_dark else LIGHT_PANEL
        panel_2 = DARK_PANEL_2 if is_dark else LIGHT_PANEL_2
        surface = DARK_SURFACE if is_dark else LIGHT_SURFACE
        input_bg = DARK_INPUT if is_dark else LIGHT_INPUT
        text = DARK_TEXT if is_dark else LIGHT_TEXT
        muted = DARK_MUTED if is_dark else LIGHT_MUTED
        border = DARK_BORDER if is_dark else LIGHT_BORDER
        
        global BG, PANEL, PANEL_2, SURFACE, INPUT, TEXT, MUTED, BORDER
        BG, PANEL, PANEL_2, SURFACE, INPUT, TEXT, MUTED, BORDER = bg, panel, panel_2, surface, input_bg, text, muted, border
        
        style = ttk.Style(self)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("Card.TFrame", background=PANEL_2)
        style.configure("TLabel", background=BG, foreground=TEXT)
        style.configure("Panel.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Card.TLabel", background=PANEL_2, foreground=TEXT)
        
        style.configure(
            "TRadiobutton",
            background=PANEL_2,
            foreground=TEXT,
            indicatorcolor=INPUT,
        )
        style.map("TRadiobutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        
        style.configure(
            "TCheckbutton",
            background=PANEL_2,
            foreground=TEXT,
            indicatorcolor=INPUT,
        )
        style.map("TCheckbutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        
        style.configure(
            "TEntry",
            fieldbackground=INPUT,
            background=INPUT,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            insertcolor=TEXT,
        )
        
        style.configure(
            "Source.TCombobox",
            fieldbackground=INPUT,
            background=SURFACE,
            foreground=TEXT,
            bordercolor=BORDER,
            lightcolor=BORDER,
            darkcolor=BORDER,
            arrowcolor=TEXT,
        )
        style.map(
            "Source.TCombobox",
            fieldbackground=[("readonly", INPUT)],
            foreground=[("readonly", TEXT)],
            background=[("readonly", SURFACE), ("active", BORDER)],
        )
        
        style.configure(
            "TScrollbar",
            background=PANEL_2,
            troughcolor=INPUT,
            bordercolor=BORDER,
            arrowcolor=TEXT,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.map(
            "TScrollbar",
            background=[("active", ACCENT), ("pressed", ACCENT_HOVER)],
            arrowcolor=[("active", "white")],
        )
        
        self.configure(bg=BG)
        
        if hasattr(self, "header") and self.header.winfo_exists():
            self.header.configure(bg=BG)
        if hasattr(self, "brand_frame") and self.brand_frame.winfo_exists():
            self.brand_frame.configure(bg=BG)
        if hasattr(self, "logo_label") and self.logo_label.winfo_exists():
            self.logo_label.configure(bg=BG, fg=TEXT)
        if hasattr(self, "logo_mark") and self.logo_mark.winfo_exists():
            self.logo_mark.configure(bg=ACCENT, fg="#04130A")
        if hasattr(self, "nav_frame") and self.nav_frame.winfo_exists():
            self.nav_frame.configure(bg=BG)
        if hasattr(self, "nav_labels"):
            for lbl in self.nav_labels:
                if lbl.winfo_exists():
                    lbl.configure(bg=BG, fg=MUTED)
        if hasattr(self, "status_label") and self.status_label.winfo_exists():
            self.status_label.configure(bg=PANEL_2, fg=TEXT)
        if hasattr(self, "theme_toggle_btn") and self.theme_toggle_btn.winfo_exists():
            self._draw_theme_icon()
        if hasattr(self, "minimize_button") and self.minimize_button.winfo_exists():
            self.minimize_button.configure(bg=SURFACE, activebackground=BORDER, fg=TEXT, activeforeground=TEXT)
        if hasattr(self, "settings_button") and self.settings_button and self.settings_button.winfo_exists():
            self.settings_button.configure(bg=SURFACE, activebackground=BORDER, fg=TEXT, activeforeground=TEXT)
        if hasattr(self, "close_button") and self.close_button.winfo_exists():
            self.close_button.configure(bg="#7F1D1D", activebackground="#991B1B", fg=TEXT, activeforeground=TEXT)
        if hasattr(self, "update_button") and self.update_button and self.update_button.winfo_exists():
            self.update_button.configure(bg="#0D342A", activebackground="#14532D", fg="#31F287", activeforeground="#F8FAFC")
        if hasattr(self, "source_combo") and self.source_combo.winfo_exists():
            self.source_combo.configure(style="Source.TCombobox")
            
        if hasattr(self, "custom_buttons"):
            for btn in self.custom_buttons:
                if btn.winfo_exists():
                    is_primary = (btn.cget("text") in ("CONTINUE", "CREATE ROOM", "JOIN ROOM", "START", "UPDATE NOW"))
                    btn.configure(
                        bg=ACCENT if is_primary else SURFACE,
                        activebackground=ACCENT_HOVER if is_primary else BORDER,
                        fg="white" if is_primary else TEXT,
                        activeforeground="white"
                    )

    def _clear_content(self):
        for frame in self.frames.values():
            frame.pack_forget()

    def _show_page(self, name):
        self.page = name
        self._clear_content()
        self.frames[name].pack(fill=tk.BOTH, expand=True)

    def _button(self, parent, text, command, primary=False):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=ACCENT if primary else SURFACE,
            activebackground=ACCENT_HOVER if primary else BORDER,
            fg="white" if primary else TEXT,
            activeforeground="white",
            disabledforeground="#475569",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=18,
            pady=11,
        )
        def on_enter(e):
            if btn['state'] != tk.DISABLED:
                btn.config(bg=ACCENT_HOVER if primary else BORDER)
        def on_leave(e):
            if btn['state'] != tk.DISABLED:
                btn.config(bg=ACCENT if primary else SURFACE)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        
        if not hasattr(self, "custom_buttons"):
            self.custom_buttons = []
        self.custom_buttons.append(btn)
        return btn

    def _small_button(self, parent, text, command):
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            bg=SURFACE,
            activebackground=BORDER,
            fg=TEXT,
            activeforeground=TEXT,
            disabledforeground="#475569",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=7,
        )
        def on_enter(e):
            if btn['state'] != tk.DISABLED:
                btn.config(bg=BORDER)
        def on_leave(e):
            if btn['state'] != tk.DISABLED:
                btn.config(bg=SURFACE)
        btn.bind("<Enter>", on_enter)
        btn.bind("<Leave>", on_leave)
        
        if not hasattr(self, "custom_buttons"):
            self.custom_buttons = []
        self.custom_buttons.append(btn)
        return btn

    def _build_name_page(self):
        page = tk.Frame(self.content, bg=BG)
        self.frames["name"] = page

        shell = tk.Frame(page, bg=BG)
        shell.pack(fill=tk.BOTH, expand=True, padx=18, pady=(34, 22))
        shell.columnconfigure(0, weight=1)
        shell.columnconfigure(1, weight=0, minsize=520)
        shell.rowconfigure(0, weight=1)

        intro = tk.Frame(shell, bg=BG)
        intro.grid(row=0, column=0, sticky="nsew", padx=(30, 34))
        intro.columnconfigure(0, weight=1)

        tk.Label(
            intro,
            text="WATCH PARTY",
            bg=BG,
            foreground=ACCENT,
            font=("Segoe UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")
        accent_line = tk.Canvas(intro, width=170, height=12, bg=BG, highlightthickness=0, bd=0)
        accent_line.grid(row=1, column=0, sticky="w", pady=(6, 34))
        accent_line.create_line(0, 5, 170, 5, fill="#0B2C24", width=2)
        accent_line.create_line(0, 5, 82, 5, fill=ACCENT, width=2)

        tk.Label(
            intro,
            text="Share a screen.\nKeep everyone\nin sync.",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 27, "bold"),
            justify=tk.LEFT,
            wraplength=520,
        ).grid(row=2, column=0, sticky="w")
        tk.Label(
            intro,
            text="A lightweight LAN viewer for rooms, watch parties, and quick screen handoff.",
            bg=BG,
            fg="#B9C5D8",
            wraplength=400,
            justify=tk.LEFT,
            font=("Segoe UI", 12),
        ).grid(row=3, column=0, sticky="nw", pady=(14, 0))

        art = tk.Canvas(intro, width=520, height=76, bg=BG, highlightthickness=0, bd=0)
        art.grid(row=4, column=0, sticky="sw", pady=(4, 0))
        art.create_oval(130, 30, 470, 142, outline="#12372E", width=2)
        art.create_oval(188, 48, 416, 120, outline="#154A39", width=1)
        art.create_rectangle(250, 4, 492, 74, outline="#295264", width=2)
        art.create_rectangle(258, 12, 484, 66, outline="#0E1A2B", fill="#08111E")
        art.create_polygon(360, 28, 360, 54, 384, 41, fill=ACCENT, outline=ACCENT)
        art.create_oval(330, 58, 362, 90, fill="#07111E", outline=ACCENT, width=2)
        art.create_oval(244, 62, 274, 92, fill="#07111E", outline=ACCENT, width=2)
        art.create_oval(416, 62, 446, 92, fill="#07111E", outline=ACCENT, width=2)
        art.create_oval(385, 56, 399, 70, fill=ACCENT, outline=ACCENT)
        for x, y in ((126, 48), (180, 96), (505, 28), (310, 8), (445, 76)):
            art.create_oval(x, y, x + 4, y + 4, fill=ACCENT, outline=ACCENT)

        feature_box = tk.Frame(intro, bg=BG)
        feature_box.grid(row=5, column=0, sticky="w", pady=(8, 0))
        self._feature_row(feature_box, "LAN Powered", "Works seamlessly on your local network.", "wifi")
        self._feature_row(feature_box, "Everyone in Sync", "Low latency, smooth viewing experience.", "people")
        self._feature_row(feature_box, "Private & Secure", "Your room. Your rules. Your privacy.", "shield")

        card_border = tk.Frame(shell, bg="#2B3952", padx=1, pady=1)
        card_border.grid(row=0, column=1, sticky="nsew", padx=(0, 34), pady=(18, 22))
        card = tk.Frame(card_border, bg="#101624", padx=44, pady=20)
        card.pack(fill=tk.BOTH, expand=True)

        icon = tk.Canvas(card, width=72, height=72, bg="#101624", highlightthickness=0, bd=0)
        icon.pack(pady=(0, 12))
        icon.create_oval(4, 4, 68, 68, outline="#133D32", width=2)
        icon.create_oval(18, 18, 54, 54, outline=ACCENT, width=2)
        icon.create_oval(30, 25, 38, 33, fill=TEXT, outline=TEXT)
        icon.create_oval(43, 26, 50, 33, fill=TEXT, outline=TEXT)
        icon.create_arc(23, 35, 45, 59, start=0, extent=180, outline=TEXT, width=3)
        icon.create_arc(39, 36, 59, 57, start=0, extent=180, outline=TEXT, width=3)

        tk.Label(
            card,
            text="Display name",
            bg="#101624",
            fg=TEXT,
            font=("Segoe UI", 22, "bold"),
        ).pack(anchor="center", pady=(0, 10))
        tk.Label(
            card,
            text="This is shown to the room host.",
            bg="#101624",
            fg="#AEBBD0",
            font=("Segoe UI", 13),
        ).pack(anchor="center", pady=(0, 18))

        entry_shell = tk.Frame(card, bg="#33445C", padx=1, pady=1)
        entry_shell.pack(fill=tk.X, pady=(0, 28))
        name_entry = tk.Entry(
            entry_shell,
            textvariable=self.user_name,
            bg="#0B1020",
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI", 15),
        )
        name_entry.pack(fill=tk.X, ipady=10, padx=1, pady=1)
        self._button(card, "CONTINUE  >", self.continue_to_rooms, primary=True).pack(fill=tk.X, ipady=4)

        tips_header = tk.Frame(card, bg="#101624")
        tips_header.pack(fill=tk.X, pady=(22, 0))
        tk.Frame(tips_header, bg="#2B3548", height=1).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=8)
        tk.Label(
            tips_header,
            text="TIPS",
            bg="#101624",
            fg="#4ADE80",
            font=("Segoe UI", 10, "bold"),
            padx=18,
        ).pack(side=tk.LEFT)
        tk.Frame(tips_header, bg="#2B3548", height=1).pack(side=tk.LEFT, fill=tk.X, expand=True, pady=8)
        tip = tk.Frame(card, bg="#111A2B", highlightbackground="#263148", highlightthickness=1, padx=18, pady=10)
        tip.pack(fill=tk.X, pady=(12, 0))
        tk.Label(
            tip,
            text="Use a name others will recognize\nfor a better experience.",
            bg="#111A2B",
            fg="#D7DEEA",
            justify=tk.LEFT,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

    def _feature_row(self, parent, title, body, kind):
        row = tk.Frame(parent, bg=BG)
        row.pack(fill=tk.X, pady=(0, 12))
        icon = tk.Canvas(row, width=48, height=48, bg=BG, highlightthickness=0, bd=0)
        icon.pack(side=tk.LEFT, padx=(0, 16))
        icon.create_rectangle(4, 4, 44, 44, fill="#0E2A24", outline="#112F2A")
        if kind == "wifi":
            icon.create_arc(13, 16, 35, 38, start=30, extent=120, outline=ACCENT, width=3)
            icon.create_arc(18, 22, 30, 34, start=30, extent=120, outline=ACCENT, width=3)
            icon.create_oval(23, 34, 28, 39, fill=ACCENT, outline=ACCENT)
        elif kind == "people":
            icon.create_oval(14, 14, 24, 24, fill=ACCENT, outline=ACCENT)
            icon.create_oval(28, 15, 38, 25, fill=ACCENT, outline=ACCENT)
            icon.create_arc(9, 24, 29, 44, start=0, extent=180, outline=ACCENT, width=3)
            icon.create_arc(23, 26, 43, 44, start=0, extent=180, outline=ACCENT, width=3)
        else:
            icon.create_polygon(24, 10, 37, 16, 34, 34, 24, 41, 14, 34, 11, 16, fill="", outline=ACCENT, width=3)
            icon.create_line(19, 25, 23, 30, 31, 20, fill=ACCENT, width=3)
        text = tk.Frame(row, bg=BG)
        text.pack(side=tk.LEFT)
        tk.Label(text, text=title, bg=BG, fg=TEXT, font=("Segoe UI", 11, "bold")).pack(anchor="w")
        tk.Label(text, text=body, bg=BG, fg="#AEBBD0", font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

    def _build_room_page(self):
        page = tk.Frame(self.content, bg=BG)
        self.frames["room"] = page

        shell = tk.Frame(page, bg=BG)
        shell.pack(fill=tk.BOTH, expand=True, padx=14, pady=(12, 14))
        shell.columnconfigure(0, weight=1)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(2, weight=1)

        title = tk.Frame(shell, bg=BG)
        title.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        title_text = tk.Frame(title, bg=BG)
        title_text.pack(anchor="w")
        tk.Label(
            title_text,
            text="Choose a ",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 24, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            title_text,
            text="mode",
            bg=BG,
            fg=ACCENT,
            font=("Segoe UI", 24, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            shell,
            text="Host a room or join an existing one to start your watch party.",
            bg=BG,
            fg="#B9C5D8",
            font=("Segoe UI", 11),
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 16))

        create_border = tk.Frame(shell, bg="#263148", padx=1, pady=1)
        create_border.grid(row=2, column=0, sticky="nsew", padx=(0, 12))
        create = tk.Frame(create_border, bg="#0B1220", padx=28, pady=18)
        create.pack(fill=tk.BOTH, expand=True)

        join_border = tk.Frame(shell, bg="#263148", padx=1, pady=1)
        join_border.grid(row=2, column=1, sticky="nsew", padx=(12, 0))
        join = tk.Frame(join_border, bg="#0B1220", padx=28, pady=18)
        join.pack(fill=tk.BOTH, expand=True)

        self._pill_label(create, "HOST").pack(anchor="w", pady=(0, 12))
        self._accent_heading(create, "Create ", "a room").pack(anchor="w")
        tk.Label(
            create,
            text="Start a LAN room, choose a source,\nand control when screen sharing begins.",
            bg="#0B1220",
            fg="#C7D2E6",
            justify=tk.LEFT,
            font=("Segoe UI", 11),
        ).pack(anchor="w", pady=(10, 0))

        art = tk.Canvas(create, width=430, height=184, bg="#0B1220", highlightthickness=0, bd=0)
        art.pack(fill=tk.X, expand=True, pady=(6, 8))
        art.create_oval(74, 18, 386, 234, outline="#0B3029", width=1)
        art.create_oval(112, 42, 348, 208, outline="#0D3B31", width=1)
        art.create_oval(154, 66, 306, 180, outline="#12483A", width=1)
        art.create_rectangle(164, 58, 332, 174, outline=ACCENT, width=3)
        art.create_rectangle(171, 66, 325, 166, fill="#07111E", outline="#102133")
        art.create_oval(222, 92, 274, 144, outline=ACCENT, width=2)
        art.create_line(248, 103, 248, 133, fill=ACCENT, width=4)
        art.create_line(234, 118, 262, 118, fill=ACCENT, width=4)
        art.create_rectangle(236, 174, 256, 196, fill="#0D2A26", outline="#17443A")
        art.create_polygon(184, 196, 306, 196, 274, 204, 154, 204, fill="#0D2A26", outline="#17443A")
        art.create_oval(48, 166, 90, 208, fill="#07111E", outline=ACCENT, width=1)
        art.create_oval(84, 158, 128, 202, fill="#07111E", outline=ACCENT, width=1)
        art.create_oval(124, 166, 166, 208, fill="#07111E", outline=ACCENT, width=1)
        for x, y in ((78, 116), (104, 28), (340, 28), (388, 120), (356, 78)):
            art.create_oval(x, y, x + 7, y + 7, fill="#0E3B31", outline="#0E3B31")

        self._button(create, "CREATE ROOM      ->", self.create_room, primary=True).pack(fill=tk.X, ipady=8, pady=(0, 0))

        self._pill_label(join, "VIEWER").pack(anchor="w", pady=(0, 12))
        self._accent_heading(join, "Join ", "a room").pack(anchor="w", pady=(0, 14))
        self._room_entry(join, "Host address", self.host, "▣").pack(fill=tk.X, pady=(0, 9))
        self._room_entry(join, "Port", self.port, "▤").pack(fill=tk.X, pady=(0, 9))
        self._room_entry(join, "Room code (optional)", self.join_code, "▢").pack(fill=tk.X, pady=(0, 12))
        self._button(join, "JOIN ROOM      ->", self.join_room, primary=True).pack(fill=tk.X, ipady=8)

    def _pill_label(self, parent, text):
        return tk.Label(
            parent,
            text=text,
            bg="#0D342A",
            fg="#31F287",
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=5,
        )

    def _accent_heading(self, parent, leading, accent):
        row = tk.Frame(parent, bg=parent.cget("bg"))
        tk.Label(row, text=leading, bg=parent.cget("bg"), fg=TEXT, font=("Segoe UI", 20, "bold")).pack(side=tk.LEFT)
        tk.Label(row, text=accent, bg=parent.cget("bg"), fg=ACCENT, font=("Segoe UI", 20, "bold")).pack(side=tk.LEFT)
        return row

    def _room_entry(self, parent, label, variable, icon):
        wrapper = tk.Frame(parent, bg=parent.cget("bg"))
        tk.Label(
            wrapper,
            text=label,
            bg=parent.cget("bg"),
            fg="#C7D2E6",
            font=("Segoe UI", 10),
        ).pack(anchor="w", pady=(0, 7))
        border = tk.Frame(wrapper, bg="#33445C", padx=1, pady=1)
        border.pack(fill=tk.X)
        box = tk.Frame(border, bg="#07111E", padx=14, pady=0)
        box.pack(fill=tk.X)
        tk.Label(box, text=icon, bg="#07111E", fg=ACCENT, font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Entry(
            box,
            textvariable=variable,
            bg="#07111E",
            fg=TEXT,
            insertbackground=TEXT,
            relief=tk.FLAT,
            bd=0,
            font=("Segoe UI", 12),
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=8)
        return wrapper

    def _display_resized(self, _event=None):
        if self.current_rgb_frame:
            self._rerender_current_frame()
        elif self.display_message:
            self._set_display_message(self.display_message)

    def _font(self, size, bold=False):
        names = ("segoeuib.ttf", "seguisb.ttf") if bold else ("segoeui.ttf",)
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _set_display_message(self, message):
        if not self.display:
            return
        width = max(420, self.display.winfo_width())
        height = max(320, self.display.winfo_height())
        if self.display_message == message and self.placeholder_size == (width, height) and self.placeholder_photo:
            self.display.configure(text="", image=self.placeholder_photo)
            return
        self.display_message = message
        self.placeholder_size = (width, height)
        image = Image.new("RGB", (width, height), "#050A12")
        draw = ImageDraw.Draw(image)

        cx = width // 2
        scale = max(0.85, min(1.65, min(width / 900, height / 620)))
        art_y = int(height / 2 - 185 * scale)
        art_y = max(int(50 * scale), art_y)

        def sx(value):
            return int(value * scale)

        def pt(x, y):
            return (cx + sx(x), art_y + sx(y))

        for i, color in enumerate(("#09261F", "#0B352B", "#0E4536")):
            pad = sx(i * 34)
            draw.ellipse(
                (cx - sx(170) + pad, art_y - sx(40) + pad, cx + sx(170) - pad, art_y + sx(220) - pad),
                outline=color,
                width=max(1, sx(1)),
            )
        for dx, dy in ((-150, 40), (-64, -34), (118, -12), (174, 72), (72, -56)):
            px, py = pt(dx, dy)
            r = sx(3)
            draw.ellipse((px - r, py - r, px + r, py + r), fill=ACCENT)

        screen = (cx - sx(74), art_y + sx(24), cx + sx(74), art_y + sx(110))
        draw.rounded_rectangle(screen, radius=sx(8), outline=ACCENT, width=max(2, sx(3)), fill="#07111E")
        draw.polygon([pt(-13, 50), pt(-13, 84), pt(19, 67)], outline=ACCENT, fill=None)
        for px in (-54, 0, 54):
            pcx = cx + sx(px)
            draw.ellipse((pcx - sx(18), art_y + sx(118), pcx + sx(18), art_y + sx(154)), outline=ACCENT, width=max(1, sx(1)))
            draw.arc((pcx - sx(38), art_y + sx(142), pcx + sx(38), art_y + sx(198)), 180, 360, fill=ACCENT, width=max(1, sx(1)))

        title_font = self._font(max(24, sx(30)), bold=True)
        sub_font = self._font(max(11, sx(13)))
        button_font = self._font(max(10, sx(12)), bold=True)
        if message == "Room is open. Screen sharing is off.":
            lines = [("Room is open.", TEXT), ("Screen sharing is off.", TEXT)]
            subtitle = "Share your screen to begin."
        else:
            lines = [(message, TEXT)]
            subtitle = ""
        text_y = art_y + sx(205)
        for line, fill in lines:
            bbox = draw.textbbox((0, 0), line, font=title_font)
            draw.text((cx - (bbox[2] - bbox[0]) // 2, text_y), line, fill=fill, font=title_font)
            text_y += sx(38)
        if subtitle:
            bbox = draw.textbbox((0, 0), subtitle, font=sub_font)
            draw.text((cx - (bbox[2] - bbox[0]) // 2, text_y + sx(4)), subtitle, fill="#AEBBD0", font=sub_font)
            text_y += sx(36)
        btn_text = "START SHARING" if message == "Room is open. Screen sharing is off." else "START"
        btn_w, btn_h = sx(168), sx(46)
        draw.rounded_rectangle((cx - btn_w // 2, text_y, cx + btn_w // 2, text_y + btn_h), radius=sx(8), outline=ACCENT, width=max(1, sx(1)))
        bbox = draw.textbbox((0, 0), btn_text, font=button_font)
        draw.text((cx - (bbox[2] - bbox[0]) // 2, text_y + sx(14)), btn_text, fill=ACCENT, font=button_font)

        self.placeholder_photo = ImageTk.PhotoImage(image)
        self.display.configure(text="", image=self.placeholder_photo)

    def _build_session_page(self):
        body = tk.Frame(self.content, bg=BG)
        self.frames["session"] = body
        self.session_body = body
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=318)
        body.rowconfigure(0, weight=1)

        stage_border = tk.Frame(body, bg="#263148", padx=1, pady=1)
        stage_border.grid(row=0, column=0, sticky="nsew", padx=(0, 12), pady=(8, 14))
        stage = tk.Frame(stage_border, bg="#050A12")
        stage.pack(fill=tk.BOTH, expand=True)
        stage.rowconfigure(0, weight=1)
        stage.columnconfigure(0, weight=1)

        self.display = tk.Label(
            stage,
            text="No active stream",
            bg="#050A12",
            fg="#F9FAFB",
            anchor=tk.CENTER,
            compound=tk.CENTER,
            font=("Segoe UI", 24, "bold"),
        )
        self.display.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.display.bind("<Double-Button-1>", lambda _event: self.enter_fullscreen())
        self.display.bind("<Configure>", self._display_resized)

        self.stage_secure_badge = tk.Label(
            stage,
            text="Secure LAN Connection\nYour connection is private and local.",
            bg="#101827",
            fg="#AEBBD0",
            justify=tk.LEFT,
            font=("Segoe UI", 8),
            padx=14,
            pady=9,
        )
        self.stage_secure_badge.place(relx=0.03, rely=0.92, anchor="sw")
        self.stage_viewer_badge = tk.Label(
            stage,
            text="0\nViewers in room",
            bg="#101827",
            fg="#AEBBD0",
            justify=tk.LEFT,
            font=("Segoe UI", 8),
            padx=14,
            pady=9,
        )
        self.stage_viewer_badge.place(relx=0.97, rely=0.92, anchor="se")

        controls_border = tk.Frame(body, bg="#263148", padx=1, pady=1)
        self.controls_border = controls_border
        controls_border.grid(row=0, column=1, sticky="nsew", pady=(8, 14))
        controls_border.configure(width=318)
        controls_border.grid_propagate(False)
        controls = tk.Frame(controls_border, bg="#0B1220", padx=13, pady=9)
        controls.pack(fill=tk.BOTH, expand=True)

        tk.Label(
            controls,
            text="SESSION",
            bg="#0B1220",
            fg=ACCENT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        self.session_title = tk.Label(
            controls,
            text="Room",
            bg="#0B1220",
            fg=TEXT,
            font=("Segoe UI", 15, "bold"),
        )
        self.session_title.pack(anchor="w", pady=(2, 6))

        code_card = tk.Frame(controls, bg="#111A2B", highlightbackground="#263148", highlightthickness=1, padx=11, pady=6)
        code_card.pack(fill=tk.X, pady=(0, 6))
        tk.Label(code_card, text="ROOM CODE", bg="#111A2B", fg="#AEBBD0", font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.room_code_label = tk.Label(
            code_card,
            text="------",
            bg="#111A2B",
            fg=ACCENT,
            font=("Consolas", 16, "bold"),
        )
        self.room_code_label.pack(anchor="w", pady=(1, 0))

        self.name_label = tk.Label(code_card, text="", bg="#111A2B", fg="#AEBBD0", font=("Segoe UI", 8))
        self.name_label.pack(anchor="w", pady=(2, 0))

        self.host_options = tk.Frame(controls, bg="#0B1220")
        self.host_options.pack(fill=tk.X, pady=(0, 6))
        tk.Label(
            self.host_options,
            text="SHARING",
            bg="#0B1220",
            fg="#AEBBD0",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(0, 3))
        tk.Checkbutton(
            self.host_options,
            text="Share my screen",
            variable=self.host_share_screen,
            command=self._update_host_share_state,
            bg="#0B1220",
            activebackground="#0B1220",
            fg=TEXT,
            activeforeground=TEXT,
            selectcolor=INPUT,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 2))
        tk.Label(
            self.host_options,
            text="Video only  ⓘ",
            bg="#0B1220",
            fg="#AEBBD0",
            font=("Segoe UI", 8),
        ).pack(anchor="w", pady=(0, 2))
        tk.Label(
            self.host_options,
            text="SOURCE",
            bg="#0B1220",
            fg="#AEBBD0",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(5, 3))
        self.source_combo = ttk.Combobox(
            self.host_options,
            textvariable=self.share_source,
            state="readonly",
            style="Source.TCombobox",
            font=("Segoe UI", 9),
        )
        self.source_combo.pack(fill=tk.X)
        self.source_combo.bind("<<ComboboxSelected>>", self._select_source)
        self._small_button(self.host_options, "REFRESH SOURCES", self.refresh_sources).pack(fill=tk.X, pady=(5, 0))
        tk.Label(
            self.host_options,
            text="QUALITY",
            bg="#0B1220",
            fg="#AEBBD0",
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(5, 3))
        self.quality_combo = ttk.Combobox(
            self.host_options,
            textvariable=self.quality_profile,
            values=list(QUALITY_PROFILES.keys()),
            state="readonly",
            style="Source.TCombobox",
            font=("Segoe UI", 9),
        )
        self.quality_combo.pack(fill=tk.X)
        self.quality_combo.bind("<<ComboboxSelected>>", self._select_quality)
        self.audio_note = tk.Label(
            self.host_options,
            text="Audio streaming is not implemented; this room shares video only.",
            bg="#0B1220",
            fg="#AEBBD0",
            wraplength=250,
            font=("Segoe UI", 8),
            justify=tk.LEFT,
        )
        self.audio_note.pack_forget()
        tk.Label(
            self.host_options,
            textvariable=self.current_source_label,
            bg="#0B1220",
            fg=TEXT,
            wraplength=250,
            font=("Segoe UI", 8, "bold"),
        ).pack_forget()

        self.start_button = self._button(controls, ">  START", self.start, primary=True)
        self.start_button.configure(pady=4, font=("Segoe UI", 8, "bold"))
        self.start_button.pack(fill=tk.X, pady=(0, 4))
        self.stop_button = self._button(controls, "STOP", self.stop)
        self.stop_button.configure(pady=4, font=("Segoe UI", 8, "bold"))
        self.stop_button.configure(state=tk.DISABLED)
        self.stop_button.pack(fill=tk.X)
        self.pause_button = self._button(controls, "||  PAUSE SHARING", self.toggle_pause_sharing)
        self.pause_button.configure(pady=4, font=("Segoe UI", 8, "bold"))
        self.pause_button.configure(state=tk.DISABLED)
        self.pause_button.pack(fill=tk.X, pady=(4, 0))
        self.reconnect_button = self._button(controls, "RECONNECT", self.reconnect)
        self.reconnect_button.configure(pady=4, font=("Segoe UI", 8, "bold"))
        self.reconnect_button.configure(state=tk.DISABLED)
        self.reconnect_button.pack(fill=tk.X, pady=(4, 0))
        self.auto_reconnect_check = tk.Checkbutton(
            controls,
            text="Auto reconnect",
            variable=self.auto_reconnect_enabled,
            bg="#0B1220",
            activebackground="#0B1220",
            fg=TEXT,
            activeforeground=TEXT,
            selectcolor=INPUT,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self.auto_reconnect_check.pack(fill=tk.X, pady=(4, 0))

        self.fullscreen_button = self._button(controls, "FULLSCREEN", self.enter_fullscreen)
        self.fullscreen_button.configure(pady=4, font=("Segoe UI", 8, "bold"))
        self.fullscreen_button.pack(fill=tk.X, pady=(4, 0))

        self.back_button = self._button(controls, "<-  BACK TO ROOMS", self.back_to_rooms)
        self.back_button.configure(pady=4, font=("Segoe UI", 8, "bold"))
        self.back_button.pack(fill=tk.X, pady=(4, 0))

        info = tk.Frame(controls, bg="#111A2B", highlightbackground="#263148", highlightthickness=1, padx=10, pady=8)
        tk.Label(info, text="LAN ADDRESS", bg="#111A2B", fg="#AEBBD0", font=("Segoe UI", 7, "bold")).pack(anchor="w")
        self.lan_address_label = tk.Label(
            info,
            text=f"{self._local_ip()}:{DEFAULT_PORT}",
            bg="#111A2B",
            fg=TEXT,
            font=("Consolas", 11, "bold"),
        )
        self.lan_address_label.pack(anchor="w", pady=(4, 0))
        self._small_button(info, "COPY ADDRESS", self.copy_lan_address).pack(fill=tk.X, pady=(6, 0))
        self._small_button(info, "COPY ROOM INFO", self.copy_room_info).pack(fill=tk.X, pady=(5, 0))

        self.viewers_card = tk.Frame(controls, bg="#111A2B", highlightbackground="#263148", highlightthickness=1, padx=10, pady=8)
        tk.Label(self.viewers_card, text="VIEWERS", bg="#111A2B", fg="#AEBBD0", font=("Segoe UI", 7, "bold")).pack(anchor="w")
        tk.Label(self.viewers_card, textvariable=self.viewer_count, bg="#111A2B", fg=TEXT, font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(3, 0))
        tk.Label(self.viewers_card, textvariable=self.viewer_list, bg="#111A2B", fg="#AEBBD0", wraplength=245, font=("Segoe UI", 8)).pack(anchor="w", pady=(3, 0))

    def continue_to_rooms(self):
        name = self.user_name.get().strip()
        if not name:
            messagebox.showerror("Name required", "Enter your name before continuing.")
            return
        self.status.set(f"Signed in as {name}")
        self._save_settings()
        self._show_page("room")

    def create_room(self):
        code = secrets.token_hex(3).upper()
        self.room_code.set(code)
        self.mode.set("share")
        self.host_share_screen.set(False)
        self.share_enabled = False
        self.share_paused.set(False)
        self._update_viewers([])
        self.refresh_sources()
        self.lan_address_label.configure(text=f"{self._local_ip()}:{self.port.get()}")
        self.session_title.configure(text="Share Screen")
        self.room_code_label.configure(text=code)
        self.name_label.configure(text=f"Host: {self.user_name.get().strip()}")
        self.host_options.pack(fill=tk.X, pady=(0, 12))
        self._set_display_message(self._host_waiting_text())
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.pause_button.configure(state=tk.DISABLED, text="PAUSE SHARING")
        self.reconnect_button.configure(state=tk.DISABLED)
        self.auto_reconnect_check.pack_forget()
        self._save_settings()
        self._show_page("session")

    def join_room(self):
        code = self.join_code.get().strip().upper()
        host = self.host.get().strip()
        self._apply_host_port()
        host = self.host.get().strip()
        if not host or not code:
            messagebox.showerror("Room required", "Enter the host address and room code.")
            return
        if not self._valid_host(host):
            messagebox.showerror("Invalid host", "Enter a valid host name or IP address.")
            return
        self.room_code.set(code)
        self.mode.set("watch")
        self.session_title.configure(text="Watch Screen")
        self.room_code_label.configure(text=code)
        self.name_label.configure(text=f"Viewer: {self.user_name.get().strip()}")
        self.host_options.pack_forget()
        self._update_viewers([])
        self._set_display_message("Click Start to join the room.")
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.pause_button.configure(state=tk.DISABLED)
        self.reconnect_button.configure(state=tk.NORMAL)
        self.auto_reconnect_check.pack(fill=tk.X, pady=(8, 0))
        self._save_settings()
        self._show_page("session")

    def back_to_rooms(self):
        self.stop()
        self.photo = None
        self._set_display_message("No active stream")
        self._save_settings()
        self._show_page("room")

    def _local_ip(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            try:
                sock.close()
            except Exception:
                pass

    def start(self):
        try:
            self._apply_host_port()
            port = int(self.port.get())
        except (TypeError, ValueError):
            messagebox.showerror("Invalid port", "Enter a valid numeric port.")
            return
        if not 1 <= port <= 65535:
            messagebox.showerror("Invalid port", "Enter a port from 1 to 65535.")
            return
        if self.mode.get() == "watch" and not self._valid_host(self.host.get().strip()):
            messagebox.showerror("Invalid host", "Enter a valid host name or IP address.")
            return

        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.reconnect_button.configure(state=tk.DISABLED)
        self._cancel_reconnect()
        self._save_settings()

        if self.mode.get() == "share":
            self.client = None
            self.share_enabled = self.host_share_screen.get()
            self.share_paused.set(False)
            self.pause_button.configure(state=tk.NORMAL, text="PAUSE SHARING")
            self._focus_selected_source()
            if self.share_enabled:
                self._start_capture_thread()
            else:
                with self.packet_lock:
                    self.latest_frame = self._waiting_frame()
                    self.latest_packet = self.waiting_packet
                    self.latest_packet_version += 1
            self.server = ShareServer(
                port,
                self.room_code.get(),
                self._host_packet,
                self._set_status,
                self._handle_server_start_failed,
                self._handle_viewers_changed,
            )
            self.server.start()
            if self.selected_source_name != "Entire screen" and self.share_enabled:
                self.after(300, self.iconify)
        else:
            self.server = None
            self.client = ViewerClient(
                self.host.get().strip(),
                port,
                self.room_code.get(),
                self.user_name.get().strip(),
                self.viewer_frames,
                self._set_status,
                self._handle_viewer_stopped,
            )
            self.client.start()

    def _apply_host_port(self):
        host = self.host.get().strip()
        if host.count(":") == 1:
            address, port = host.rsplit(":", 1)
            if address and port.isdigit():
                self.host.set(address)
                self.port.set(int(port))

    def _valid_host(self, host):
        if not host or any(char.isspace() for char in host):
            return False
        try:
            socket.getaddrinfo(host, None)
            return True
        except socket.gaierror:
            return False

    def stop(self):
        self._stop_capture_thread()
        self._cancel_reconnect()
        if self.server:
            self.server.stop()
            self.server = None
        if self.client:
            self.client.stop()
            self.client = None
        if self.start_button:
            self.start_button.configure(state=tk.NORMAL)
        if self.stop_button:
            self.stop_button.configure(state=tk.DISABLED)
        if self.pause_button:
            self.pause_button.configure(state=tk.DISABLED, text="PAUSE SHARING")
        if self.reconnect_button:
            self.reconnect_button.configure(state=tk.NORMAL if self.mode.get() == "watch" else tk.DISABLED)
        self.share_paused.set(False)
        self._save_settings()
        self._set_status("Stopped")

    def reconnect(self):
        if self.mode.get() != "watch":
            return
        self.stop()
        self.start()

    def _schedule_reconnect(self, reason):
        if self.mode.get() != "watch" or not self.auto_reconnect_enabled.get():
            return
        if self.reconnect_job:
            return
        self.reconnect_attempts += 1
        self.status.set(f"Viewer stopped: {reason}. Reconnecting in 3s...")
        self.reconnect_job = self.after(RECONNECT_DELAY_MS, self._run_scheduled_reconnect)

    def _run_scheduled_reconnect(self):
        self.reconnect_job = None
        if self.mode.get() == "watch" and not self.client:
            self.start()

    def _cancel_reconnect(self):
        if self.reconnect_job:
            try:
                self.after_cancel(self.reconnect_job)
            except tk.TclError:
                pass
            self.reconnect_job = None

    def _handle_server_start_failed(self, reason):
        def restore_controls():
            self._stop_capture_thread()
            self.server = None
            if self.start_button:
                self.start_button.configure(state=tk.NORMAL)
            if self.stop_button:
                self.stop_button.configure(state=tk.DISABLED)
            if self.reconnect_button:
                self.reconnect_button.configure(state=tk.DISABLED)
            self.status.set(f"Host failed: {reason}")

        self.after(0, restore_controls)

    def _handle_viewer_stopped(self, reason):
        def restore_controls():
            self.client = None
            if self.start_button:
                self.start_button.configure(state=tk.NORMAL)
            if self.stop_button:
                self.stop_button.configure(state=tk.DISABLED)
            if self.reconnect_button:
                self.reconnect_button.configure(state=tk.NORMAL)
            if self.auto_reconnect_enabled.get():
                self._schedule_reconnect(reason)
            else:
                self.status.set(f"Viewer stopped: {reason}")

        self.after(0, restore_controls)

    def toggle_pause_sharing(self):
        if self.mode.get() != "share":
            return
        paused = not self.share_paused.get()
        self.share_paused.set(paused)
        if paused:
            self.share_enabled = False
            self._stop_capture_thread()
            with self.packet_lock:
                self.latest_frame = self._waiting_frame()
                self.latest_packet = self.waiting_packet
                self.latest_packet_version += 1
            self.pause_button.configure(text="RESUME SHARING")
            self._set_display_message("Sharing is paused.")
            self._set_status("Sharing paused")
        else:
            self.share_enabled = self.host_share_screen.get()
            self.pause_button.configure(text="PAUSE SHARING")
            if self.share_enabled:
                self._start_capture_thread()
            self._set_display_message(self._host_waiting_text())
            self._set_status("Sharing resumed")

    def _handle_viewers_changed(self, viewers):
        self.after(0, lambda: self._update_viewers(viewers))

    def _update_viewers(self, viewers):
        count = len(viewers)
        self.viewer_count.set(f"{count} viewer{'s' if count != 1 else ''} connected")
        self.viewer_list.set(", ".join(viewers) if viewers else "Waiting for viewers")

    def copy_lan_address(self):
        value = self.lan_address_label.cget("text")
        self.clipboard_clear()
        self.clipboard_append(value)
        self._set_status("LAN address copied")

    def copy_room_info(self):
        value = f"{self.lan_address_label.cget('text')} / {self.room_code.get()}"
        self.clipboard_clear()
        self.clipboard_append(value)
        self._set_status("Room info copied")

    def _set_status(self, value):
        self.after(0, self.status.set, value)

    def _version_tuple(self, value):
        parts = []
        for part in str(value).strip().split("."):
            digits = "".join(ch for ch in part if ch.isdigit())
            parts.append(int(digits or 0))
        while len(parts) < 3:
            parts.append(0)
        return tuple(parts[:3])

    def check_for_updates(self, manual=False, status_widget=None, update_widget=None):
        if self.update_info:
            if status_widget and status_widget.winfo_exists():
                status_widget.configure(text=f"Update {self.update_info['version']} is available.")
            if update_widget and update_widget.winfo_exists():
                update_widget.configure(state=tk.NORMAL)
            return
        if self.update_checking:
            return
        self.update_checking = True
        if status_widget and status_widget.winfo_exists():
            status_widget.configure(text="Checking GitHub for updates...")
        threading.Thread(
            target=self._check_for_updates_worker,
            args=(manual, status_widget, update_widget),
            daemon=True,
        ).start()

    def _check_for_updates_worker(self, manual=False, status_widget=None, update_widget=None):
        found_update = False
        check_failed = False
        try:
            request = urllib.request.Request(
                UPDATE_MANIFEST_URL,
                headers={"User-Agent": f"Watch/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=6) as response:
                manifest = json.loads(response.read().decode("utf-8"))
            latest = str(manifest.get("version", "")).strip()
            if latest and self._version_tuple(latest) > self._version_tuple(APP_VERSION):
                self.update_info = {
                    "version": latest,
                    "download_url": manifest.get("download_url") or DEFAULT_DOWNLOAD_URL,
                    "notes": manifest.get("notes", ""),
                }
                found_update = True
                self.after(0, self._show_update_available)
                if status_widget:
                    self.after(0, status_widget.configure, {"text": f"Update {latest} is available."})
                if update_widget:
                    self.after(0, update_widget.configure, {"state": tk.NORMAL})
        except (OSError, ValueError, urllib.error.URLError):
            check_failed = True
            if manual and status_widget:
                self.after(0, status_widget.configure, {"text": "Could not check for updates. Check your internet connection."})
        finally:
            self.update_checking = False
            if manual and not found_update and not check_failed and status_widget:
                self.after(0, status_widget.configure, {"text": f"You are up to date. Version {APP_VERSION}."})
            if not found_update:
                try:
                    self.after(UPDATE_CHECK_INTERVAL_MS, self.check_for_updates)
                except RuntimeError:
                    pass

    def _show_update_available(self):
        if self.update_button and self.update_button.winfo_exists():
            self.update_button.configure(text=f"Update {self.update_info['version']}")
            self.update_button.grid()
        self._set_status(f"Update {self.update_info['version']} available")

    def show_settings_dialog(self):
        dialog = tk.Toplevel(self)
        dialog.title("Settings")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(
            dialog,
            text="Settings",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w", padx=24, pady=(22, 8))
        tk.Label(
            dialog,
            text=f"Current version: {APP_VERSION}",
            bg=PANEL,
            fg=MUTED,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=24, pady=(0, 18))

        update_card = tk.Frame(dialog, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1, padx=18, pady=16)
        update_card.pack(fill=tk.X, padx=24, pady=(0, 18))
        tk.Label(
            update_card,
            text="Updates",
            bg=PANEL_2,
            fg=TEXT,
            font=("Segoe UI", 13, "bold"),
        ).pack(anchor="w")
        status_text = "Update available." if self.update_info else "Check GitHub for a newer version."
        settings_status = tk.Label(
            update_card,
            text=status_text,
            bg=PANEL_2,
            fg=MUTED,
            justify=tk.LEFT,
            wraplength=390,
            font=("Segoe UI", 9),
        )
        settings_status.pack(anchor="w", pady=(6, 14))

        buttons = tk.Frame(update_card, bg=PANEL_2)
        buttons.pack(fill=tk.X)
        update_now = self._button(buttons, "UPDATE NOW", lambda: (dialog.destroy(), self.show_update_dialog()), primary=True)
        update_now.configure(state=tk.NORMAL if self.update_info else tk.DISABLED)
        update_now.pack(side=tk.RIGHT)
        check_button = self._small_button(
            buttons,
            "CHECK FOR UPDATES",
            lambda: self.check_for_updates(manual=True, status_widget=settings_status, update_widget=update_now),
        )
        check_button.pack(side=tk.RIGHT, padx=(0, 10))

        close_button = self._small_button(dialog, "CLOSE", dialog.destroy)
        close_button.pack(anchor="e", padx=24, pady=(0, 22))

        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")

    def show_update_dialog(self):
        if not self.update_info:
            return
        dialog = tk.Toplevel(self)
        dialog.title("Update Watch")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self)
        dialog.grab_set()

        tk.Label(
            dialog,
            text=f"Update to version {self.update_info['version']}",
            bg=PANEL,
            fg=TEXT,
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w", padx=24, pady=(22, 8))
        notes = self.update_info.get("notes") or "A new version is available from GitHub."
        tk.Label(
            dialog,
            text=notes,
            bg=PANEL,
            fg=MUTED,
            justify=tk.LEFT,
            wraplength=430,
            font=("Segoe UI", 10),
        ).pack(anchor="w", padx=24, pady=(0, 16))

        progress = ttk.Progressbar(dialog, mode="determinate", length=430)
        progress.pack(fill=tk.X, padx=24, pady=(0, 8))
        status_label = tk.Label(dialog, text="Ready to download", bg=PANEL, fg=MUTED, font=("Segoe UI", 9))
        status_label.pack(anchor="w", padx=24, pady=(0, 18))

        buttons = tk.Frame(dialog, bg=PANEL)
        buttons.pack(fill=tk.X, padx=24, pady=(0, 22))
        cancel_button = self._small_button(buttons, "CANCEL", dialog.destroy)
        cancel_button.pack(side=tk.RIGHT)
        update_button = self._button(
            buttons,
            "UPDATE NOW",
            lambda: self._start_update_download(dialog, progress, status_label, update_button, cancel_button),
            primary=True,
        )
        update_button.pack(side=tk.RIGHT, padx=(0, 10))

        dialog.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - dialog.winfo_width()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - dialog.winfo_height()) // 2)
        dialog.geometry(f"+{x}+{y}")

    def _start_update_download(self, dialog, progress, status_label, update_button, cancel_button):
        update_button.configure(state=tk.DISABLED)
        cancel_button.configure(state=tk.DISABLED)
        status_label.configure(text="Downloading update...")
        threading.Thread(
            target=self._download_update_worker,
            args=(dialog, progress, status_label),
            daemon=True,
        ).start()

    def _download_update_worker(self, dialog, progress, status_label):
        try:
            url = self.update_info.get("download_url") or DEFAULT_DOWNLOAD_URL
            target = os.path.join(tempfile.gettempdir(), "Watch-update.exe")
            request = urllib.request.Request(url, headers={"User-Agent": f"Watch/{APP_VERSION}"})
            with urllib.request.urlopen(request, timeout=15) as response, open(target, "wb") as output:
                total = int(response.headers.get("Content-Length") or 0)
                downloaded = 0
                while True:
                    chunk = response.read(1024 * 128)
                    if not chunk:
                        break
                    output.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        percent = min(100, int(downloaded * 100 / total))
                        self.after(0, progress.configure, {"value": percent})
                        self.after(0, status_label.configure, {"text": f"Downloading update... {percent}%"})
                    else:
                        self.after(0, status_label.configure, {"text": f"Downloaded {downloaded // 1024} KB"})
            self.after(0, self._finish_update, dialog, status_label, target)
        except Exception as exc:
            self.after(0, status_label.configure, {"text": f"Update failed: {exc}"})

    def _finish_update(self, dialog, status_label, downloaded_exe):
        status_label.configure(text="Download complete. Restarting...")
        current_exe = sys.executable if getattr(sys, "frozen", False) else os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist", "Watch.exe")
        if not getattr(sys, "frozen", False):
            try:
                os.makedirs(os.path.dirname(current_exe), exist_ok=True)
                with open(downloaded_exe, "rb") as src, open(current_exe, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 128)
                        if not chunk:
                            break
                        dst.write(chunk)
                dialog.destroy()
                messagebox.showinfo("Update ready", "dist\\Watch.exe has been updated.")
                return
            except OSError as exc:
                status_label.configure(text=f"Could not install update: {exc}")
                return

        updater = os.path.join(tempfile.gettempdir(), "watch_apply_update.bat")
        script = "\r\n".join([
            "@echo off",
            "timeout /t 2 /nobreak >nul",
            f'copy /y "{downloaded_exe}" "{current_exe}" >nul',
            f'start "" "{current_exe}"',
            f'del "{downloaded_exe}" >nul 2>nul',
            'del "%~f0" >nul 2>nul',
        ])
        try:
            with open(updater, "w", encoding="utf-8") as updater_file:
                updater_file.write(script)
            subprocess.Popen(["cmd", "/c", updater], close_fds=True)
            self.destroy()
        except OSError as exc:
            status_label.configure(text=f"Could not apply update: {exc}")

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
        except (OSError, ValueError):
            return
        self.user_name.set(str(settings.get("user_name", "")))
        saved_host = str(settings.get("host", "")).strip()
        self.host.set("" if saved_host == "127.0.0.1" else saved_host)
        try:
            self.port.set(int(settings.get("port", self.port.get())))
        except (TypeError, ValueError, tk.TclError):
            self.port.set(DEFAULT_PORT)
        quality = settings.get("quality", self.quality_profile.get())
        if quality in QUALITY_PROFILES:
            self.quality_profile.set("Balanced" if quality == "High" else quality)
        self.mode.set(settings.get("last_mode", self.mode.get()) if settings.get("last_mode") in ("share", "watch") else self.mode.get())
        self.auto_reconnect_enabled.set(bool(settings.get("auto_reconnect", True)))

    def _save_settings(self):
        settings = {
            "user_name": self.user_name.get().strip(),
            "host": self.host.get().strip(),
            "port": self.port.get(),
            "quality": self.quality_profile.get(),
            "last_mode": self.mode.get(),
            "auto_reconnect": self.auto_reconnect_enabled.get(),
        }
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as settings_file:
                json.dump(settings, settings_file, indent=2)
        except OSError as exc:
            self.status.set(f"Could not save settings: {exc}")

    def _schedule_preview(self):
        try:
            if self.page != "session":
                return
            if self.client:
                self._show_viewer_frame()
            elif self.mode.get() == "share" and self.server and self.host_share_screen.get() and not self.share_paused.get():
                self._show_host_preview()
            elif self.mode.get() == "share":
                with self.packet_lock:
                    self.latest_frame = self._waiting_frame()
                    self.latest_packet = self.waiting_packet
                if self.display:
                    self._set_display_message(self._host_waiting_text())
        finally:
            self.after(CAPTURE_INTERVAL_MS, self._schedule_preview)

    def _host_frame(self):
        if self.host_share_screen.get() and not self.share_paused.get():
            return self.latest_frame
        return self._waiting_frame()

    def _host_packet(self):
        with self.packet_lock:
            if self.share_enabled and not self.share_paused.get():
                if self.latest_packet is None:
                    return -1, self.waiting_packet
                return self.latest_packet_version, self.latest_packet
            return -1, self.waiting_packet

    def _start_capture_thread(self):
        self._stop_capture_thread()
        self.capture_stop_event = threading.Event()
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()

    def _stop_capture_thread(self):
        if self.capture_stop_event:
            self.capture_stop_event.set()
        if self.capture_thread and self.capture_thread.is_alive():
            self.capture_thread.join(timeout=1)
        self.capture_stop_event = None
        self.capture_thread = None

    def _capture_loop(self):
        frame_interval = 1 / STREAM_FPS
        next_capture_time = time.monotonic()
        while self.capture_stop_event and not self.capture_stop_event.is_set():
            started = time.monotonic()
            try:
                frame = self._capture_selected_source()
                packet = make_packet(*frame, jpeg_quality=self._quality_settings()["quality"])
                with self.packet_lock:
                    self.latest_frame = frame
                    self.latest_packet = packet
                    self.latest_packet_version += 1
                self.capture_error = None
                elapsed = time.monotonic() - started
                if elapsed > frame_interval * 1.5:
                    self._set_status("Streaming is heavy; lower quality if lag continues")
            except Exception as exc:
                self.capture_error = str(exc)
            next_capture_time += frame_interval
            sleep_time = next_capture_time - time.monotonic()
            if sleep_time > 0:
                time.sleep(sleep_time)
            else:
                next_capture_time = time.monotonic()

    def _capture_selected_source(self):
        source = self.selected_source_name
        settings = self._quality_settings()
        max_width = settings["width"]
        max_height = settings["height"]
        if source == "Entire screen":
            return self.capture.capture(max_width=max_width, max_height=max_height)
        hwnd = self.window_sources.get(source)
        if not hwnd:
            raise RuntimeError("Selected source is no longer available")
        return self.capture.capture_window(hwnd, max_width=max_width, max_height=max_height)

    def _quality_settings(self):
        return QUALITY_PROFILES.get(self.quality_profile.get(), QUALITY_PROFILES["Balanced"])

    def _select_quality(self, _event=None):
        self._set_status(f"Quality: {self.quality_profile.get()}")

    def _waiting_frame(self):
        return WAITING_WIDTH, WAITING_HEIGHT, WAITING_FRAME

    def _host_waiting_text(self):
        if self.share_paused.get():
            return "Sharing is paused."
        if self.host_share_screen.get():
            if self.server:
                return "Sharing this screen."
            return "Click Start to share this screen."
        return "Room is open. Screen sharing is off."

    def _update_host_share_state(self):
        if self.mode.get() != "share" or not self.display:
            return
        self.photo = None
        self.share_paused.set(False)
        self.share_enabled = self.host_share_screen.get()
        if self.pause_button:
            self.pause_button.configure(text="PAUSE SHARING")
        self._set_display_message(self._host_waiting_text())
        if self.share_enabled:
            self._focus_selected_source()
            if self.server:
                self._start_capture_thread()
        else:
            self._stop_capture_thread()
            with self.packet_lock:
                self.latest_frame = self._waiting_frame()
                self.latest_packet = self.waiting_packet
                self.latest_packet_version += 1

    def refresh_sources(self):
        self.window_sources = {}
        values = ["Entire screen"]
        try:
            for hwnd, title in self.capture.visible_windows():
                label = title
                if label in self.window_sources:
                    label = f"{title} ({hwnd})"
                self.window_sources[label] = hwnd
                values.append(label)
        except Exception as exc:
            self._set_status(f"Could not refresh sources: {exc}")
        if self.share_source.get() not in values:
            self.share_source.set("Entire screen")
        self.selected_source_name = self.share_source.get()
        self.source_combo.configure(values=values)
        self.source_combo.set(self.share_source.get())
        self.current_source_label.set(f"Source: {self.share_source.get()}")

    def _select_source(self, _event=None):
        selected = self.share_source.get()
        if not selected:
            return
        self.selected_source_name = selected
        self.current_source_label.set(f"Source: {selected}")
        if self.mode.get() == "share" and self.host_share_screen.get():
            self._focus_selected_source()

    def _focus_selected_source(self):
        source = self.selected_source_name
        hwnd = self.window_sources.get(source)
        if hwnd:
            try:
                self.capture.bring_to_front(hwnd)
                self._set_status(f"Window only: {source}")
            except Exception as exc:
                self._set_status(f"Could not focus source: {exc}")
        elif source == "Entire screen":
            self._set_status("Sharing source: Entire screen")

    def _show_local_frame(self):
        try:
            source = self.share_source.get()
            settings = self._quality_settings()
            if source == "Entire screen":
                frame = self.capture.capture(max_width=settings["width"], max_height=settings["height"])
            else:
                hwnd = self.window_sources.get(source)
                if not hwnd:
                    raise RuntimeError("Selected source is no longer available")
                frame = self.capture.capture_window(hwnd, max_width=settings["width"], max_height=settings["height"])
            self.latest_frame = frame
            self.latest_packet = make_packet(*frame, jpeg_quality=self._quality_settings()["quality"])
            self.latest_packet_version += 1
            now = time.monotonic()
            if now - self.last_preview_time >= PREVIEW_INTERVAL_MS / 1000:
                self.last_preview_time = now
                self._render_frame(*frame)
        except Exception as exc:
            self._set_display_message(f"Screen capture failed: {exc}")

    def _show_host_preview(self):
        if self.capture_error:
            self._set_display_message(f"Screen capture failed: {self.capture_error}")
            return
        now = time.monotonic()
        if now - self.last_preview_time < PREVIEW_INTERVAL_MS / 1000:
            return
        if not self.display or self.display.winfo_width() < 80 or self.display.winfo_height() < 80:
            return
        with self.packet_lock:
            frame = self.latest_frame
        if frame is None:
            self._set_display_message("Preparing stream...")
            return
        self.last_preview_time = now
        self._render_frame(*frame)

    def _show_viewer_frame(self):
        now = time.monotonic()
        if now - self.last_viewer_render_time < VIEWER_RENDER_INTERVAL_MS / 1000:
            return

        frame_info = None
        try:
            while True:
                frame_info = self.viewer_frames.get_nowait()
        except queue.Empty:
            pass
        if frame_info is None:
            return
        width, height, frame_data, encoded = frame_info
        if encoded:
            try:
                width, height, frame_data = decode_packet_payload(width, height, frame_data)
            except ValueError as exc:
                self._set_status(str(exc))
                return
        self.last_viewer_render_time = now
        if width == WAITING_WIDTH and height == WAITING_HEIGHT and frame_data == WAITING_FRAME:
            self.current_rgb_frame = None
            self.photo = None
            if self.display:
                self._set_display_message("Host is connected. Screen sharing is off or paused.")
            return
        self._render_frame(width, height, frame_data)

    def _render_frame(self, width, height, rgb):
        self.current_rgb_frame = (width, height, rgb)

        def photo_for(target_width, target_height):
            image = Image.frombytes("RGB", (width, height), rgb)
            scale = min(target_width / width, target_height / height)
            new_width = max(1, int(width * scale))
            new_height = max(1, int(height * scale))
            if new_width != width or new_height != height:
                try:
                    resample_mode = Image.Resampling.BILINEAR
                except AttributeError:
                    resample_mode = Image.BILINEAR
                image = image.resize((new_width, new_height), resample_mode)
            return ImageTk.PhotoImage(image)
        
        if self.fullscreen_display:
            if not self.fullscreen_display.winfo_exists():
                return
            if self.fullscreen_rendering:
                return
            self.fullscreen_rendering = True
            fs_width = max(1, self.fullscreen_display.winfo_width())
            fs_height = max(1, self.fullscreen_display.winfo_height())
            render_width = min(fs_width, MAX_FULLSCREEN_RENDER_WIDTH)
            render_height = min(fs_height, MAX_FULLSCREEN_RENDER_HEIGHT)
            try:
                self.fullscreen_photo = photo_for(render_width, render_height)

                if hasattr(self, "fullscreen_text_id"):
                    try:
                        self.fullscreen_display.delete(self.fullscreen_text_id)
                    except tk.TclError:
                        pass
                    del self.fullscreen_text_id
                if hasattr(self, "fullscreen_image_id") and self.fullscreen_display.winfo_exists():
                    self.fullscreen_display.itemconfig(self.fullscreen_image_id, image=self.fullscreen_photo)
                    self.fullscreen_display.coords(self.fullscreen_image_id, fs_width // 2, fs_height // 2)
                else:
                    self.fullscreen_image_id = self.fullscreen_display.create_image(
                        fs_width // 2, fs_height // 2,
                        image=self.fullscreen_photo,
                        anchor=tk.CENTER
                    )
            finally:
                self.fullscreen_rendering = False
        else:
            target_width = max(1, self.display.winfo_width())
            target_height = max(1, self.display.winfo_height())
            render_width = min(target_width, MAX_RENDER_WIDTH)
            render_height = min(target_height, MAX_RENDER_HEIGHT)
            self.photo = photo_for(render_width, render_height)
            self.display_message = None
            
            if self.display.cget("text"):
                self.display.configure(text="")
            self.display.configure(image=self.photo)

    def _rerender_current_frame(self):
        if self.current_rgb_frame:
            self._render_frame(*self.current_rgb_frame)

    def enter_fullscreen(self):
        if not self.display or self.fullscreen_active:
            return
        self.fullscreen_active = True
        self.fullscreen_window = tk.Toplevel(self)
        self.fullscreen_window.configure(bg="#000000")
        self.fullscreen_window.withdraw()
        self.fullscreen_window.attributes("-fullscreen", True)
        self.fullscreen_window.bind("<Escape>", lambda _event: self.exit_fullscreen())
        self.fullscreen_window.protocol("WM_DELETE_WINDOW", self.exit_fullscreen)
        self.fullscreen_window.bind("<Motion>", self._fullscreen_motion)
        
        self.fullscreen_display = tk.Canvas(
            self.fullscreen_window,
            bg="#000000",
            highlightthickness=0,
            borderwidth=0,
        )
        self.fullscreen_display.pack(fill=tk.BOTH, expand=True)
        self.fullscreen_display.bind("<Double-Button-1>", lambda _event: self.exit_fullscreen())
        
        self.fullscreen_window.deiconify()
        self.fullscreen_window.lift()
        self.fullscreen_window.update_idletasks()
        fs_width = self.fullscreen_window.winfo_width()
        fs_height = self.fullscreen_window.winfo_height()
        
        if self.current_rgb_frame:
            self._render_frame(*self.current_rgb_frame)
            self.after(100, self._rerender_current_frame)
        else:
            self.fullscreen_text_id = self.fullscreen_display.create_text(
                fs_width // 2, fs_height // 2,
                text=self.display.cget("text"),
                fill="#F9FAFB",
                font=("Segoe UI", 32, "bold")
            )
        self.fullscreen_hint_id = self.fullscreen_display.create_text(
            fs_width // 2,
            max(36, fs_height - 48),
            text="Esc or double-click to exit",
            fill="#CBD5E1",
            font=("Segoe UI", 12, "bold"),
        )
        self.after(2200, self._hide_fullscreen_hint)
        self._schedule_hide_fullscreen_cursor()
        self.fullscreen_window.focus_force()

    def _hide_fullscreen_hint(self):
        if self.fullscreen_display and self.fullscreen_hint_id:
            try:
                self.fullscreen_display.delete(self.fullscreen_hint_id)
            except tk.TclError:
                pass
            self.fullscreen_hint_id = None

    def _fullscreen_motion(self, _event=None):
        if self.fullscreen_window:
            self.fullscreen_window.configure(cursor="")
            self._schedule_hide_fullscreen_cursor()

    def _schedule_hide_fullscreen_cursor(self):
        if not self.fullscreen_window:
            return
        if self.fullscreen_cursor_job:
            self.after_cancel(self.fullscreen_cursor_job)
        self.fullscreen_cursor_job = self.after(1800, self._hide_fullscreen_cursor)

    def _hide_fullscreen_cursor(self):
        self.fullscreen_cursor_job = None
        if self.fullscreen_window:
            self.fullscreen_window.configure(cursor="none")

    def exit_fullscreen(self):
        if not self.fullscreen_active:
            return
        if hasattr(self, "fullscreen_image_id"):
            del self.fullscreen_image_id
        if hasattr(self, "fullscreen_text_id"):
            del self.fullscreen_text_id
        self.fullscreen_hint_id = None
        if self.fullscreen_cursor_job:
            try:
                self.after_cancel(self.fullscreen_cursor_job)
            except tk.TclError:
                pass
            self.fullscreen_cursor_job = None
        self.fullscreen_photo = None
        if self.fullscreen_window:
            self.fullscreen_window.destroy()
        self.fullscreen_window = None
        self.fullscreen_display = None
        self.fullscreen_rendering = False
        self.fullscreen_active = False
        self._rerender_current_frame()

    def destroy(self):
        self._cancel_reconnect()
        self._save_settings()
        self.exit_fullscreen()
        self.stop()
        super().destroy()


if __name__ == "__main__":
    app = ScreenShareApp()
    app.mainloop()


