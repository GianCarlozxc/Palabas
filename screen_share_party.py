import ctypes
import ctypes.wintypes
import io
import json
import queue
import secrets
import socket
import struct
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
import zlib
from PIL import Image, ImageTk


FRAME_HEADER = struct.Struct("!III")
DEFAULT_PORT = 5050
STREAM_FPS = 10
VIEWER_RENDER_FPS = 10
CAPTURE_INTERVAL_MS = int(1000 / STREAM_FPS)
VIEWER_RENDER_INTERVAL_MS = int(1000 / VIEWER_RENDER_FPS)
PREVIEW_INTERVAL_MS = 250
MAX_PREVIEW_WIDTH = 1280
MAX_PREVIEW_HEIGHT = 720
WAITING_WIDTH = 1280
WAITING_HEIGHT = 720
MAX_RENDER_WIDTH = 1600
MAX_RENDER_HEIGHT = 900
MAX_FULLSCREEN_RENDER_WIDTH = 1280
MAX_FULLSCREEN_RENDER_HEIGHT = 720
JPEG_QUALITY = 68
SOCKET_BUFFER_SIZE = 65536
SEND_TIMEOUT_SECONDS = 0.08
WAITING_FRAME = bytes([8, 8, 8]) * WAITING_WIDTH * WAITING_HEIGHT
BG = "#090A0F"
PANEL = "#11131C"
PANEL_2 = "#171A25"
SURFACE = "#222838"
INPUT = "#0D1018"
ACCENT = "#22C55E"
ACCENT_HOVER = "#16A34A"
TEXT = "#F8FAFC"
MUTED = "#94A3B8"
BORDER = "#2A3142"
WARNING = "#F59E0B"

# Dark Mode Colors
DARK_BG = "#090A0F"
DARK_PANEL = "#11131C"
DARK_PANEL_2 = "#171A25"
DARK_SURFACE = "#222838"
DARK_INPUT = "#0D1018"
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


def make_packet(width, height, rgb):
    image = Image.frombytes("RGB", (width, height), rgb)
    payload_buffer = io.BytesIO()
    image.save(payload_buffer, format="JPEG", quality=JPEG_QUALITY, optimize=False, subsampling=2)
    payload = payload_buffer.getvalue()
    return FRAME_HEADER.pack(width, height, len(payload)) + payload


def decode_packet_payload(width, height, payload):
    try:
        image = Image.open(io.BytesIO(payload))
        if image.mode != "RGB":
            image = image.convert("RGB")
        return image.size[0], image.size[1], image.tobytes()
    except Exception:
        return width, height, zlib.decompress(payload)


def recv_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError("Connection closed")
        data.extend(chunk)
    return bytes(data)


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


