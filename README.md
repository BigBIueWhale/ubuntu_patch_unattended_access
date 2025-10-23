# Unattended Remote Desktop on Ubuntu 24.04 (Wayland, GNOME) — Portal Auto-Approve Patch

This README is intentionally strict and opinionated. Follow it **exactly** on **Ubuntu 24.04 LTS (Noble)**, **GNOME on Wayland**.

> **What this does:** Applies a minimal patch to GNOME’s `xdg-desktop-portal-gnome` (tag **49.0**) so that:
>
> * **Remote-Desktop** prompt is **auto-approved** with **“Allow Remote Interaction”** enabled (no dialog).
> * **Screencast** auto-selects the **first monitor** when there’s no valid restore data (no chooser).
>
> Use only on machines you own/administer. This bypasses a security prompt by design.

---

## 0) Preconditions — Verify Your System **Now**

Run each command and confirm the expected output **before** you continue.

```bash
# Ubuntu release should be 24.04.*
lsb_release -ds
# EXPECTS: Ubuntu 24.04 LTS ...

# You must be on Wayland (not Xorg)
echo "$XDG_SESSION_TYPE"
# EXPECTS: wayland

# GNOME Shell should be 46.x on Noble
gnome-shell --version
# EXPECTS: GNOME Shell 46.x

# Portal packages present (stock)
dpkg -s xdg-desktop-portal | grep '^Version'
dpkg -s xdg-desktop-portal-gnome | grep '^Version'
# EXPECTS: versions installed (any Noble GA/updates are OK)
```

If these do not match, **stop** and align your environment (Ubuntu 24.04 LTS, GNOME Wayland).

---

## 1) Verify You’re Using the **GNOME** Portal Backend (Required)

You must be running the **GNOME** implementation of the XDG portal backend on your session; otherwise this patch won’t take effect.

```bash
# A. You’re on GNOME Wayland (quick sanity)
echo "$XDG_SESSION_TYPE"    # should be: wayland
echo "$XDG_CURRENT_DESKTOP" # should include: GNOME (e.g., GNOME or GNOME:GNOME)

# B. The GNOME portal backend service is actually running
systemctl --user status xdg-desktop-portal-gnome.service --no-pager
# EXPECTS: Active: active (running)

# C. D-Bus: the GNOME backend owns the impl name
busctl --user list | grep -E 'org\.freedesktop\.impl\.portal\.desktop\.(gnome|kde|wlr)'
# EXPECTS: org.freedesktop.impl.portal.desktop.gnome (present)

# Optional: show who owns it
busctl --user status org.freedesktop.impl.portal.desktop.gnome | sed -n '1,15p'

# D. xdg-desktop-portal sees the GNOME backend
journalctl --user -u xdg-desktop-portal.service -b --no-pager | grep -i -E 'backend|gnome|portal'
# EXPECTS: lines indicating it picked up the GNOME backend

# E. No override forcing another backend
test -f ~/.config/xdg-desktop-portal/portals.conf && cat ~/.config/xdg-desktop-portal/portals.conf || echo "No user override file"
# EXPECTS: "No user override file" (or if file exists, it should not redirect RemoteDesktop/Screencast away from gnome)
```

If A/B/C don’t show GNOME (or an override is present), fix that first or this patch won’t apply.

---

## 2) Install Build Tooling & Headers

Install a modern toolchain and the GNOME dev dependencies.

```bash
sudo apt update

# Tooling
sudo apt install -y git build-essential ninja-build meson gettext libtool pkg-config \
                    gobject-introspection libglib2.0-dev

# Pull Ubuntu’s build-deps for the GNOME portal backend
sudo apt build-dep -y xdg-desktop-portal-gnome
```

> If `apt build-dep` complains about missing source repositories, enable `deb-src` lines in `/etc/apt/sources.list`, then `sudo apt update` and retry.

---

## 3) Fetch the Sources (lock to **49.0**)

```bash
mkdir -p ~/src && cd ~/src
git clone https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git
cd xdg-desktop-portal-gnome
git fetch --tags
git checkout 49.0
```

You should now be at tag **49.0**. Confirm:

```bash
git describe --tags --exact-match
# EXPECTS: 49.0
```

---

## 4) Apply the Patch (Auto-Approve + First-Monitor)

Save the patcher script file as `patch_portal_autoapprove.py` at the **repository root** (same directory that contains `meson.build` and `src/`).

> You should have received `patch_portal_autoapprove.py` alongside this README. If not, paste the script as provided in your instructions.

Run it:

