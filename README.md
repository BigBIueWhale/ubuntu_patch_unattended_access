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

Below is the whole flow with **why** each step exists and **what you should see** if it worked.

```bash
# 1) Clone this repo and enter it — so everything stays project-local and reversible.
git clone https://github.com/BigBIueWhale/ubuntu_patch_unattended_access && cd ubuntu_patch_unattended_access
```

**Expect:** a new folder named `ubuntu_patch_unattended_access` and your shell prompt inside it.

```bash
# 2) Refresh package indices — guarantees the next installs see the latest package lists.
sudo apt update
```

**Expect:** “Hit/Get” lines and “Reading package lists… Done”. No errors.

```bash
# 3) Install toolchain and headers — these are needed for Meson/Ninja builds and GLib introspection.
sudo apt install -y git build-essential ninja-build meson gettext libtool pkg-config gobject-introspection libglib2.0-dev
```

**Expect:** Packages install or are reported as already the newest version.

```bash
# 4) Enable source repositories using the Deb822 method (Ubuntu 24.04 default).
#    Why: 'apt build-dep xdg-desktop-portal-gnome' needs access to *source* indices.
#    We patch the system-provided Deb822 file instead of touching /etc/apt/sources.list.
#    First, make a backup you can revert to in one command:
sudo cp /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources.bak
```

**Expect:** The command exits silently. If the file doesn’t exist, you’re on a non-standard setup — stop and check your APT configuration.

```bash
# 5) Add 'deb-src' to any 'Types: deb' stanzas in the Deb822 file — this turns on source indexes.
sudo sed -i 's/^\(Types:\s*deb\)\(\s\+\|\s*$\)/\1 deb-src\2/' /etc/apt/sources.list.d/ubuntu.sources
```

**Expect:** No output. The file now contains `Types: deb deb-src` for relevant stanzas.

```bash
# 6) Re-sync APT indices now that sources are enabled.
sudo apt update
```

**Expect:** You should see lines for `InRelease` and `Sources` being fetched. No errors.

```bash
# 7) Pull distro-provided build dependencies for xdg-desktop-portal-gnome — this smooths the Meson configure step.
sudo apt build-dep -y xdg-desktop-portal-gnome
```

**Expect:** Packages get installed. If you still see “You must put some 'deb-src' URIs…”, re-check Step 5 and confirm `Types: deb deb-src` exists in `/etc/apt/sources.list.d/ubuntu.sources`.

```bash
# 8) Fetch upstream sources locally inside the project and lock to the exact tag we patch against.
mkdir -p ./sources && cd ./sources && git clone https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git && cd xdg-desktop-portal-gnome && git fetch --tags && git checkout 49.0 && git describe --tags --exact-match
```

**Expect:** The last command prints exactly `49.0`. If it doesn’t, you’re not on the required tag — don’t continue.

```bash
# 9) Apply this repo’s patcher from the project root — validates layout and edits only what’s needed.
cd ../../ && python3 ./patch_portal_autoapprove.py ./sources/xdg-desktop-portal-gnome
```

**Expect:** Lines like `[OK] Patched …remotedesktopdialog.c` and `[OK] Patched …screencast.c`. If you see `[ERROR] Sentinel not found`, your sources are not exactly 49.0.

```bash
# 10) Build the project with Meson/Ninja — produces the patched binaries under ./sources/.../build.
cd ./sources/xdg-desktop-portal-gnome && meson setup build --prefix=/usr --buildtype=release && ninja -C build
```

**Expect:** Meson detects your system and finishes with “Build targets in project: …”. Ninja compiles without errors.

