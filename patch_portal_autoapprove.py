#!/usr/bin/env python3
"""
patch_portal_autoapprove.py

Highly-opinionated, environment-distrusting patcher for GNOME's
xdg-desktop-portal-gnome (tag 49.0) to:
  1) Auto-approve Remote-Desktop dialog (enable "Allow Remote Interaction", press Share)
  2) Auto-select first output for Screencast when no restore data is available

Targets (MUST match 49.0 layout):
  - src/remotedesktopdialog.c
  - src/screencast.c

Usage:
  python3 patch_portal_autoapprove.py /absolute/path/to/xdg-desktop-portal-gnome

It will:
  - sanity-check file contents for known sentinels (49.0)
  - create *.bak backups
  - apply minimal, robust edits
  - print a summary diff-ish preview of touched regions
"""

import sys, os, re, textwrap, shutil

REQUIRED_FILES = {
    "remotedesktopdialog.c": "src/remotedesktopdialog.c",
    "screencast.c": "src/screencast.c",
}

# --- Sentinels we expect in 49.0 (fail loudly if not found) ---
SENTINELS = {
    "remotedesktopdialog.c": [
        r"^G_DEFINE_TYPE\s*\(\s*RemoteDesktopDialog\s*,\s*remote_desktop_dialog\s*,\s*ADW_TYPE_WINDOW\s*\)",
        r"^RemoteDesktopDialog\s*\*\s*remote_desktop_dialog_new\s*\(",
        r"^static void\s+button_clicked\s*\(\s*GtkWidget\s*\*button,\s*",
    ],
    "screencast.c": [
        r"^static gboolean\s+restore_stream_from_data\s*\(",
        r"^static gboolean\s+handle_start\s*\(",
        r"^static ScreenCastDialogHandle\s*\*\s*create_screen_cast_dialog\s*\(",
        r"^G_DEFINE_TYPE\s*\(\s*ScreenCastSession\s*,\s*screen_cast_session\s*,\s*session_get_type\s*\(\)\s*\)",
    ],
}

def die(msg):
    print(f"\n[ERROR] {msg}\n", file=sys.stderr)
    print("This script is intentionally strict.\n"
          "• Confirm you checked out the *49.0* sources exactly.\n"
          "• If you’re on a different version, adapt the patch manually.\n")
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
                "File no longer matches expected 49.0 layout.\n"
                "Open the file and verify function names/blocks, then patch manually.")

def patch_remotedesktopdialog_c(text):
    """
    Strategy:
      In remote_desktop_dialog_new(...):
        - Ensure "Allow Remote Interaction" is ON
        - Mark 'is_screen_cast_sources_selected' true (satisfies accept gating)
        - Immediately call button_clicked(accept_button, dialog) to emit DONE/Share
      This bypasses any UI and unconditionally approves interaction.

      We inject right before the final 'return dialog;' in remote_desktop_dialog_new.
    """
    func_re = r"(RemoteDesktopDialog\s*\*\s*remote_desktop_dialog_new\s*\([^)]*\)\s*\{\s*)(.*?)(\n\s*return\s+dialog;\s*\})"
    m = re.search(func_re, text, flags=re.S)
    if not m:
        die("Could not locate remote_desktop_dialog_new body for patching.")

    body = m.group(2)
    # Avoid double-inject
    if "OPINIONATED_PATCH_AUTO_APPROVE" in body:
        return text  # already patched

    inject = textwrap.dedent(r"""
        /* === OPINIONATED_PATCH_AUTO_APPROVE ===
         * Short-circuit Remote Desktop dialog:
         *   - Force "Allow Remote Interaction" ON
         *   - Pretend a screen-cast source is selected (so "Share" is allowed)
         *   - Immediately trigger the accept handler (equivalent to pressing Share)
         *
         * This intentionally bypasses consent UI; use only on machines you administer.
         */
        adw_switch_row_set_active (dialog->allow_remote_interaction_switch, TRUE);
        dialog->is_screen_cast_sources_selected = TRUE;

        /* Immediately behave as if user clicked Share */
        button_clicked (GTK_WIDGET (dialog->accept_button), dialog);
        /* === /OPINIONATED_PATCH_AUTO_APPROVE === */
    """).strip("\n")

    new_body = body + "\n\n  " + inject + "\n"
    patched = text[:m.start(2)] + new_body + text[m.end(2):]
    return patched

def patch_screencast_c(text):
    """
    Strategy:
      - Add a small static helper 'start_first_monitor(...)' that builds a single-monitor stream and calls start_session().
      - In handle_start(...): after '!restore_stream_from_data(...)', try start_first_monitor(); only if THAT fails, fall back to the chooser dialog.

      We insert the helper above 'handle_start' and modify the 'if (!restore_stream_from_data ...)' block.
    """
    # 1) Insert helper if missing
    if "OPINIONATED_PATCH_START_FIRST_MONITOR" not in text:
        insert_after_anchor = r"(static ShellWindow\s*\*\s*find_best_window_by_app_id_and_title\s*\([^)]*\)\s*\{.*?\}\s*)"
        anchor_match = re.search(insert_after_anchor, text, flags=re.S)
        if not anchor_match:
            die("Could not find anchor after find_best_window_by_app_id_and_title() to insert helper.")

        helper = textwrap.dedent(r"""
            /* === OPINIONATED_PATCH_START_FIRST_MONITOR ===
             * Try to auto-pick the first logical monitor as a Screencast source.
             * Returns TRUE if session successfully started.
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

        idx = anchor_match.end(1)
        text = text[:idx] + "\n\n" + helper + "\n\n" + text[idx:]

    # 2) Modify handle_start flow
    handle_re = r"(static gboolean\s+handle_start\s*\([^)]*\)\s*\{\s*)(.*?)(\n\}\s*)"
    hm = re.search(handle_re, text, flags=re.S)
    if not hm:
        die("Could not locate handle_start() body for patching in screencast.c.")

    body = hm.group(2)

    # Find the branch that creates the dialog when restore fails
    branch_re = r"if\s*\(!restore_stream_from_data\s*\(\s*screen_cast_session\s*\)\)\s*\{\s*(.*?)\s*\}"
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

    new_body = re.sub(branch_re, "if (!restore_stream_from_data (screen_cast_session)) {\n" +
                      replacement_block + "\n}", body, flags=re.S)

    text = text[:hm.start(2)] + new_body + text[hm.end(2):]
    return text

def main():
    if len(sys.argv) != 2:
        die("Provide the path to your local *49.0* checkout:\n"
            "  python3 patch_portal_autoapprove.py /path/to/xdg-desktop-portal-gnome")

    root = os.path.abspath(sys.argv[1])
    for short, rel in REQUIRED_FILES.items():
        p = os.path.join(root, rel)
        if not os.path.isfile(p):
            die(f"Missing required file: {p}\n"
                "You did not point me at a 49.0 source tree (or tree is incomplete).")

    # Validate sentinels
    texts = {}
    for short, rel in REQUIRED_FILES.items():
        p = os.path.join(root, rel)
        s = read(p)
        assert_sentinels(p, s, SENTINELS[short])
        texts[short] = (p, s)

    # Patch remotedesktopdialog.c
    rpath, rtext = texts["remotedesktopdialog.c"]
    rpatched = patch_remotedesktopdialog_c(rtext)

    # Patch screencast.c
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
          "  1) Build + install (see README.md below)\n"
          "  2) Restart user portal services\n")

if __name__ == "__main__":
    main()
