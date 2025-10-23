# Unattended Remote Desktop on Ubuntu 24.04 (Wayland, GNOME) — Portal Auto-Approve Patch

This README is **strict and opinionated**. Follow it **exactly** on **Ubuntu 24.04 LTS (Noble)** with **GNOME on Wayland** and the **GNOME xdg-desktop-portal backend**.

> **What this does:** Applies a minimal patch to GNOME’s `xdg-desktop-portal-gnome` (tag **49.0**) so that:
>
> * **Remote-Desktop** is **auto-approved** with **“Allow Remote Interaction”** enabled (no consent dialog).
> * **Screencast** auto-selects the **first monitor** when there’s no valid restore data (no chooser).
>
> Use only on machines you own/administer. This intentionally bypasses a security prompt.

---

## Quick Start (TL;DR)

```bash
# 1) Clone this repo and enter it
git clone https://github.com/BigBIueWhale/ubuntu_patch_unattended_access
cd ubuntu_patch_unattended_access

# 2) Install build deps (Ubuntu 24.04)
sudo apt update
sudo apt install -y git build-essential ninja-build meson gettext libtool pkg-config \
  gobject-introspection libglib2.0-dev
# (Optional but recommended)
if ! grep -q '^deb-src ' /etc/apt/sources.list; then
  sudo sed -i 's/^#\s*deb-src/deb-src/' /etc/apt/sources.list
  sudo apt update
fi
sudo apt build-dep -y xdg-desktop-portal-gnome

# 3) Fetch upstream sources locally inside the project and lock to 49.0
mkdir -p ./sources && cd ./sources
git clone https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git
cd xdg-desktop-portal-gnome
git fetch --tags
git checkout 49.0
git describe --tags --exact-match   # should print: 49.0

# 4) Apply this repo’s patcher (from the project root)
cd ../../
python3 ./patch_portal_autoapprove.py ./sources/xdg-desktop-portal-gnome

# 5) Build and install
cd ./sources/xdg-desktop-portal-gnome
meson setup build --prefix=/usr --buildtype=release
ninja -C build
systemctl --user stop xdg-desktop-portal-gnome.service xdg-desktop-portal.service
sudo ninja -C build install
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

If the `git describe` check or the patcher fails, you’re not on **49.0**. Fix that before proceeding.

---

## 0) Preconditions — Verify Environment

Run and check:

```bash
lsb_release -ds                # should be: Ubuntu 24.04.* LTS
gnome-shell --version          # should be: GNOME Shell 46.x
dpkg -s xdg-desktop-portal | grep '^Version'
dpkg -s xdg-desktop-portal-gnome | grep '^Version'
```

Portal backend must be **GNOME**:

```bash
systemctl --user status xdg-desktop-portal-gnome.service --no-pager  # Active: active (running)
busctl --user list | grep -E 'org\.freedesktop\.impl\.portal\.desktop\.(gnome|kde|wlr)'  # should list ...desktop.gnome
journalctl --user -u xdg-desktop-portal.service -b --no-pager | grep -i -E 'backend|gnome|portal'
test -f ~/.config/xdg-desktop-portal/portals.conf && cat ~/.config/xdg-desktop-portal/portals.conf || echo "No user override file"
```

*If a different backend (KDE/wlr) is active, switch to GNOME Wayland and ensure the GNOME backend owns the name before continuing.*

---

## 1) Keep Everything Project-Local

All work happens **under this repo**. No use of `~/src`.

After following the steps below, your tree will look like:

```
ubuntu_patch_unattended_access/
├── patch_portal_autoapprove.py
├── README.md
├── remote_access_wayland_reality.html
└── sources/
    └── xdg-desktop-portal-gnome/   # upstream sources at tag 49.0
```

---

## 2) Install Toolchain & Headers (Ubuntu packages)

```bash
sudo apt update
sudo apt install -y \
  git build-essential ninja-build meson gettext libtool pkg-config \
  gobject-introspection libglib2.0-dev

# Recommended: use Ubuntu’s source build-deps to smooth meson configuration
if ! grep -q '^deb-src ' /etc/apt/sources.list; then
  sudo sed -i 's/^#\s*deb-src/deb-src/' /etc/apt/sources.list
  sudo apt update
