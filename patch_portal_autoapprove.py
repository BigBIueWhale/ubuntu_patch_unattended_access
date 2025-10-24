#!/usr/bin/env python3
"""
patch_portal_autoapprove.py

Strict, mapped-aware + idle-fallback patcher for GNOME's xdg-desktop-portal-gnome
to allow truly unattended Remote Desktop consent + first-monitor auto-selection.

Guarantees:
  • The consent dialog is auto-accepted even if the monitor grid hasn't populated.
  • Works when the window is mapped (notify::mapped) AND also via an idle fallback.

Targets (validated against tags 46.2 and 49.0):
  - src/remotedesktopdialog.c
  - src/screencastwidget.c

If anything does not match the expected layout, this script fails loudly and prints
canonical upstream links to compare.

Usage:
  python3 patch_portal_autoapprove.py /absolute/path/to/xdg-desktop-portal-gnome

This script:
  - Checks for required files
  - Validates "sentinel" structures
  - REFUSES TO RUN if any *.bak already exists (to protect prior backups)
  - Writes *.bak once, then patches the files
  - Prints a short diff-like preview of the touched regions
"""

import sys, os, re, textwrap, shutil, subprocess, difflib

REQUIRED_FILES = {
    "remotedesktopdialog.c": "src/remotedesktopdialog.c",
    "screencastwidget.c":    "src/screencastwidget.c",
}

# --- Sentinels for known-good layouts (46.2 / 49.0). ---
SENTINELS = {
    "remotedesktopdialog.c": [
        r"^G_DEFINE_TYPE\s*\(\s*RemoteDesktopDialog\s*,\s*remote_desktop_dialog\s*,\s*ADW_TYPE_WINDOW\s*\)",
        r"^static void\s+button_clicked\s*\(\s*GtkWidget\s*\*button,\s*.*?RemoteDesktopDialog\s*\*dialog\)",
        r"^static void\s+update_button_sensitivity\s*\(\s*RemoteDesktopDialog\s*\*dialog\)",
        r"^RemoteDesktopDialog\s*\*\s*remote_desktop_dialog_new\s*\(",
        r"^static void\s+remote_desktop_dialog_init\s*\(\s*RemoteDesktopDialog\s*\*dialog\)",
    ],
    "screencastwidget.c": [
        r"^G_DEFINE_TYPE\s*\(\s*ScreenCastWidget\s*,\s*screen_cast_widget\s*,\s*GTK_TYPE_BOX\s*\)",
        r"^static void\s+update_monitor_container\s*\(\s*ScreenCastWidget\s*\*widget\)",
        r"^static void\s+screen_cast_widget_init\s*\(\s*ScreenCastWidget\s*\*widget\)",
    ],
}

HELP_LINKS = {
    "remotedesktopdialog.c": [
        "https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/blob/46.2/src/remotedesktopdialog.c",
        "https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/blob/49.0/src/remotedesktopdialog.c",
    ],
    "screencastwidget.c": [
        "https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/blob/46.2/src/screencastwidget.c",
        "https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/blob/49.0/src/screencastwidget.c",
    ],
}

# Tags for injected blocks
AUTO_HELPERS_TAG = "OPINIONATED_PATCH_AUTO_SHARE_HELPERS"
MAPPED_SCHEDULE_TAG = "OPINIONATED_PATCH_SCHEDULE_ON_MAPPED"
AUTO_FIRST_MONITOR_TAG = "OPINIONATED_PATCH_AUTO_SELECT_FIRST_MONITOR"

def die(msg, filekey=None):
    print(f"\n[ERROR] {msg}\n", file=sys.stderr)
    if filekey and filekey in HELP_LINKS:
        print("Compare against the upstream layout here:", file=sys.stderr)
        for url in HELP_LINKS[filekey]:
            print(f"  - {url}", file=sys.stderr)
    print("\nThis script is intentionally strict.\n"
          "• Confirm you checked out a supported tag (e.g., 46.2 / 49.0).\n"
          "• If you’re on a different version/layout, adapt the patch manually.\n", file=sys.stderr)
    sys.exit(1)

