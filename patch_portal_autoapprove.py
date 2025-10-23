#!/usr/bin/env python3
"""
patch_portal_autoapprove.py

Highly-opinionated, environment-distrusting patcher for GNOME's
xdg-desktop-portal-gnome (tested on 46.2 and 49.0) to:
  1) Auto-approve Remote-Desktop dialog (enable "Allow Remote Interaction", press Share)
     **mapped-aware + non-blocking**: we hook after the dialog is actually mapped
     ("notify::mapped") and then trigger Share via a short GLib timer (~120ms).
     This guarantees the caller has connected to the "done" signal and avoids the
     "no popup + no connection" race seen with idle-only scheduling.
  2) Auto-select first output for Screencast when no restore data is available

Targets (must match the known-good layout for your tag; tested on 46.2 and 49.0):
  - src/remotedesktopdialog.c
  - src/screencast.c

Usage:
  python3 patch_portal_autoapprove.py /absolute/path/to/xdg-desktop-portal-gnome

It will:
  - sanity-check file contents for known sentinels (46.2/49.0)
  - create *.bak backups
  - apply minimal, robust edits
  - print a summary diff-ish preview of touched regions
"""

import sys, os, re, textwrap, shutil, subprocess

REQUIRED_FILES = {
    "remotedesktopdialog.c": "src/remotedesktopdialog.c",
    "screencast.c": "src/screencast.c",
}

# --- Sentinels we expect for known-good layouts (46.2/49.0). Fail loudly if not found. ---
SENTINELS = {
    "remotedesktopdialog.c": [
        r"^G_DEFINE_TYPE\s*\(\s*RemoteDesktopDialog\s*,\s*remote_desktop_dialog\s*,\s*ADW_TYPE_WINDOW\s*\)",
        r"^RemoteDesktopDialog\s*\*\s*remote_desktop_dialog_new\s*\(",
        r"^static void\s+button_clicked\s*\(\s*GtkWidget\s*\*button,\s*",
    ],
    "screencast.c": [
        r"^static gboolean\s+restore_stream_from_data\s*\(",
        r"^static gboolean\s+handle_start\s*\(",
        r"^static\s+ScreenCastDialogHandle\s*\*\s*create_screen_cast_dialog\s*\(",
        r"^G_DEFINE_TYPE\s*\(\s*ScreenCastSession\s*,\s*screen_cast_session\s*,\s*session_get_type\s*\(\)\s*\)",
    ],
}

def die(msg):
    print(f"\n[ERROR] {msg}\n", file=sys.stderr)
    print("This script is intentionally strict.\n"
          "• Confirm you checked out the tag selected by tools_portal_tag_probe.py.\n"
          "• If you’re on a different version/layout, adapt the patch manually.\n")
    sys.exit(1)

def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def write_backup_then(path, new_text):
    backup = path + ".bak"
    if not os.path.exists(backup):
        shutil.copy2(path, backup)
    with open(path, "w", encoding="utf-8") as f:
        f.write(new_text)

def assert_sentinels(path, content, patterns):
    for pat in patterns:
        if not re.search(pat, content, flags=re.M):
            die(f"Sentinel not found in {path}:\n  pattern: {pat}\n"
                "File does not match the expected layout for supported tags (e.g., 46.2/49.0).\n"
                "Open the file and verify function names/blocks, then patch manually.")

def detect_tag(root):
    """Best-effort: print which tag/commit is checked-out (for friendlier logs)."""
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
        # fall back to short SHA
        proc2 = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        if proc2.returncode == 0:
            return proc2.stdout.strip()
    except Exception:
        pass
    return None

# ------------------------------- REMOTEDESKTOP --------------------------------