class ShareServer:
    def __init__(self, port, room_code, packet_source, status_callback):
        self.port = port
        self.room_code = room_code
        self.packet_source = packet_source
        self.status_callback = status_callback
        self.stop_event = threading.Event()
        self.clients = []
        self.client_versions = {}
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def _status(self, value):
        try:
            self.status_callback(value)
        except RuntimeError:
            pass

    def stop(self):
        self.stop_event.set()
        with self.lock:
            clients = list(self.clients)
            self.clients.clear()
            self.client_versions.clear()
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
                client.close()
            except OSError:
                pass

    def _run(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("", self.port))
        listener.listen()
        listener.settimeout(0.25)
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
                    except OSError:
                        dead_clients.append(client)

                if dead_clients:
                    with self.lock:
                        for client in dead_clients:
                            if client in self.clients:
                                self.clients.remove(client)
                            self.client_versions.pop(client, None)
                            try:
                                client.close()
                            except OSError:
                                pass

                time.sleep(1 / STREAM_FPS)
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
                    client.close()
                    self._status(f"Rejected wrong room code from {addr[0]}")
                    continue
                viewer_name = hello.get("name", "Viewer").strip() or "Viewer"
                client.settimeout(SEND_TIMEOUT_SECONDS)
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_BUFFER_SIZE)
                with self.lock:
                    self.clients.append(client)
                    self.client_versions[client] = None
                    count = len(self.clients)
                self._status(f"{viewer_name} connected from {addr[0]} ({count})")
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
    def __init__(self, host, port, room_code, viewer_name, frame_queue, status_callback):
        self.host = host
        self.port = port
        self.room_code = room_code
        self.viewer_name = viewer_name
        self.frame_queue = frame_queue
        self.status_callback = status_callback
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
            self.sock.settimeout(None)
            self._status("Watching shared screen")
            while not self.stop_event.is_set():
                header = recv_exact(self.sock, FRAME_HEADER.size)
                width, height, payload_size = FRAME_HEADER.unpack(header)
                payload = recv_exact(self.sock, payload_size)
                self._push_frame(width, height, payload, True)
        except Exception as exc:
            if not self.stop_event.is_set():
                self._status(f"Viewer stopped: {exc}")

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
        self.fullscreen_photo = None
        self.server = None
        self.client = None
        self.page = "name"
        self.viewer_frames = queue.Queue(maxsize=1)
        self.mode = tk.StringVar(value="share")
        self.host = tk.StringVar(value="127.0.0.1")
        self.port = tk.IntVar(value=DEFAULT_PORT)
        self.user_name = tk.StringVar()
        self.room_code = tk.StringVar()
        self.join_code = tk.StringVar()
        self.host_share_screen = tk.BooleanVar(value=False)
        self.share_enabled = False
        self.host_audio_enabled = tk.BooleanVar(value=False)
        self.share_source = tk.StringVar(value="Entire screen")
        self.selected_source_name = "Entire screen"
        self.window_sources = {}
        self.current_source_label = tk.StringVar(value="Source: Entire screen")
        self.status = tk.StringVar(value="Ready")
        self.frames = {}
        self.display = None
        self.fullscreen_active = False
        self.fullscreen_window = None
        self.fullscreen_display = None
        self.fullscreen_rendering = False
        self.start_button = None
        self.stop_button = None

        self._build_ui()
        self.bind("<Escape>", lambda _event: self.exit_fullscreen())
        self.bind("<Map>", self._restore_borderless)
        self._schedule_preview()

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

        self.root = ttk.Frame(self, padding=18)
        self.root.pack(fill=tk.BOTH, expand=True)

        self.header = tk.Frame(self.root, bg=BG)
        self.header.pack(fill=tk.X, pady=(0, 16))
        self.header.columnconfigure(0, weight=0)
        self.header.columnconfigure(1, weight=0)
        self.header.columnconfigure(2, weight=1)
        self.header.columnconfigure(3, weight=0)
        self.header.columnconfigure(4, weight=0)
        self.header.columnconfigure(5, weight=0)
        self.header.columnconfigure(6, weight=0)
        self.header.bind("<ButtonPress-1>", self._start_window_drag)
        self.header.bind("<B1-Motion>", self._drag_window)

        self.brand_frame = tk.Frame(self.header, bg=BG)
        self.brand_frame.grid(row=0, column=0, sticky="w")
        self.brand_frame.bind("<ButtonPress-1>", self._start_window_drag)
        self.brand_frame.bind("<B1-Motion>", self._drag_window)
        
        self.logo_label = tk.Label(
            self.brand_frame,
            text="WATCH",
            bg=BG,
            fg=TEXT,
            font=("Segoe UI", 18, "bold"),
            padx=0,
        )
        self.logo_label.pack(side=tk.LEFT)
        self.logo_label.bind("<ButtonPress-1>", self._start_window_drag)
        self.logo_label.bind("<B1-Motion>", self._drag_window)

        self.logo_mark = tk.Label(
            self.brand_frame,
            text="LIVE",
            bg=ACCENT,
            fg="#04130A",
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=3,
        )
        self.logo_mark.pack(side=tk.LEFT, padx=(10, 0))
        self.logo_mark.bind("<ButtonPress-1>", self._start_window_drag)
        self.logo_mark.bind("<B1-Motion>", self._drag_window)
        
        self.nav_frame = tk.Frame(self.header, bg=BG)
        self.nav_frame.grid(row=0, column=1, sticky="w", padx=(22, 0))
        for label in ("Profile", "Rooms", "Session"):
            lbl = tk.Label(
                self.nav_frame,
                text=label,
                bg=BG,
                fg=MUTED,
                font=("Segoe UI", 10, "bold"),
                cursor="hand2",
            )
            lbl.pack(side=tk.LEFT, padx=(0, 18))
            def make_hover(l):
                return lambda e: l.config(fg=TEXT)
            def make_leave(l):
                return lambda e: l.config(fg=MUTED)
            lbl.bind("<Enter>", make_hover(lbl))
            lbl.bind("<Leave>", make_leave(lbl))
            self.nav_labels.append(lbl)
            
        self.theme_mode = tk.StringVar(value="dark")
        self.theme_toggle_btn = tk.Canvas(
            self.header,
            width=34,
            height=30,
            bg=SURFACE,
            cursor="hand2",
            highlightthickness=0,
            bd=0,
        )
        self.theme_toggle_btn.grid(row=0, column=4, sticky="e", padx=(12, 0))
        self.theme_toggle_btn.bind("<Button-1>", lambda _event: self.toggle_theme())
        self.theme_toggle_btn.bind("<Enter>", lambda _event: self._draw_theme_icon(hover=True))
        self.theme_toggle_btn.bind("<Leave>", lambda _event: self._draw_theme_icon(hover=False))
        self._draw_theme_icon()

        self.minimize_button = self._window_control_button("-", self._minimize_window)
        self.minimize_button.grid(row=0, column=5, sticky="e", padx=(8, 0))

        self.close_button = self._window_control_button("X", self.destroy, danger=True)
        self.close_button.grid(row=0, column=6, sticky="e", padx=(6, 0))
        
        self.status_label = tk.Label(
            self.header,
            textvariable=self.status,
            bg=PANEL_2,
            fg=TEXT,
            font=("Segoe UI", 9, "bold"),
            padx=12,
            pady=6,
        )
        self.status_label.grid(row=0, column=3, sticky="e")

        self.content = ttk.Frame(self.root)
        self.content.pack(fill=tk.BOTH, expand=True)

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
        x_offset, y_offset = self._drag_start
        self.geometry(f"+{event.x_root - x_offset}+{event.y_root - y_offset}")

    def _minimize_window(self):
        self.overrideredirect(False)
        self.iconify()

    def _restore_borderless(self, _event=None):
        if self.state() == "normal":
            self.after(10, lambda: self.overrideredirect(True))

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
            self.theme_toggle_btn.create_oval(13, 11, 21, 19, fill=fg, outline=fg)
            for x1, y1, x2, y2 in (
                (17, 4, 17, 8),
                (17, 22, 17, 26),
                (5, 15, 9, 15),
                (25, 15, 29, 15),
                (8, 6, 11, 9),
                (23, 21, 26, 24),
                (8, 24, 11, 21),
                (23, 9, 26, 6),
            ):
                self.theme_toggle_btn.create_line(x1, y1, x2, y2, fill=fg, width=2, capstyle=tk.ROUND)
        else:
            self.theme_toggle_btn.create_oval(10, 7, 24, 23, fill=fg, outline=fg)
            self.theme_toggle_btn.create_oval(16, 4, 29, 20, fill=bg, outline=bg)

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
        if hasattr(self, "close_button") and self.close_button.winfo_exists():
            self.close_button.configure(bg="#7F1D1D", activebackground="#991B1B", fg=TEXT, activeforeground=TEXT)
        if hasattr(self, "source_combo") and self.source_combo.winfo_exists():
            self.source_combo.configure(style="Source.TCombobox")
            
        if hasattr(self, "custom_buttons"):
            for btn in self.custom_buttons:
                if btn.winfo_exists():
                    is_primary = (btn.cget("text") in ("CONTINUE", "CREATE ROOM", "JOIN ROOM", "START"))
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
        page = ttk.Frame(self.content)
        self.frames["name"] = page

        shell = ttk.Frame(page)
        shell.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=760)
        shell.columnconfigure(0, weight=1)
        shell.columnconfigure(1, weight=0, minsize=320)

        intro = ttk.Frame(shell)
        intro.grid(row=0, column=0, sticky="nsew", padx=(0, 28))

        ttk.Label(
            intro,
            text="WATCH PARTY",
            foreground=ACCENT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            intro,
            text="Share a screen. Keep everyone in sync.",
            font=("Segoe UI", 30, "bold"),
            wraplength=360,
        ).pack(anchor="w", pady=(8, 12))
        ttk.Label(
            intro,
            text="A lightweight LAN viewer for rooms, watch parties, and quick screen handoff.",
            foreground=MUTED,
            wraplength=360,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        card = ttk.Frame(shell, style="Panel.TFrame", padding=28)
        card.grid(row=0, column=1, sticky="nsew")
        ttk.Label(
            card,
            text="Display name",
            style="Panel.TLabel",
            foreground=TEXT,
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w", pady=(0, 20))
        ttk.Label(
            card,
            text="This is shown to the room host.",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(0, 8))
        ttk.Entry(card, textvariable=self.user_name, width=34).pack(fill=tk.X, pady=(0, 16))
        self._button(card, "CONTINUE", self.continue_to_rooms, primary=True).pack(fill=tk.X)

    def _build_room_page(self):
        page = ttk.Frame(self.content)
        self.frames["room"] = page

        shell = ttk.Frame(page)
        shell.pack(fill=tk.BOTH, expand=True)
        shell.columnconfigure(0, weight=1)
        shell.columnconfigure(1, weight=1)
        shell.rowconfigure(1, weight=1)

        ttk.Label(
            shell,
            text="Choose a mode",
            font=("Segoe UI", 28, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(8, 18))

        create = ttk.Frame(shell, style="Panel.TFrame", padding=24)
        create.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        ttk.Label(create, text="HOST", style="Panel.TLabel", foreground=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(create, text="Create a room", style="Panel.TLabel", font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(8, 10))
        ttk.Label(create, text="Start a LAN room, choose a source, and control when screen sharing begins.", style="Panel.TLabel", foreground=MUTED, wraplength=330).pack(anchor="w", pady=(0, 18))
        ttk.Frame(create, style="Card.TFrame", height=1).pack(fill=tk.X, pady=(0, 18))
        self._button(create, "CREATE ROOM", self.create_room, primary=True).pack(fill=tk.X)

        join = ttk.Frame(shell, style="Panel.TFrame", padding=24)
        join.grid(row=1, column=1, sticky="nsew", padx=(10, 0))
        ttk.Label(join, text="VIEWER", style="Panel.TLabel", foreground=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(join, text="Join a room", style="Panel.TLabel", font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(8, 10))
        ttk.Label(join, text="Host address", style="Panel.TLabel", foreground=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Entry(join, textvariable=self.host, width=32).pack(fill=tk.X, pady=(6, 12))
        ttk.Label(join, text="Port", style="Panel.TLabel", foreground=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Entry(join, textvariable=self.port, width=32).pack(fill=tk.X, pady=(6, 12))
        ttk.Label(join, text="Room code", style="Panel.TLabel", foreground=MUTED, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Entry(join, textvariable=self.join_code, width=32).pack(fill=tk.X, pady=(6, 18))
        self._button(join, "JOIN ROOM", self.join_room, primary=True).pack(fill=tk.X)

    def _build_session_page(self):
        body = ttk.Frame(self.content)
        self.frames["session"] = body
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=320)
        body.rowconfigure(0, weight=1)

        stage = ttk.Frame(body, style="Panel.TFrame", padding=10)
        stage.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        stage.rowconfigure(0, weight=1)
        stage.columnconfigure(0, weight=1)

        display_wrap = tk.Frame(stage, bg="#000000", padx=1, pady=1)
        display_wrap.grid(row=0, column=0, sticky="nsew")

        self.display = tk.Label(
            display_wrap,
            text="No active stream",
            bg="#000000",
            fg="#F9FAFB",
            anchor=tk.CENTER,
            compound=tk.CENTER,
            font=("Segoe UI", 26, "bold"),
        )
        self.display.pack(fill=tk.BOTH, expand=True)
        self.display.bind("<Double-Button-1>", lambda _event: self.enter_fullscreen())
        self.display.bind("<Configure>", lambda _event: self._rerender_current_frame())

        controls = ttk.Frame(body, style="Panel.TFrame", padding=16)
        controls.grid(row=0, column=1, sticky="ns")
        controls.configure(width=320)
        controls.grid_propagate(False)

        ttk.Label(
            controls,
            text="SESSION",
            style="Panel.TLabel",
            foreground=ACCENT,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        self.session_title = ttk.Label(
            controls,
            text="Room",
            style="Panel.TLabel",
            font=("Segoe UI", 19, "bold"),
        )
        self.session_title.pack(anchor="w", pady=(4, 12))

        code_card = ttk.Frame(controls, style="Card.TFrame", padding=12)
        code_card.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(code_card, text="ROOM CODE", style="Card.TLabel", foreground=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.room_code_label = ttk.Label(
            code_card,
            text="------",
            style="Card.TLabel",
            font=("Consolas", 22, "bold"),
        )
        self.room_code_label.pack(anchor="w", pady=(4, 0))

        self.name_label = ttk.Label(controls, text="", style="Panel.TLabel", foreground=MUTED)
        self.name_label.pack(anchor="w", pady=(0, 10))

        self.host_options = ttk.Frame(controls, style="Card.TFrame", padding=12)
        self.host_options.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            self.host_options,
            text="SHARING",
            style="Card.TLabel",
            foreground=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        tk.Checkbutton(
            self.host_options,
            text="Share my screen",
            variable=self.host_share_screen,
            command=self._update_host_share_state,
            bg=PANEL_2,
            activebackground=PANEL_2,
            fg=TEXT,
            activeforeground=TEXT,
            selectcolor=INPUT,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 4))
        tk.Checkbutton(
            self.host_options,
            text="Audio on",
            variable=self.host_audio_enabled,
            command=self._update_audio_state,
            bg=PANEL_2,
            activebackground=PANEL_2,
            fg=TEXT,
            activeforeground=TEXT,
            selectcolor=INPUT,
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X)
        ttk.Label(
            self.host_options,
            text="SOURCE",
            style="Card.TLabel",
            foreground=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(14, 5))
        self.source_combo = ttk.Combobox(
            self.host_options,
            textvariable=self.share_source,
            state="readonly",
            style="Source.TCombobox",
            font=("Segoe UI", 9),
        )
        self.source_combo.pack(fill=tk.X)
        self.source_combo.bind("<<ComboboxSelected>>", self._select_source)
        self._small_button(self.host_options, "REFRESH SOURCES", self.refresh_sources).pack(fill=tk.X, pady=(8, 0))
        self.audio_note = ttk.Label(
            self.host_options,
            text="Pick a window source to reduce capture work and keep viewers smoother.",
            style="Card.TLabel",
            foreground=MUTED,
            wraplength=250,
            font=("Segoe UI", 8),
        )
        self.audio_note.pack(anchor="w", pady=(10, 0))
        ttk.Label(
            self.host_options,
            textvariable=self.current_source_label,
            style="Card.TLabel",
            foreground=TEXT,
            wraplength=250,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(6, 0))

        self.start_button = self._button(controls, "START", self.start, primary=True)
        self.start_button.pack(fill=tk.X, pady=(0, 8))
        self.stop_button = self._button(controls, "STOP", self.stop)
        self.stop_button.configure(state=tk.DISABLED)
        self.stop_button.pack(fill=tk.X)

        self.fullscreen_button = self._button(controls, "FULLSCREEN", self.enter_fullscreen)
        self.fullscreen_button.pack(fill=tk.X, pady=(8, 0))

        self.back_button = self._button(controls, "BACK TO ROOMS", self.back_to_rooms)
        self.back_button.pack(fill=tk.X, pady=(8, 0))

        info = ttk.Frame(controls, style="Card.TFrame", padding=14)
        info.pack(fill=tk.X, pady=(14, 0))
        ttk.Label(info, text="LAN ADDRESS", style="Card.TLabel", foreground=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.lan_address_label = ttk.Label(
            info,
            text=f"{self._local_ip()}:{DEFAULT_PORT}",
            style="Card.TLabel",
            font=("Consolas", 14, "bold"),
        )
        self.lan_address_label.pack(anchor="w", pady=(6, 0))
        ttk.Label(info, text="Give this to viewers on your Wi-Fi.", style="Card.TLabel", foreground=MUTED, wraplength=245).pack(anchor="w", pady=(10, 0))

    def continue_to_rooms(self):
        name = self.user_name.get().strip()
        if not name:
            messagebox.showerror("Name required", "Enter your name before continuing.")
            return
        self.status.set(f"Signed in as {name}")
        self._show_page("room")

    def create_room(self):
        code = secrets.token_hex(3).upper()
        self.room_code.set(code)
        self.mode.set("share")
        self.host_share_screen.set(False)
        self.share_enabled = False
        self.host_audio_enabled.set(False)
        self.refresh_sources()
        self.lan_address_label.configure(text=f"{self._local_ip()}:{self.port.get()}")
        self.session_title.configure(text="Share Screen")
        self.room_code_label.configure(text=code)
        self.name_label.configure(text=f"Host: {self.user_name.get().strip()}")
        self.host_options.pack(fill=tk.X, pady=(0, 12))
        self.display.configure(text=self._host_waiting_text(), image="")
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self._show_page("session")

    def join_room(self):
        code = self.join_code.get().strip().upper()
        host = self.host.get().strip()
        self._apply_host_port()
        if not host or not code:
            messagebox.showerror("Room required", "Enter the host address and room code.")
            return
        self.room_code.set(code)
        self.mode.set("watch")
        self.session_title.configure(text="Watch Screen")
        self.room_code_label.configure(text=code)
        self.name_label.configure(text=f"Viewer: {self.user_name.get().strip()}")
        self.host_options.pack_forget()
        self.display.configure(text="Click Start to join the room.", image="")
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self._show_page("session")

    def back_to_rooms(self):
        self.stop()
        self.photo = None
        self.display.configure(text="No active stream", image="")
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

        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)

        if self.mode.get() == "share":
            self.client = None
            self.share_enabled = self.host_share_screen.get()
            self._focus_selected_source()
            if self.share_enabled:
                self._start_capture_thread()
            else:
                with self.packet_lock:
                    self.latest_frame = self._waiting_frame()
                    self.latest_packet = self.waiting_packet
                    self.latest_packet_version += 1
            self.server = ShareServer(port, self.room_code.get(), self._host_packet, self._set_status)
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
            )
            self.client.start()

    def _apply_host_port(self):
        host = self.host.get().strip()
        if host.count(":") == 1:
            address, port = host.rsplit(":", 1)
            if address and port.isdigit():
                self.host.set(address)
                self.port.set(int(port))

    def stop(self):
        self._stop_capture_thread()
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
        self._set_status("Stopped")

    def _set_status(self, value):
        self.after(0, self.status.set, value)

    def _schedule_preview(self):
        try:
            if self.page != "session":
                return
            if self.client:
                self._show_viewer_frame()
            elif self.mode.get() == "share" and self.server and self.host_share_screen.get():
                self._show_host_preview()
            elif self.mode.get() == "share":
                with self.packet_lock:
                    self.latest_frame = self._waiting_frame()
                    self.latest_packet = self.waiting_packet
                if self.display:
                    self.display.configure(text=self._host_waiting_text(), image="")
        finally:
            self.after(CAPTURE_INTERVAL_MS, self._schedule_preview)

    def _host_frame(self):
        if self.host_share_screen.get():
            return self.latest_frame
        return self._waiting_frame()

    def _host_packet(self):
        with self.packet_lock:
            if self.share_enabled:
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
            try:
                frame = self._capture_selected_source()
                packet = make_packet(*frame)
                with self.packet_lock:
                    self.latest_frame = frame
                    self.latest_packet = packet
                    self.latest_packet_version += 1
                self.capture_error = None
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
        if source == "Entire screen":
            return self.capture.capture()
        hwnd = self.window_sources.get(source)
        if not hwnd:
            raise RuntimeError("Selected source is no longer available")
        return self.capture.capture_window(hwnd)

    def _waiting_frame(self):
        return WAITING_WIDTH, WAITING_HEIGHT, WAITING_FRAME

    def _host_waiting_text(self):
        if self.host_share_screen.get():
            if self.server:
                return "Sharing this screen."
            return "Click Start to share this screen."
        return "Room is open. Screen sharing is off."

    def _update_host_share_state(self):
        if self.mode.get() != "share" or not self.display:
            return
        self.photo = None
        self.share_enabled = self.host_share_screen.get()
        self.display.configure(text=self._host_waiting_text(), image="")
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

    def _update_audio_state(self):
        if self.host_audio_enabled.get():
            self._set_status("Audio is on in settings; video stream is active only")
        else:
            self._set_status("Audio off")

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
            if source == "Entire screen":
                frame = self.capture.capture()
            else:
                hwnd = self.window_sources.get(source)
                if not hwnd:
                    raise RuntimeError("Selected source is no longer available")
                frame = self.capture.capture_window(hwnd)
            self.latest_frame = frame
            self.latest_packet = make_packet(*frame)
            self.latest_packet_version += 1
            now = time.monotonic()
            if now - self.last_preview_time >= PREVIEW_INTERVAL_MS / 1000:
                self.last_preview_time = now
                self._render_frame(*frame)
        except Exception as exc:
            self.display.configure(text=f"Screen capture failed: {exc}", image="")

    def _show_host_preview(self):
        if self.capture_error:
            self.display.configure(text=f"Screen capture failed: {self.capture_error}", image="")
            return
        now = time.monotonic()
        if now - self.last_preview_time < PREVIEW_INTERVAL_MS / 1000:
            return
        with self.packet_lock:
            frame = self.latest_frame
        if frame is None:
            self.display.configure(text="Preparing stream...", image="")
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
            width, height, frame_data = decode_packet_payload(width, height, frame_data)
        self.last_viewer_render_time = now
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
        self.fullscreen_window.focus_force()

    def exit_fullscreen(self):
        if not self.fullscreen_active:
            return
        if hasattr(self, "fullscreen_image_id"):
            del self.fullscreen_image_id
        if hasattr(self, "fullscreen_text_id"):
            del self.fullscreen_text_id
        self.fullscreen_photo = None
        if self.fullscreen_window:
            self.fullscreen_window.destroy()
        self.fullscreen_window = None
        self.fullscreen_display = None
        self.fullscreen_rendering = False
        self.fullscreen_active = False
        self._rerender_current_frame()

    def destroy(self):
        self.exit_fullscreen()
        self.stop()
        super().destroy()


if __name__ == "__main__":
    app = ScreenShareApp()
    app.mainloop()