fi
sudo apt build-dep -y xdg-desktop-portal-gnome
```

---

## 3) Fetch Upstream Sources (locked to **49.0**) — Project-Local

```bash
mkdir -p ./sources
cd ./sources
git clone https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git
cd xdg-desktop-portal-gnome
git fetch --tags
git checkout 49.0
git describe --tags --exact-match  # EXPECTS: 49.0
cd ../../
```

---

## 4) Apply the Patch

```bash
python3 ./patch_portal_autoapprove.py ./sources/xdg-desktop-portal-gnome
```

Expected patcher behavior:

* Verifies the **49.0** layout using strict sentinels.
* Creates backups:

  * `sources/xdg-desktop-portal-gnome/src/remotedesktopdialog.c.bak`
  * `sources/xdg-desktop-portal-gnome/src/screencast.c.bak`
* Prints `[OK] Patched ...` (or `[SKIP] ... already patched`)

If you get `[ERROR]` about sentinels/layout, the sources are not **exactly** 49.0. Fix the checkout before proceeding.

---

## 5) Build (Project-Local source tree)

```bash
cd ./sources/xdg-desktop-portal-gnome
meson setup build --prefix=/usr --buildtype=release
ninja -C build
```

If configuration fails, revisit **Section 2**, then re-run:

```bash
meson setup build --wipe --prefix=/usr --buildtype=release
ninja -C build
```

---

## 6) Install and Restart Portal Services

Installation is to `/usr` (system prefix) and we restart **user** services:

```bash
# Stop user portal services
systemctl --user stop xdg-desktop-portal-gnome.service xdg-desktop-portal.service

# Install patched backend
sudo ninja -C build install

# Reload and restart user services
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service

# Confirm a clean start
journalctl --user -u xdg-desktop-portal-gnome -u xdg-desktop-portal -b --no-pager
```

Notes:

* Seeing occasional “Failed to associate portal window with parent window” lines can be benign; what matters is both services are **active (running)** and portal requests succeed.
* If a different desktop backend (KDE/wlr) is active for your session, the patch will not apply; ensure **GNOME** owns the backend name.

---

## 7) What Exactly Changes (Code-Level Summary)

* **`src/remotedesktopdialog.c` (49.0)**

  * Inside `remote_desktop_dialog_new(...)`:

    * Forces **“Allow Remote Interaction”** ON,
    * Marks a source as “selected” so **Share** is permitted,
    * Immediately calls the dialog’s accept handler (**acts like Share was clicked**).

* **`src/screencast.c` (49.0)**

  * Adds `start_first_monitor(ScreenCastSession*)` to select the **first logical monitor** and call `start_session(...)`.
  * In `handle_start(...)`: if `restore_stream_from_data(...)` fails, attempts unattended **first-monitor** start before falling back to the chooser.

Result: **No consent dialog** for Remote-Desktop and **first monitor auto-share** for Screencast when no restore data exists.

---

## 8) Testing

With the patched services running:

1. Start a Remote-Desktop/Screencast client that uses **xdg-desktop-portal** (e.g., RustDesk server integration).
2. Observe that no consent dialog appears.
3. The session should start with **remote input allowed** and the **first monitor** shared.

Live logs (optional):

```bash
journalctl --user -fu xdg-desktop-portal-gnome -u xdg-desktop-portal
```

---

## 9) Rollback (Return to Stock)

**Option A — Reinstall distro package**

```bash
sudo apt install --reinstall xdg-desktop-portal-gnome
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Option B — Restore backups and reinstall your local build**

```bash
cd ./sources/xdg-desktop-portal-gnome
cp src/remotedesktopdialog.c.bak src/remotedesktopdialog.c
cp src/screencast.c.bak src/screencast.c
meson setup build --wipe --prefix=/usr --buildtype=release
ninja -C build
sudo ninja -C build install
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

---

## 10) Notes on Restore Tokens & Persistence

Wayland portals use **single-use** restore tokens. Well-behaved apps rotate them after each success. This patch **bypasses** the consent path and **auto-shares the first monitor**, preventing “stale token → prompt” loops from blocking unattended operation. If an app never reaches the portal start flow, that’s outside the portal’s scope.

---

## 11) Security & Responsibility

This removes a user consent step that GNOME ships intentionally. Apply only on systems you control with explicit authorization to allow unattended access. Keep machines physically secure and restrict who can start remote-desktop clients.

---

## 12) References

* Upstream project: [https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome](https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome)
* Tag used: **49.0**
* Files affected:

  * `src/remotedesktopdialog.c`
  * `src/screencast.c`

---

### Troubleshooting

* **Backend mismatch**: Ensure **GNOME** owns `org.freedesktop.impl.portal.desktop.gnome`.
* **Wrong sources**: `git -C ./sources/xdg-desktop-portal-gnome describe --tags` → **49.0**.
* **Missing deps**: Re-run Section 2; then `meson setup build --wipe ...`.
* **Portal logs**: `journalctl --user -u xdg-desktop-portal-gnome -u xdg-desktop-portal -b --no-pager`.
* **App still prompts**: Confirm the request goes through **xdg-desktop-portal** (visible in logs) and that the GNOME backend is in use (not KDE/wlr).
