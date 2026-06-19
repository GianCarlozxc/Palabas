# Watch Party Screen Share

A Netflix-style Python desktop GUI for sharing one screen to another computer on the same network.

## Run

Double-click `run_screen_share.bat`, or run:

```powershell
python screen_share_party.py
```

## Use

1. Enter your display name.
2. To host, click `Create Room`, choose whether `Share my screen` is enabled, pick `Entire screen` or a specific window from `Source`, then click `Start`. Screen sharing is off by default.
3. The `Audio on` toggle is available in the host controls, but this lightweight build currently streams video frames only.
4. Tell viewers the LAN address with port, like `192.168.1.20:5050`, and the room code shown in the app.
5. To watch, enter your display name, click `Join Room`, enter the host address with port or use the port field, enter the room code, then click `Start`.

Both computers need to be on the same network. If Windows Firewall asks, allow Python on private networks.

## Performance

The stream targets up to 55 FPS at 320x180. The app only sends newly captured frames, so actual unique FPS depends on how fast Windows screen capture runs on the host.

Use `Entire screen` to show everything. Select Chrome or another app in `Source` to stream only that window rectangle. The Watch Party control window minimizes when app-only sharing starts so it does not cover the selected app.