def detect_tag(root):
    """Best-effort: identify checked-out tag or SHA for friendlier logs."""
    try:
        p = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5
        )
        if p.returncode == 0:
            return p.stdout.strip()
        p2 = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5
        )
        if p2.returncode == 0:
            return p2.stdout.strip()
    except Exception:
        pass
    return None

def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_with_backup(path, new_text):
    backup = path + ".bak"
    if os.path.exists(backup):
        die(f"Backup already exists: {backup}\n"
            "To protect your previous backup, this run aborts.\n"
            "Move/rename the existing .bak and re-run.", None)
    shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)

def assert_sentinels(path, content, patterns, filekey):
    for pat in patterns:
        if not re.search(pat, content, flags=re.M | re.S):
            die(f"Sentinel not found in {path}:\n  pattern: {pat}\n"
                "File does not match the expected layout for supported tags (46.2/49.0).",
                filekey=filekey)

# -------------------------- remotedesktopdialog.c -----------------------------

def _insert_remotedesktop_helpers(text):
    """Insert helpers BEFORE remote_desktop_dialog_init()."""
    if AUTO_HELPERS_TAG in text:
        return text

    init_decl = re.search(
        r"\n(static\s+void\s+remote_desktop_dialog_init\s*\(\s*RemoteDesktopDialog\s*\*dialog\s*\)\s*\{)",
        text
    )
    if not init_decl:
        die("Could not find remote_desktop_dialog_init() to anchor helper insertion.", "remotedesktopdialog.c")

    # NEVER mode: force selection TRUE + mapped-aware + idle fallback (idle added later)
    helpers = textwrap.dedent(f"""
        /* === {AUTO_HELPERS_TAG} ===
         * Auto-Share: ensure selection is considered present, enable "Allow Remote Interaction",
         * refresh sensitivity, then click Share. This is used by both the mapped hook and idle fallback.
         */
        static gboolean
        auto_share_response (RemoteDesktopDialog *dialog)
        {{
          /* Treat Screencast as selected to guarantee Share is permitted. */
          dialog->is_screen_cast_sources_selected = TRUE;

          /* Turn ON the permission toggle so Share becomes allowed. */
          adw_switch_row_set_active (dialog->allow_remote_interaction_switch, TRUE);

          /* Ensure accept button sensitivity re-evaluates */
          update_button_sensitivity (dialog);

          /* Programmatically press the bright orange "Share" button. */
          g_signal_emit_by_name (dialog->accept_button, "clicked");

          return G_SOURCE_REMOVE; /* run once */
        }}

        static void
        on_dialog_notify_mapped (GObject *obj, GParamSpec *pspec, gpointer user_data)
        {{
          GtkWidget *w = GTK_WIDGET (obj);
          if (!gtk_widget_get_mapped (w))
            return;

          /* Run only once */
          g_signal_handlers_disconnect_by_func (obj, G_CALLBACK (on_dialog_notify_mapped), user_data);

          /* Defer slightly so external code has its "done" handlers connected. */
          g_timeout_add (120, (GSourceFunc) auto_share_response, user_data);
        }}
        /* === /{AUTO_HELPERS_TAG} === */
    """).strip("\n") + "\n\n"

    insert_at = init_decl.start(1)
    return text[:insert_at] + helpers + text[insert_at:]