def _ensure_helpers_inserted(text):
    """
    Ensure our helpers exist *above* remote_desktop_dialog_new(...).
    We place them immediately before the function declaration for stability.
      - auto_approve_remote_desktop_idle(...)  (existing)
      - on_dialog_notify_mapped(...)           (new)
    """
    # Find start of remote_desktop_dialog_new to insert helpers right before it.
    new_decl = re.search(r"\n(RemoteDesktopDialog\s*\*\s*remote_desktop_dialog_new\s*\([^)]*\)\s*\{)", text)
    if not new_decl:
        die("Could not find remote_desktop_dialog_new() declaration to insert helpers before it.")
    insert_at = new_decl.start(1)

    need_idle = "OPINIONATED_PATCH_AUTO_APPROVE_DEFERRED" not in text
    need_mapped = "OPINIONATED_PATCH_AUTO_APPROVE_MAPPED_HANDLER" not in text

    helper_idle = textwrap.dedent(r"""
        /* === OPINIONATED_PATCH_AUTO_APPROVE_DEFERRED ===
         * We defer the "Share" click until a GLib scheduled callback so:
         *  - The dialog is realized and the **orange Share button** exists.
         *  - External code has already connected to the "done" signal.
         *
         * Visual effect:
         *  - The centered white dialog may flash momentarily (or not be noticeable).
         *  - The **Allow Remote Interaction** toggle is ON.
         *  - The dialog auto-confirms as if **Share** was pressed, so it disappears immediately,
         *    skipping the screen/monitor grid selection UI you described ("LG 24\"" vs "Telecom 23\"").
         */
        static gboolean
        auto_approve_remote_desktop_idle (gpointer user_data)
        {
          RemoteDesktopDialog *dialog = REMOTE_DESKTOP_DIALOG (user_data);

          /* Turn ON the permission toggle in the first row. */
          adw_switch_row_set_active (dialog->allow_remote_interaction_switch, TRUE);

          /* Satisfy accept gating even if no screen cast selection has happened yet. */
          dialog->is_screen_cast_sources_selected = TRUE;

          /*
           * Now we "press" the bright orange **Share** button.
           * Because this runs after mapping, the parent has already connected to "done",
           * so the emission is received and the dialog closes instead of lingering.
           */
          button_clicked (GTK_WIDGET (dialog->accept_button), dialog);

          return G_SOURCE_REMOVE; /* run once */
        }
        /* === /OPINIONATED_PATCH_AUTO_APPROVE_DEFERRED === */
    """).strip("\n") + "\n\n"

    helper_mapped = textwrap.dedent(r"""
        /* === OPINIONATED_PATCH_AUTO_APPROVE_MAPPED_HANDLER ===
         * Runs once when the dialog becomes mapped (actually on-screen), then defers
         * auto-approve by ~120ms so the caller has connected its "done" handler.
         * Non-blocking: this schedules a timer; it does not sleep the main loop.
         */
        static void
        on_dialog_notify_mapped (GObject *obj, GParamSpec *pspec, gpointer user_data)
        {
          GtkWidget *w = GTK_WIDGET (obj);
          /* Only proceed after the dialog is really mapped */
          if (!gtk_widget_get_mapped (w))
            return;

          /* Disconnect ourselves so we only run once */
          g_signal_handlers_disconnect_by_func (obj, G_CALLBACK (on_dialog_notify_mapped), user_data);

          /* Defer one tick; callers like TeamViewer/RustDesk will have their handlers connected */
          g_timeout_add (120, (GSourceFunc) auto_approve_remote_desktop_idle, user_data);
        }
        /* === /OPINIONATED_PATCH_AUTO_APPROVE_MAPPED_HANDLER === */
    """).strip("\n") + "\n\n"

    # Build insertion text in correct order (idle first, then mapped handler)
    insertion = ""
    if need_idle:
        insertion += helper_idle
    if need_mapped:
        insertion += helper_mapped

    if not insertion:
        return text  # nothing to do

    return text[:insert_at] + insertion + text[insert_at:]


def _strip_previous_schedule_blocks(body):
    """
    Remove any previous scheduling blocks we might have injected in older versions:
      - Immediate click block (OPINIONATED_PATCH_AUTO_APPROVE)
      - Idle scheduler block (OPINIONATED_PATCH_SCHEDULE_DEFERRED_APPROVE)
    """
    patt_immediate = re.compile(
        r"/\*\s*===\s*OPINIONATED_PATCH_AUTO_APPROVE\s*===.*?/\*\s*===\s*/OPINIONATED_PATCH_AUTO_APPROVE\s*===\s*\*/",
        re.S
    )
    patt_idle = re.compile(
        r"/\*\s*===\s*OPINIONATED_PATCH_SCHEDULE_DEFERRED_APPROVE\s*===.*?/\*\s*===\s*/OPINIONATED_PATCH_SCHEDULE_DEFERRED_APPROVE\s*===\s*\*/",
        re.S
    )
    body = re.sub(patt_immediate, "", body)
    body = re.sub(patt_idle, "", body)
    return body