```bash
# 11) Install and restart user portal services — switches your session to the patched backend.
systemctl --user stop xdg-desktop-portal-gnome.service xdg-desktop-portal.service && sudo ninja -C build install && systemctl --user daemon-reload && systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Expect:** Both services come back as “active (running)”. See verification below if you want to double-check.

If the `git describe` check or the patcher fails, you’re not on **49.0**. Fix that before proceeding.

---

## 0) Preconditions — Verify Environment

Why: Ensures you’re on the intended distro and desktop so the portal backend and source tree match what we patch.

```bash
lsb_release -ds
```

**Expect:** `Ubuntu 24.04.* LTS`.

```bash
gnome-shell --version
```

**Expect:** `GNOME Shell 46.x`.

```bash
dpkg -s xdg-desktop-portal | grep '^Version'
```

**Expect:** A `1.18.x` (Noble-era) version string.

```bash
dpkg -s xdg-desktop-portal-gnome | grep '^Version'
```

**Expect:** A `49.x` series version string (distro package; we’ll build from tag 49.0 next).

Portal backend must be **GNOME**:

```bash
systemctl --user status xdg-desktop-portal-gnome.service --no-pager
```

**Expect:** `Active: active (running)`.

```bash
busctl --user list | grep -E 'org\.freedesktop\.impl\.portal\.desktop\.(gnome|kde|wlr)'
```

**Expect:** a line containing `org.freedesktop.impl.portal.desktop.gnome`. If you see KDE or wlr, switch to GNOME Wayland.

```bash
journalctl --user -u xdg-desktop-portal.service -b --no-pager | grep -i -E 'backend|gnome|portal'
```

**Expect:** Entries showing GNOME backend ownership.

```bash
test -f ~/.config/xdg-desktop-portal/portals.conf && cat ~/.config/xdg-desktop-portal/portals.conf || echo "No user override file"
```

**Expect:** Either no file (default) or a config that doesn’t force a different backend.

*If a different backend (KDE/wlr) is active, switch to GNOME Wayland and ensure the GNOME backend owns the name before continuing.*

---

## 1) Keep Everything Project-Local

Why: Keeping all work under the repo makes it simple to see what changed, revert it, and avoid surprising your system.

After following the steps below, your tree will look like:

```
ubuntu_patch_unattended_access/
├── patch_portal_autoapprove.py
├── README.md
├── remote_access_wayland_reality.html
└── sources/
    └── xdg-desktop-portal-gnome/   # upstream sources at tag 49.0
```

**Expect:** The `sources/xdg-desktop-portal-gnome` directory contains the GNOME Git clone fixed at tag `49.0`.

---

## 2) Install Toolchain & Headers (Ubuntu packages)

Why: Meson/Ninja build system, gettext/libtool/pkg-config for configure steps, GLib dev headers and introspection for the portal code.

```bash
sudo apt update
```

```bash
sudo apt install -y git build-essential ninja-build meson gettext libtool pkg-config gobject-introspection libglib2.0-dev
```

**Expect:** Successful installation. If Meson later complains about missing -dev packages, return here and re-run the install line.

### Enable Source Repositories (Deb822 method, Ubuntu 24.04)

Why: `apt build-dep` needs access to *source* package metadata. On Noble, APT uses Deb822 stanzas in `/etc/apt/sources.list.d/ubuntu.sources`, so uncommenting `/etc/apt/sources.list` won’t help. We patch the Deb822 file safely and reversibly.

```bash
sudo cp /etc/apt/sources.list.d/ubuntu.sources /etc/apt/sources.list.d/ubuntu.sources.bak
```

**Expect:** Silent success. This backup lets you revert in one command later.

```bash
sudo sed -i 's/^\(Types:\s*deb\)\(\s\+\|\s*$\)/\1 deb-src\2/' /etc/apt/sources.list.d/ubuntu.sources
```

**Expect:** No output. This changes `Types: deb` to `Types: deb deb-src` across relevant stanzas.

```bash
sudo apt update
```

**Expect:** You should now see `Sources` indices being fetched. If not, re-check the Deb822 file.

```bash
sudo apt build-dep -y xdg-desktop-portal-gnome
```

**Expect:** Build dependencies are installed. Any “You must put some 'deb-src' URIs …” means the Deb822 patch didn’t apply; revisit the sed step.

---

## 3) Fetch Upstream Sources (locked to **49.0**) — Project-Local

Why: The patcher uses **strict sentinels** that match *exactly* what’s in tag 49.0. Building from any other tag or distro branch may fail or produce different behavior.

```bash
mkdir -p ./sources && cd ./sources && git clone https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome.git && cd xdg-desktop-portal-gnome && git fetch --tags && git checkout 49.0 && git describe --tags --exact-match && cd ../../
```

**Expect:** The final `git describe` prints `49.0`. If it doesn’t, do not proceed.

---

## 4) Apply the Patch

Why: The script validates the source layout first, then does minimal, targeted edits, taking `.bak` backups so you can revert.

```bash
python3 ./patch_portal_autoapprove.py ./sources/xdg-desktop-portal-gnome
```

**Expect:**

* Verifies the **49.0** layout using strict sentinels.

* Creates backups:

  * `sources/xdg-desktop-portal-gnome/src/remotedesktopdialog.c.bak`
  * `sources/xdg-desktop-portal-gnome/src/screencast.c.bak`

* Prints `[OK] Patched ...` (or `[SKIP] ... already patched`)

If you get `[ERROR]` about sentinels/layout, the sources are not **exactly 49.0**. Fix the checkout before proceeding.

---

## 5) Build (Project-Local source tree)

Why: Produces patched binaries under a `build` directory, keeping the working tree clean.

```bash
cd ./sources/xdg-desktop-portal-gnome && meson setup build --prefix=/usr --buildtype=release && ninja -C build
```

**Expect:** Meson completes configuration; Ninja compiles without errors. If configuration fails (missing -dev packages), go back to Section 2 and then:

```bash
meson setup build --wipe --prefix=/usr --buildtype=release && ninja -C build
```

**Expect:** A clean reconfigure and successful build.

---

## 6) Install and Restart Portal Services

Why: We stop user services so the new backend can be installed and then brought up cleanly in your session.

```bash
systemctl --user stop xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Expect:** Both services stop without error.