def patch_remotedesktopdialog_c(text):
    """
    1) Insert helpers above remote_desktop_dialog_init().
    2) In remote_desktop_dialog_init(), right after gtk_widget_init_template(GTK_WIDGET(dialog));
       install BOTH:
         - mapped-aware notify::mapped hook -> on_dialog_notify_mapped
         - idle fallback -> g_idle_add(auto_share_response, dialog)
    """
    text = _insert_remotedesktop_helpers(text)

    func_re = r"(static\s+void\s+remote_desktop_dialog_init\s*\(\s*RemoteDesktopDialog\s*\*dialog\s*\)\s*\{\s*)(.*?)(\n\}\s*)"
    m = re.search(func_re, text, flags=re.S)
    if not m:
        die("Could not locate remote_desktop_dialog_init() body for patching.", "remotedesktopdialog.c")

    prefix, body, suffix = m.group(1), m.group(2), m.group(3)

    anchor_re = r"gtk_widget_init_template\s*\(\s*GTK_WIDGET\s*\(\s*dialog\s*\)\s*\)\s*;"
    if not re.search(anchor_re, body):
        die("remote_desktop_dialog_init(): expected gtk_widget_init_template(GTK_WIDGET(dialog)); not found.",
            "remotedesktopdialog.c")

    if (MAPPED_SCHEDULE_TAG in body) or ("notify::mapped" in body and "on_dialog_notify_mapped" in body):
        # If previous version exists but without idle fallback, add idle fallback if missing.
        if "g_idle_add" not in body:
            body_new = re.sub(anchor_re, lambda m: m.group(0) + "\n\n  g_idle_add ((GSourceFunc) auto_share_response, dialog);\n", body, count=1)
            return text[:m.start(2)] + body_new + text[m.end(2):]
        return text

    inject = textwrap.dedent(f"""
        /* === {MAPPED_SCHEDULE_TAG} ===
         * Schedule auto-share after the dialog is mapped, and also schedule an idle fallback.
         */
        g_signal_connect_after (dialog,
                                "notify::mapped",
                                G_CALLBACK (on_dialog_notify_mapped),
                                dialog);
        /* Idle fallback: if mapped notify never arrives, still auto-approve */
        g_idle_add ((GSourceFunc) auto_share_response, dialog);
        /* === /{MAPPED_SCHEDULE_TAG} === */
    """).strip("\n")

    body_new = re.sub(anchor_re, lambda m: m.group(0) + "\n\n  " + inject + "\n", body, count=1)
    return text[:m.start(2)] + body_new + text[m.end(2):]

# --------------------------- screencastwidget.c --------------------------------

def patch_screencastwidget_c(text):
    """
    In update_monitor_container(ScreenCastWidget *widget):
      - Replace the inline 'singular' CSS if/else block with:
          int child_count = ...;
          singular CSS toggle;
          auto-select first monitor if (child_count > 0 && !widget->allow_multiple).
    """
    func_re = r"(static\s+void\s+update_monitor_container\s*\(\s*ScreenCastWidget\s*\*widget\s*\)\s*\{\s*)(.*?)(\n\}\s*)"
    m = re.search(func_re, text, flags=re.S)
    if not m:
        die("Could not locate update_monitor_container() body for patching in screencastwidget.c.",
            "screencastwidget.c")

    body = m.group(2)
    if AUTO_FIRST_MONITOR_TAG in body:
        return text  # already patched

    child_count_if_re = (
        r"if\s*\(\s*screen_cast_geometry_container_get_child_count\s*\(\s*SCREEN_CAST_GEOMETRY_CONTAINER\s*\(\s*monitor_container\s*\)\s*\)\s*==\s*1\s*\)\s*\{\s*"
        r"gtk_widget_add_css_class\s*\(\s*monitor_container\s*,\s*\"singular\"\s*\)\s*;\s*"
        r"\}\s*else\s*\{\s*"
        r"gtk_widget_remove_css_class\s*\(\s*monitor_container\s*,\s*\"singular\"\s*\)\s*;\s*"
        r"\}"
    )

    replacement = textwrap.dedent(f"""
        int child_count = screen_cast_geometry_container_get_child_count (SCREEN_CAST_GEOMETRY_CONTAINER (monitor_container));
        if (child_count == 1)
          gtk_widget_add_css_class (monitor_container, "singular");
        else
          gtk_widget_remove_css_class (monitor_container, "singular");

        /* === {AUTO_FIRST_MONITOR_TAG} ===
         * For truly unattended use: auto-select the first monitor when present
         * and single-selection is enforced.
         */
        if (child_count > 0 && !widget->allow_multiple)
          {{
            GtkWidget *first_button = gtk_widget_get_first_child (monitor_container);
            if (first_button)
              gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (first_button), TRUE);
          }}
        /* === /{AUTO_FIRST_MONITOR_TAG} === */
    """).strip("\n")

    if not re.search(child_count_if_re, body, flags=re.S):
        die(
            "update_monitor_container(): expected inline 'singular' CSS toggle block not found.\n"
            "This patch relies on the 46.2/49.0 structure that toggles 'singular' based on\n"
            "screen_cast_geometry_container_get_child_count(...).",
            "screencastwidget.c"
        )

    new_body = re.sub(child_count_if_re, replacement, body, flags=re.S, count=1)
    return text[:m.start(2)] + new_body + text[m.end(2):]