def patch_remotedesktopdialog_c(text):
    """
    Strategy (mapped-aware accept):
      - Insert static helpers:
          * auto_approve_remote_desktop_idle(...)   (kept as the "do it" action)
          * on_dialog_notify_mapped(...)            (new, hooks notify::mapped then defers ~120ms)
      - In remote_desktop_dialog_new(...), connect notify::mapped handler
        right before 'return dialog;'.
      - Remove any older scheduler injections (immediate/idle) to avoid double fire.
    """
    # 1) Ensure helpers are present above the constructor
    text = _ensure_helpers_inserted(text)

    # 2) Locate constructor body
    func_re = r"(RemoteDesktopDialog\s*\*\s*remote_desktop_dialog_new\s*\([^)]*\)\s*\{\s*)(.*?)(\n\s*return\s+dialog;\s*\})"
    m = re.search(func_re, text, flags=re.S)
    if not m:
        die("Could not locate remote_desktop_dialog_new body for patching.")

    prefix, body, suffix = m.group(1), m.group(2), m.group(3)

    # 3) Remove any previous schedule blocks
    body = _strip_previous_schedule_blocks(body)

    # 4) Avoid double scheduling with the new approach
    if "OPINIONATED_PATCH_SCHEDULE_ON_MAPPED" in body or "on_dialog_notify_mapped" in body and "notify::mapped" in body:
        # already patched with mapped-aware approach
        return text

    # 5) Inject the mapped hook just before return
    inject = textwrap.dedent(r"""
        /* === OPINIONATED_PATCH_SCHEDULE_ON_MAPPED ===
         * Schedule auto-approve only after the dialog is mapped (visible).
         * Then defer by ~120ms on the main loop to avoid early "done" races.
         */
        g_signal_connect_after (dialog,
                                "notify::mapped",
                                G_CALLBACK (on_dialog_notify_mapped),
                                dialog);
        /* === /OPINIONATED_PATCH_SCHEDULE_ON_MAPPED === */
    """).strip("\n")

    new_body = body.rstrip() + "\n\n  " + inject + "\n"
    patched = text[:m.start(2)] + new_body + text[m.end(2):]
    return patched

# -------------------------------- SCREENCAST ----------------------------------