```bash
sudo ninja -C build install
```

**Expect:** Files are installed under `/usr`. No errors.

```bash
systemctl --user daemon-reload
```

**Expect:** No output. This reloads user unit files.

```bash
systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Expect:** Services restart and become **active (running)**.

```bash
journalctl --user -u xdg-desktop-portal-gnome -u xdg-desktop-portal -b --no-pager
```

**Expect:** Recent logs show clean startup. Occasional “Failed to associate portal window with parent window” lines can be benign; what matters is both services are active and portal requests work.

*If a different desktop backend (KDE/wlr) is active for your session, the patch will not apply; ensure **GNOME** owns the backend name.*

---

## 7) What Exactly Changes (Code-Level Summary)

* **`src/remotedesktopdialog.c` (49.0)**

  * Inside `remote_desktop_dialog_new(...)`:

    * Forces **“Allow Remote Interaction”** ON,
    * Marks a source as “selected” so **Share** is permitted,
    * Immediately calls the dialog’s accept handler (**acts like Share was clicked**).

  **Result:** No consent dialog for Remote-Desktop; unattended approval.

* **`src/screencast.c` (49.0)**

  * Adds `start_first_monitor(ScreenCastSession*)` to select the **first logical monitor** and call `start_session(...)`.
  * In `handle_start(...)`: if `restore_stream_from_data(...)` fails, attempts unattended **first-monitor** start before falling back to the chooser.

  **Result:** When there is no valid restore token, Screencast auto-shares the first monitor without prompting.

---

## 8) Testing

Why: Validates behavior at runtime.

1. Start a Remote-Desktop/Screencast client that uses **xdg-desktop-portal** (e.g., RustDesk server integration).
2. Observe that no consent dialog appears.
3. The session should start with **remote input allowed** and the **first monitor** shared.

Optional live logs:

```bash
journalctl --user -fu xdg-desktop-portal-gnome -u xdg-desktop-portal
```

**Expect:** When a client starts a session, you’ll see portal activity without UI prompts.

---

## 9) Rollback (Return to Stock)

**Option A — Reinstall distro package**

Why: Restores the official package files.

```bash
sudo apt install --reinstall xdg-desktop-portal-gnome && systemctl --user daemon-reload && systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Expect:** Services restart; behavior returns to stock (with consent dialogs).

**Option B — Restore backups and reinstall your local build**

Why: Undo just your source edits while keeping your local build flow.

```bash
cd ./sources/xdg-desktop-portal-gnome && cp src/remotedesktopdialog.c.bak src/remotedesktopdialog.c && cp src/screencast.c.bak src/screencast.c && meson setup build --wipe --prefix=/usr --buildtype=release && ninja -C build && sudo ninja -C build install && systemctl --user daemon-reload && systemctl --user restart xdg-desktop-portal-gnome.service xdg-desktop-portal.service
```

**Expect:** Services restart; patched behavior is removed.

**Reverting the Deb822 change (optional)**

```bash
sudo mv /etc/apt/sources.list.d/ubuntu.sources.bak /etc/apt/sources.list.d/ubuntu.sources && sudo apt update
```

**Expect:** Source indices are no longer fetched; system returns to pre-change APT configuration.

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

* **Backend mismatch:** Ensure **GNOME** owns `org.freedesktop.impl.portal.desktop.gnome`.
* **Wrong sources:** `git -C ./sources/xdg-desktop-portal-gnome describe --tags` should print **49.0**.
* **Missing deps:** Re-run Section 2; then `meson setup build --wipe --prefix=/usr --buildtype=release && ninja -C build`.
* **Portal logs:** `journalctl --user -u xdg-desktop-portal-gnome -u xdg-desktop-portal -b --no-pager` to inspect startup and requests.
* **App still prompts:** Confirm the request goes through **xdg-desktop-portal** (visible in logs) and that the GNOME backend is in use (not KDE/wlr).
* **`apt build-dep` still complains about deb-src:** Re-open `/etc/apt/sources.list.d/ubuntu.sources`, confirm `Types: deb deb-src` appears, run `sudo apt update`, then retry `sudo apt build-dep -y xdg-desktop-portal-gnome`. If you changed mirrors, make sure the `URIs:` entries are valid for your region.