# ------------------------------- utilities ------------------------------------

def preview_changed_region(before, after, label, max_lines=120):
    diff = list(difflib.unified_diff(
        before.splitlines(keepends=False),
        after.splitlines(keepends=False),
        fromfile=label + " (before)",
        tofile=label + " (after)",
        lineterm=""
    ))
    if not diff:
        print(f"[SKIP] {label}: no changes")
        return
    print(f"[DIFF] {label}: showing up to {max_lines} lines of diff:")
    for i, line in enumerate(diff):
        if i >= max_lines:
            print("  ... (diff truncated)")
            break
        print(line)

# ----------------------------------- main -------------------------------------

def main():
    if len(sys.argv) != 2:
        die("Provide the path to your local checkout (e.g., 46.2/49.0):\n"
            "  python3 patch_portal_autoapprove.py /path/to/xdg-desktop-portal-gnome")

    root = os.path.abspath(sys.argv[1])

    detected = detect_tag(root)
    if detected:
        print(f"[info] Detected checkout: {detected}")

    # Ensure files exist
    paths = {}
    for short, rel in REQUIRED_FILES.items():
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die(f"Missing required file: {p}\n"
                "You did not point me at a supported source tree (or tree is incomplete).",
                short)
        paths[short] = p

    # REFUSE to proceed if ANY .bak already exists
    existing_baks = [p + ".bak" for p in paths.values() if os.path.exists(p + ".bak")]
    if existing_baks:
        msg = ["Refusing to proceed because the following backups already exist:"]
        msg += [f"  - {b}" for b in existing_baks]
        msg.append("Move/rename these .bak files and re-run.")
        die("\n".join(msg), None)

    # Read + sentinel check
    texts = {}
    for short, p in paths.items():
        s = read(p)
        assert_sentinels(p, s, SENTINELS[short], short)
        texts[short] = s

    # Patch files
    r_before = texts["remotedesktopdialog.c"]
    r_after  = patch_remotedesktopdialog_c(r_before)

    s_before = texts["screencastwidget.c"]
    s_after  = patch_screencastwidget_c(s_before)

    # Write with backups (this also creates .bak)
    write_with_backup(paths["remotedesktopdialog.c"], r_after)
    print(f"[OK] Patched {paths['remotedesktopdialog.c']} (backup at {paths['remotedesktopdialog.c']}.bak)")
    preview_changed_region(r_before, r_after, os.path.basename(paths["remotedesktopdialog.c"]))

    write_with_backup(paths["screencastwidget.c"], s_after)
    print(f"[OK] Patched {paths['screencastwidget.c']} (backup at {paths['screencastwidget.c']}.bak)")
    preview_changed_region(s_before, s_after, os.path.basename(paths["screencastwidget.c"]))

    print("\nNext steps:\n"
          "  1) Build + install (per the project README)\n"
          "  2) Restart portal services (e.g., log out/in, or: "
          "systemctl --user restart xdg-desktop-portal{,-gnome})\n")

if __name__ == "__main__":
    main()