```bash
python3 ./patch_portal_autoapprove.py "$(pwd)"
```

**Expected output:**

* Confirms both target files were found and match **49.0** structure.
* Creates backups:

  * `src/remotedesktopdialog.c.bak`
  * `src/screencast.c.bak`
* Prints `[OK] Patched ...` for each target, or `[SKIP] ... already patched`.

If you see **any** `[ERROR]` about sentinels/layout, you are **not** on the expected code. **Do not proceed**; open the files and patch manually or ensure you’re on tag **49.0**.

---

## 5) Build

```bash
# From repo root
meson setup build --prefix=/usr --buildtype=release
ninja -C build
```

If meson complains about missing deps, revisit **Step 2**.

---

## 6) Safely Install Your Custom Build

Stop the per-user portal services, install to `/usr`, then restart.

```bash
# Stop portal services in your user session
systemctl --user stop xdg-desktop-portal-gnome.service xdg-desktop-portal.service

# Install
sudo ninja -C build install

# Reload & restart in your user session
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service

# Follow logs to confirm a clean start
journalctl --user -u xdg-desktop-portal-gnome -u xdg-desktop-portal -b --no-pager
```

You should see both services start cleanly. If not, capture the logs and fix any missing runtime pieces.

---

## 7) What Exactly Changed (Code-Level Summary)

* **`src/remotedesktopdialog.c` (49.0)**

  * In `remote_desktop_dialog_new(...)` the patch:

    * Programmatically **enables** the “Allow Remote Interaction” switch.
    * Pretends a source is selected so “Share” is allowed.
    * **Calls** the dialog’s accept handler as if the user clicked **Share** immediately.

* **`src/screencast.c` (49.0)**

  * Adds `start_first_monitor(ScreenCastSession*)`:

    * Grabs the **first** logical monitor, creates a single stream, and calls `start_session(...)`.
  * In `handle_start(...)`:

    * If `restore_stream_from_data(...)` fails, tries **unattended first-monitor start**;
    * only if that fails does it open the original chooser dialog.

This matches the goal: **short-circuit Remote-Desktop** approval and **auto-select the first output** for Screencast.

---

## 8) Test the Result

From a fresh Wayland login:

```bash
# Watch the portals
journalctl --user -fu xdg-desktop-portal-gnome -u xdg-desktop-portal
```

Trigger a Remote-Desktop or Screencast request (e.g., **RustDesk** server session). You should **not** see the GNOME prompt; the session should start with **remote input allowed** and **the first monitor** shared.

---

## 9) Rollback (Return to Stock Behavior)

**Option A — Reinstall the distro package**

```bash
sudo apt install --reinstall xdg-desktop-portal-gnome
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Option B — Restore backups and rebuild**

```bash
cd ~/src/xdg-desktop-portal-gnome
cp src/remotedesktopdialog.c.bak src/remotedesktopdialog.c
cp src/screencast.c.bak src/screencast.c
meson setup build --wipe --prefix=/usr --buildtype=release
ninja -C build
sudo ninja -C build install
systemctl --user daemon-reload
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

---

## 10) Notes on “Persistence” vs. Your Patch

Wayland’s Remote-Desktop/Screencast portals use **single-use** restore tokens. Apps must rotate them **every** successful start. This patch **bypasses** the user-facing consent path and **auto-shares** the first monitor, so typical “token got stale → prompt appears” scenarios won’t block unattended operation.
However, if an app **never** reaches the portal start flow (its own bug), the portal can’t help.

---

## 11) Security & Responsibility

This patch removes an explicit consent UI that GNOME ships intentionally. Apply it **only** on systems you control, where unattended access is a conscious, documented policy. Keep systems physically secure, and restrict who can run remote-desktop clients on them.

---

## 12) Quick Reference

* Upstream project: [https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome](https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome)
* Tag used: **49.0**
* Files changed:

  * `src/remotedesktopdialog.c`
  * `src/screencast.c`

---

### Troubleshooting Checklist

* **Service mismatch:** Make sure the **GNOME** backend is active (see Section 1).
* **Wrong source tree:** `git status` clean, `git describe --tags` shows **49.0**.
* **Missing deps:** Re-run Step 2 and `meson setup` from scratch (`--wipe` if needed).
* **Portal logs:** `journalctl --user -u xdg-desktop-portal-gnome -u xdg-desktop-portal -b --no-pager`
* **App still prompts:** Confirm you’re not on KDE/wlr backend; confirm Screencast/Remote-Desktop requests actually hit the portal (you’ll see log lines).