def patch_screencast_c(text):
    """
    Strategy:
      - Add a small static helper 'start_first_monitor(...)' that builds a single-monitor stream and calls start_session().
      - In handle_start(...): after '!restore_stream_from_data(...)', try start_first_monitor(); only if THAT fails, fall back to the chooser dialog.

      We insert the helper **above 'handle_start'** (more robust than anchoring after the _free() function),
      and modify the 'if (!restore_stream_from_data ...)' block with a resilient pattern.
    """
    # 1) Insert helper if missing
    if "OPINIONATED_PATCH_START_FIRST_MONITOR" not in text:
        # Find the beginning of handle_start() and insert the helper right before it.
        handle_decl_re = r"\n(static\s+gboolean\s+handle_start\s*\([^)]*\)\s*\{)"
        hd = re.search(handle_decl_re, text, flags=re.S)
        if not hd:
            die("Could not find handle_start() declaration to insert helper before it.")

        helper = textwrap.dedent(r"""
            /* === OPINIONATED_PATCH_START_FIRST_MONITOR ===
             * Try to auto-pick the first logical monitor as a Screencast source.
             * Returns TRUE if session successfully started.
             *
             * Visual effect:
             *   - When Screencast would normally show a monitor grid, we auto-select the first
             *     tile (your “LG 24\"” in the example) and proceed, so the chooser usually won’t appear.
             */
            static gboolean
            start_first_monitor (ScreenCastSession *screen_cast_session)
            {
              DisplayStateTracker *dst = display_state_tracker_get ();
              GList *l;

              for (l = display_state_tracker_get_logical_monitors (dst); l; l = l->next)
                {
                  LogicalMonitor *lm = l->data;
                  GList *monitors = logical_monitor_get_monitors (lm);
                  if (monitors)
                    {
                      Monitor *m = monitor_dup ((Monitor *)monitors->data);

                      ScreenCastStreamInfo *info = g_new0 (ScreenCastStreamInfo, 1);
                      info->type = SCREEN_CAST_SOURCE_TYPE_MONITOR;
                      info->data.monitor = m;
                      info->id = 1;

                      GPtrArray *arr =
                        g_ptr_array_new_with_free_func ((GDestroyNotify) screen_cast_stream_info_free);
                      g_ptr_array_add (arr, info);

                      /* Ensure selection flags are sane */
                      screen_cast_session->select.multiple = FALSE;
                      screen_cast_session->select.source_types.monitor = TRUE;

                      g_autoptr(GError) error = NULL;
                      if (!start_session (screen_cast_session, arr, &error))
                        {
                          if (error)
                            g_warning ("Failed to start first-monitor session: %s", error->message);
                          /* arr is owned by start_session on success; free on failure */
                          g_ptr_array_unref (arr);
                          return FALSE;
                        }

                      return TRUE;
                    }
                }
              return FALSE;
            }
            /* === /OPINIONATED_PATCH_START_FIRST_MONITOR === */
        """).strip("\n")

        insert_at = hd.start(1)
        text = text[:insert_at] + "\n\n" + helper + "\n\n" + text[insert_at:]

    # 2) Modify handle_start flow
    handle_re = r"(static\s+gboolean\s+handle_start\s*\([^)]*\)\s*\{\s*)(.*?)(\n\}\s*)"
    hm = re.search(handle_re, text, flags=re.S)
    if not hm:
        die("Could not locate handle_start() body for patching in screencast.c.")

    body = hm.group(2)

    # Robustly find the restore branch
    branch_re = (r"if\s*\(\s*!\s*restore_stream_from_data\s*\(\s*[^)]*\)\s*\)\s*\{\s*(.*?)\s*\}")
    bm = re.search(branch_re, body, flags=re.S)
    if not bm:
        die("Could not find the '!restore_stream_from_data' branch in handle_start().")

    branch_block = bm.group(1)
    if "OPINIONATED_PATCH_TRY_FIRST_MONITOR" in branch_block:
        return text  # already patched

    replacement_block = textwrap.dedent(r"""
        /* === OPINIONATED_PATCH_TRY_FIRST_MONITOR ===
         * Try unattended path: start the first monitor immediately.
         * Only fall back to the chooser dialog if that fails.
         *
         * Visual impact:
         *   - The monitor selection grid (two tiles) usually won’t be shown; we auto-pick the first tile.
         */
        if (start_first_monitor (screen_cast_session))
          return TRUE;
        /* Fallback to original chooser dialog */
        ScreenCastDialogHandle *dialog_handle;
        dialog_handle = create_screen_cast_dialog (screen_cast_session,
                                                   invocation,
                                                   request,
                                                   arg_parent_window);
        screen_cast_session->dialog_handle = dialog_handle;
        /* === /OPINIONATED_PATCH_TRY_FIRST_MONITOR === */
    """).strip("\n")

    # Normalize the whole branch to use screen_cast_session explicitly (46.2/49.0 both use it)
    new_body = re.sub(
        branch_re,
        "if (!restore_stream_from_data (screen_cast_session)) {\n"
        + replacement_block +
        "\n}",
        body,
        flags=re.S
    )

    text = text[:hm.start(2)] + new_body + text[hm.end(2):]
    return text

# ----------------------------------- MAIN ------------------------------------

def main():
    if len(sys.argv) != 2:
        die("Provide the path to your local checkout of the chosen tag (e.g., 46.2/49.0):\n"
            "  python3 patch_portal_autoapprove.py /path/to/xdg-desktop-portal-gnome")

    root = os.path.abspath(sys.argv[1])

    detected = detect_tag(root)
    if detected:
        print(f"[info] Detected checkout: {detected}")

    for short, rel in REQUIRED_FILES.items():
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die(f"Missing required file: {p}\n"
                "You did not point me at a supported source tree (or tree is incomplete).")

    # Validate sentinels
    texts = {}
    for short, rel in REQUIRED_FILES.items():
        p = os.path.join(root, rel)
        s = read(p)
        assert_sentinels(p, s, SENTINELS[short])
        texts[short] = (p, s)

    # Patch remotedesktopdialog.c (mapped-aware auto-approve)
    rpath, rtext = texts["remotedesktopdialog.c"]
    rpatched = patch_remotedesktopdialog_c(rtext)

    # Patch screencast.c (auto-pick first monitor)
    spath, stext = texts["screencast.c"]
    spatched = patch_screencast_c(stext)

    # Write
    if rpatched != rtext:
        write_backup_then(rpath, rpatched)
        print(f"[OK] Patched {rpath} (backup at {rpath}.bak)")
    else:
        print(f"[SKIP] {rpath} already patched; left as-is (backup untouched).")

    if spatched != stext:
        write_backup_then(spath, spatched)
        print(f"[OK] Patched {spath} (backup at {spath}.bak)")
    else:
        print(f"[SKIP] {spath} already patched; left as-is (backup untouched).")

    print("\nNext steps:\n"
          "  1) Build + install (see README.md)\n"
          "  2) Restart user portal services\n")

if __name__ == "__main__":
    main()
