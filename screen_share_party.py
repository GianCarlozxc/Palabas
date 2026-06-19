import ctypes
import ctypes.wintypes
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
from PIL import Image


FRAME_HEADER = struct.Struct("!III")
DEFAULT_PORT = 5050
STREAM_FPS = 24
CAPTURE_INTERVAL_MS = int(1000 / STREAM_FPS)
PREVIEW_INTERVAL_MS = 250
MAX_PREVIEW_WIDTH = 1920
MAX_PREVIEW_HEIGHT = 1080
WAITING_WIDTH = 1920
WAITING_HEIGHT = 1080
WAITING_FRAME = bytes([8, 8, 8]) * WAITING_WIDTH * WAITING_HEIGHT
BG = "#000000"
PANEL = "#101010"
PANEL_2 = "#181818"
SURFACE = "#2a2a2a"
INPUT = "#050505"
RED = "#e50914"
RED_HOVER = "#b20710"
TEXT = "#ffffff"
MUTED = "#b3b3b3"
BORDER = "#303030"


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
    payload = zlib.compress(rgb, level=1)
    return FRAME_HEADER.pack(width, height, len(payload)) + payload


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
                client.settimeout(None)
                client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
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
            hello = json.dumps({"room": self.room_code, "name": self.viewer_name}).encode("utf-8") + b"\n"
            self.sock.sendall(hello)
            self.sock.settimeout(None)
            self._status("Watching shared screen")
            while not self.stop_event.is_set():
                header = recv_exact(self.sock, FRAME_HEADER.size)
                width, height, payload_size = FRAME_HEADER.unpack(header)
                payload = recv_exact(self.sock, payload_size)
                rgb = zlib.decompress(payload)
                self._push_frame(width, height, rgb)
        except Exception as exc:
            if not self.stop_event.is_set():
                self._status(f"Viewer stopped: {exc}")

    def _push_frame(self, width, height, rgb):
        try:
            while True:
                self.frame_queue.get_nowait()
        except queue.Empty:
            pass
        self.frame_queue.put((width, height, rgb))


class ScreenShareApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Watch")
        self.geometry("1180x820")
        self.minsize(760, 560)

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
        self.waiting_packet = make_packet(WAITING_WIDTH, WAITING_HEIGHT, WAITING_FRAME)
        self.photo = None
        self.fullscreen_photo = None
        self.server = None
        self.client = None
        self.page = "name"
        self.viewer_frames = queue.Queue(maxsize=2)
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
        self.start_button = None
        self.stop_button = None

        self._build_ui()
        self.bind("<Escape>", lambda _event: self.exit_fullscreen())
        self._schedule_preview()

    def _build_ui(self):
        self.configure(bg=BG)
        style = ttk.Style(self)
        style.theme_use("clam")
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
            padding=(0, 6),
        )
        style.map("TRadiobutton", background=[("active", PANEL_2)], foreground=[("active", TEXT)])
        style.configure(
            "TCheckbutton",
            background=PANEL,
            foreground=TEXT,
            indicatorcolor=INPUT,
            padding=(0, 6),
        )
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])
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

        self.root = ttk.Frame(self, padding=20)
        self.root.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(self.root)
        header.pack(fill=tk.X, pady=(0, 18))
        tk.Label(
            header,
            text="WATCH",
            bg=BG,
            fg=RED,
            font=("Arial", 27, "bold"),
        ).pack(side=tk.LEFT)
        nav = tk.Frame(header, bg=BG)
        nav.pack(side=tk.LEFT, padx=(28, 0))
        for label in ("Home", "Rooms", "Share"):
            tk.Label(nav, text=label, bg=BG, fg=MUTED, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 18))
        tk.Label(
            header,
            textvariable=self.status,
            bg=BG,
            fg=MUTED,
            font=("Segoe UI", 9),
            padx=8,
            pady=4,
        ).pack(side=tk.RIGHT)

        self.content = ttk.Frame(self.root)
        self.content.pack(fill=tk.BOTH, expand=True)

        self._build_name_page()
        self._build_room_page()
        self._build_session_page()
        self._show_page("name")

    def _clear_content(self):
        for frame in self.frames.values():
            frame.pack_forget()

    def _show_page(self, name):
        self.page = name
        self._clear_content()
        self.frames[name].pack(fill=tk.BOTH, expand=True)

    def _button(self, parent, text, command, primary=False):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=RED if primary else SURFACE,
            activebackground=RED_HOVER if primary else SURFACE,
            fg="white" if primary else TEXT,
            activeforeground="white",
            disabledforeground="#64748b",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 11, "bold"),
            padx=14,
            pady=10,
        )

    def _small_button(self, parent, text, command):
        return tk.Button(
            parent,
            text=text,
            command=command,
            bg=SURFACE,
            activebackground=BORDER,
            fg=TEXT,
            activeforeground=TEXT,
            disabledforeground="#64748b",
            relief=tk.FLAT,
            bd=0,
            cursor="hand2",
            font=("Segoe UI", 9, "bold"),
            padx=10,
            pady=6,
        )

    def _build_name_page(self):
        page = ttk.Frame(self.content)
        self.frames["name"] = page

        card = ttk.Frame(page, style="Panel.TFrame", padding=42)
        card.place(relx=0.5, rely=0.5, anchor=tk.CENTER, width=520)

        ttk.Label(
            card,
            text="ENTER NAME",
            style="Panel.TLabel",
            foreground=RED,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            card,
            text="Who's watching?",
            style="Panel.TLabel",
            font=("Segoe UI", 30, "bold"),
        ).pack(anchor="w", pady=(6, 8))
        ttk.Label(
            card,
            text="This name appears to the room host when you connect.",
            style="Panel.TLabel",
            foreground=MUTED,
        ).pack(anchor="w", pady=(0, 20))
        ttk.Entry(card, textvariable=self.user_name, width=34).pack(fill=tk.X, pady=(0, 18))
        self._button(card, "CONTINUE", self.continue_to_rooms, primary=True).pack(fill=tk.X)

    def _build_room_page(self):
        page = ttk.Frame(self.content)
        self.frames["room"] = page

        shell = ttk.Frame(page)
        shell.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        ttk.Label(
            shell,
            text="Who's watching tonight?",
            font=("Segoe UI", 32, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 22))

        create = ttk.Frame(shell, style="Panel.TFrame", padding=28)
        create.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        ttk.Label(create, text="CREATE ROOM", style="Panel.TLabel", foreground=RED, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(create, text="Host a screen", style="Panel.TLabel", font=("Segoe UI", 23, "bold")).pack(anchor="w", pady=(6, 12))
        ttk.Label(create, text="Generate a private room code and control what viewers see.", style="Panel.TLabel", foreground=MUTED, wraplength=300).pack(anchor="w", pady=(0, 20))
        self._button(create, "CREATE ROOM", self.create_room, primary=True).pack(fill=tk.X)

        join = ttk.Frame(shell, style="Panel.TFrame", padding=28)
        join.grid(row=1, column=1, sticky="nsew", padx=(14, 0))
        ttk.Label(join, text="JOIN ROOM", style="Panel.TLabel", foreground=RED, font=("Segoe UI", 9, "bold")).pack(anchor="w")
        ttk.Label(join, text="Join the party", style="Panel.TLabel", font=("Segoe UI", 23, "bold")).pack(anchor="w", pady=(6, 12))
        ttk.Label(join, text="Host address", style="Panel.TLabel", foreground=MUTED).pack(anchor="w")
        ttk.Entry(join, textvariable=self.host, width=32).pack(fill=tk.X, pady=(6, 12))
        ttk.Label(join, text="Port", style="Panel.TLabel", foreground=MUTED).pack(anchor="w")
        ttk.Entry(join, textvariable=self.port, width=32).pack(fill=tk.X, pady=(6, 12))
        ttk.Label(join, text="Room code", style="Panel.TLabel", foreground=MUTED).pack(anchor="w")
        ttk.Entry(join, textvariable=self.join_code, width=32).pack(fill=tk.X, pady=(6, 18))
        self._button(join, "JOIN ROOM", self.join_room, primary=True).pack(fill=tk.X)
        shell.columnconfigure(0, weight=1, minsize=280)
        shell.columnconfigure(1, weight=1, minsize=280)

    def _build_session_page(self):
        body = ttk.Frame(self.content)
        self.frames["session"] = body
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=0, minsize=300)
        body.rowconfigure(0, weight=1)

        display_wrap = tk.Frame(body, bg="#202020", padx=1, pady=1)
        display_wrap.grid(row=0, column=0, sticky="nsew", padx=(0, 20))

        self.display = tk.Label(
            display_wrap,
            text="Share the screen. Watch together.",
            bg="#000000",
            fg=TEXT,
            anchor=tk.CENTER,
            compound=tk.CENTER,
            font=("Segoe UI", 32, "bold"),
        )
        self.display.pack(fill=tk.BOTH, expand=True)
        self.display.bind("<Double-Button-1>", lambda _event: self.enter_fullscreen())
        self.display.bind("<Configure>", lambda _event: self._rerender_current_frame())

        controls = ttk.Frame(body, style="Panel.TFrame", padding=18)
        controls.grid(row=0, column=1, sticky="ns")
        controls.configure(width=310)
        controls.grid_propagate(False)

        ttk.Label(
            controls,
            text="NOW PLAYING",
            style="Panel.TLabel",
            foreground=RED,
            font=("Segoe UI", 9, "bold"),
        ).pack(anchor="w")
        self.session_title = ttk.Label(
            controls,
            text="Room",
            style="Panel.TLabel",
            font=("Segoe UI", 20, "bold"),
        )
        self.session_title.pack(anchor="w", pady=(4, 14))

        code_card = ttk.Frame(controls, style="Card.TFrame", padding=12)
        code_card.pack(fill=tk.X, pady=(0, 14))
        ttk.Label(code_card, text="ROOM CODE", style="Card.TLabel", foreground=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        self.room_code_label = ttk.Label(
            code_card,
            text="------",
            style="Card.TLabel",
            font=("Consolas", 20, "bold"),
        )
        self.room_code_label.pack(anchor="w", pady=(4, 0))

        self.name_label = ttk.Label(controls, text="", style="Panel.TLabel", foreground=MUTED)
        self.name_label.pack(anchor="w", pady=(0, 12))

        self.host_options = ttk.Frame(controls, style="Card.TFrame", padding=12)
        self.host_options.pack(fill=tk.X, pady=(0, 12))
        ttk.Label(
            self.host_options,
            text="HOST OPTIONS",
            style="Card.TLabel",
            foreground=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(0, 6))
        ttk.Checkbutton(
            self.host_options,
            text="Share my screen",
            variable=self.host_share_screen,
            command=self._update_host_share_state,
        ).pack(anchor="w", pady=(0, 2))
        ttk.Checkbutton(
            self.host_options,
            text="Audio on",
            variable=self.host_audio_enabled,
            command=self._update_audio_state,
        ).pack(anchor="w")
        ttk.Label(
            self.host_options,
            text="Source",
            style="Card.TLabel",
            foreground=MUTED,
            font=("Segoe UI", 8, "bold"),
        ).pack(anchor="w", pady=(10, 4))
        source_frame = tk.Frame(self.host_options, bg=BORDER, padx=1, pady=1)
        source_frame.pack(fill=tk.X)
        self.source_list = tk.Listbox(
            source_frame,
            height=3,
            bg=INPUT,
            fg=TEXT,
            selectbackground=RED,
            selectforeground="white",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            exportselection=False,
            font=("Segoe UI", 9),
        )
        self.source_list.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.source_list.bind("<<ListboxSelect>>", self._select_source)
        source_scroll = tk.Scrollbar(source_frame, command=self.source_list.yview, bg=SURFACE, troughcolor=INPUT)
        source_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.source_list.configure(yscrollcommand=source_scroll.set)
        self._small_button(self.host_options, "REFRESH SOURCES", self.refresh_sources).pack(fill=tk.X, pady=(8, 0))
        self.audio_note = ttk.Label(
            self.host_options,
            text="Select Chrome or any app to share only that window. The app will minimize so it won't cover the source.",
            style="Card.TLabel",
            foreground=MUTED,
            wraplength=250,
            font=("Segoe UI", 8),
        )
        self.audio_note.pack(anchor="w", pady=(8, 0))
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
        info.pack(fill=tk.X, pady=(16, 0))
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
        self.display.configure(text="Share the screen. Watch together.", image="")
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
            time.sleep(1 / STREAM_FPS)

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
        self.source_list.delete(0, tk.END)
        for value in values:
            self.source_list.insert(tk.END, value)
        if self.share_source.get() not in values:
            self.share_source.set("Entire screen")
        self.selected_source_name = self.share_source.get()
        selected = values.index(self.share_source.get())
        self.source_list.selection_clear(0, tk.END)
        self.source_list.selection_set(selected)
        self.source_list.see(selected)
        self.current_source_label.set(f"Source: {self.share_source.get()}")

    def _select_source(self, _event=None):
        selection = self.source_list.curselection()
        if not selection:
            return
        self.share_source.set(self.source_list.get(selection[0]))
        self.selected_source_name = self.share_source.get()
        self.current_source_label.set(f"Source: {self.share_source.get()}")
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
        try:
            frame = self.viewer_frames.get_nowait()
        except queue.Empty:
            return
        self._render_frame(*frame)

    def _render_frame(self, width, height, rgb):
        self.current_rgb_frame = (width, height, rgb)
        
        if self.fullscreen_display:
            fs_width = max(1, self.fullscreen_display.winfo_width())
            fs_height = max(1, self.fullscreen_display.winfo_height())
            fs_w, fs_h, fs_rgb = scale_rgb_frame(width, height, rgb, fs_width, fs_height)
            
            old_fs_photo = self.fullscreen_photo
            self.fullscreen_photo = tk.PhotoImage(data=make_ppm(fs_w, fs_h, fs_rgb), format="PPM")
            
            if hasattr(self, "fullscreen_image_id") and self.fullscreen_display.winfo_exists():
                self.fullscreen_display.itemconfig(self.fullscreen_image_id, image=self.fullscreen_photo)
                self.fullscreen_display.coords(self.fullscreen_image_id, fs_width // 2, fs_height // 2)
            else:
                self.fullscreen_image_id = self.fullscreen_display.create_image(
                    fs_width // 2, fs_height // 2,
                    image=self.fullscreen_photo,
                    anchor=tk.CENTER
                )
        else:
            target_width = max(1, self.display.winfo_width())
            target_height = max(1, self.display.winfo_height())
            w, h, r = scale_rgb_frame(width, height, rgb, target_width, target_height)
            
            old_photo = self.photo
            self.photo = tk.PhotoImage(data=make_ppm(w, h, r), format="PPM")
            
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
        
        self.fullscreen_window.update_idletasks()
        fs_width = self.fullscreen_window.winfo_width()
        fs_height = self.fullscreen_window.winfo_height()
        
        if self.photo:
            self.fullscreen_image_id = self.fullscreen_display.create_image(
                fs_width // 2, fs_height // 2,
                image=self.photo,
                anchor=tk.CENTER
            )
        else:
            self.fullscreen_display.create_text(
                fs_width // 2, fs_height // 2,
                text=self.display.cget("text"),
                fill=TEXT,
                font=("Segoe UI", 32, "bold")
            )
        self.fullscreen_window.focus_force()

    def exit_fullscreen(self):
        if not self.fullscreen_active:
            return
        if hasattr(self, "fullscreen_image_id"):
            del self.fullscreen_image_id
        if self.fullscreen_window:
            self.fullscreen_window.destroy()
        self.fullscreen_window = None
        self.fullscreen_display = None
        self.fullscreen_active = False

    def destroy(self):
        self.exit_fullscreen()
        self.stop()
        super().destroy()


if __name__ == "__main__":
    app = ScreenShareApp()
    app.mainloop()
