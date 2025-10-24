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

# --------------------------- screencastwidget.c (STRICT) -----------------------

def patch_screencastwidget_c(text):
    """
    PROCEDURAL + STRICT:
      We accept either of two known layouts for update_monitor_container(ScreenCastWidget *widget):
        A) STRICT layout we authored for (preferred):
           - Local 'int child_count = screen_cast_geometry_container_get_child_count(SCREEN_CAST_GEOMETRY_CONTAINER(monitor_container));'
           - Followed immediately by the 'singular' CSS toggle using 'child_count'.
        B) 46.2 inline layout (upstream):
           - The 'singular' CSS toggle calls get_child_count(...) *inline* in the if-condition.

      Behavior:
        • If already patched (tag present) → return text unchanged (idempotent).
        • If STRICT (A) → replace ONLY the if/else block, inject our auto-select block.
        • If inline (B) → FIRST rewrite into STRICT (insert the local declaration + strict toggle),
                          THEN apply the same replacement + injection.
        • Anything else → die() with a *specific* and *actionable* error message.

      We *never* patch unless all preconditions match exactly.
    """
    func_re = r"(static\s+void\s+update_monitor_container\s*\(\s*ScreenCastWidget\s*\*widget\s*\)\s*\{\s*)(.*?)(\n\}\s*)"
    m = re.search(func_re, text, flags=re.S)
    if not m:
        die(
            "update_monitor_container(): function not found.\n"
            "Expected exact signature:\n"
            "  static void update_monitor_container (ScreenCastWidget *widget)\n",
            "screencastwidget.c"
        )

    prefix, body, suffix = m.group(1), m.group(2), m.group(3)

    # Guard: already patched?
    if AUTO_FIRST_MONITOR_TAG in body:
        return text  # idempotent

    # Canonical fragments we rely on
    decl_exact = (
        r"\bint\s+child_count\s*=\s*"
        r"screen_cast_geometry_container_get_child_count\s*\(\s*"
        r"SCREEN_CAST_GEOMETRY_CONTAINER\s*\(\s*monitor_container\s*\)\s*"
        r"\)\s*;\s*"
    )

    toggle_strict_block = (
        r"if\s*\(\s*child_count\s*==\s*1\s*\)\s*\{\s*"
        r"gtk_widget_add_css_class\s*\(\s*monitor_container\s*,\s*\"singular\"\s*\)\s*;\s*"
        r"\}\s*else\s*\{\s*"
        r"gtk_widget_remove_css_class\s*\(\s*monitor_container\s*,\s*\"singular\"\s*\)\s*;\s*"
        r"\}"
    )

    # Variant B: inline get_child_count(...) inside the if condition (46.2 upstream shape)
    toggle_inline_block = (
        r"if\s*\(\s*screen_cast_geometry_container_get_child_count\s*\(\s*"
        r"SCREEN_CAST_GEOMETRY_CONTAINER\s*\(\s*monitor_container\s*\)\s*\)\s*==\s*1\s*\)\s*"
        r"gtk_widget_add_css_class\s*\(\s*monitor_container\s*,\s*\"singular\"\s*\)\s*;\s*"
        r"else\s*"
        r"gtk_widget_remove_css_class\s*\(\s*monitor_container\s*,\s*\"singular\"\s*\)\s*;"
    )

    # The block we want to replace the toggle with (strict form) + our injection
    replacement_if_block = textwrap.dedent(f"""
        if (child_count == 1)
          gtk_widget_add_css_class (monitor_container, "singular");
        else
          gtk_widget_remove_css_class (monitor_container, "singular");

        /* === {AUTO_FIRST_MONITOR_TAG} ===
         * STRICT patch: auto-select the first monitor when present
         * and multiple selection is NOT allowed.
         */
        if (child_count > 0 && !widget->allow_multiple)
          {{
            GtkWidget *first_button = gtk_widget_get_first_child (monitor_container);
            if (first_button)
              gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (first_button), TRUE);
          }}
        /* === /{AUTO_FIRST_MONITOR_TAG} === */
    """).strip("\n")

    # 1) Try STRICT path first: declaration + strict toggle present
    decl_match = re.search(decl_exact, body)
    strict_toggle_match = re.search(toggle_strict_block, body)

    if decl_match and strict_toggle_match:
        # Sanity: ensure decl is immediately before the toggle (no arbitrary code between),
        # allowing whitespace/comments. We enforce "close proximity" to avoid false positives.
        intervening = body[decl_match.end():strict_toggle_match.start()]
        if re.search(r"[^\s/\*]", intervening):  # anything other than whitespace/comments?
            die(
                "update_monitor_container(): STRICT check failed.\n"
                "Reason: The expected 'child_count' declaration is not immediately before the\n"
                "'singular' CSS toggle. Our opinionated patch requires that layout to avoid\n"
                "rewriting unrelated code. Compare to upstream 46.2/49.0.\n",
                "screencastwidget.c"
            )

        # Replace only the toggle block (keep the declaration intact)
        new_body = re.sub(toggle_strict_block, replacement_if_block, body, count=1)
        assert AUTO_FIRST_MONITOR_TAG in new_body
        return text[:m.start(2)] + new_body + text[m.end(2):]

    # 2) Try INLINE shape: rewrite to STRICT, then apply the same replacement
    inline_match = re.search(toggle_inline_block, body)
    if inline_match:
        # Ensure we don't already have a child_count declaration; we will insert exactly one.
        if re.search(r"\bint\s+child_count\b", body):
            die(
                "update_monitor_container(): Found inline 'singular' toggle *and* an existing\n"
                "'child_count' variable. This mixed state is unexpected. Please normalize the\n"
                "toggle to either (A) strict local 'child_count' usage or (B) pure inline call.\n"
                "Refusing to guess which one to transform.",
                "screencastwidget.c"
            )

        # We will transform the inline toggle into:
        #    int child_count = get_child_count(...);
        #    if (child_count == 1) { add CSS } else { remove CSS }
        #
        # To do so, we:
        #  - splice in the declaration immediately *before* the inline if/else
        #  - replace the inline if/else with the strict toggle using 'child_count'
        #
        # First, build the declaration text.
        decl_text = (
            "int child_count = screen_cast_geometry_container_get_child_count ("
            "SCREEN_CAST_GEOMETRY_CONTAINER (monitor_container));\n\n"
        )

        # Insert the declaration just before the inline block
        body_with_decl = body[:inline_match.start()] + decl_text + body[inline_match.start():]

        # Now replace the inline if/else (we must recompute match on the updated body)
        inline_match_2 = re.search(toggle_inline_block, body_with_decl)
        if not inline_match_2:
            # This would be extremely strange unless the source changed after insertion
            die(
                "update_monitor_container(): Internal transform error while normalizing inline\n"
                "toggle to strict form. After inserting the local 'child_count' declaration, the\n"
                "expected inline toggle was not found at the previous location. Aborting to avoid\n"
                "corrupting the file.",
                "screencastwidget.c"
            )

        # Replace inline with strict block (WITHOUT our injection yet)
        strict_toggle_only = (
            "if (child_count == 1) {\n"
            '  gtk_widget_add_css_class (monitor_container, "singular");\n'
            "} else {\n"
            '  gtk_widget_remove_css_class (monitor_container, "singular");\n'
            "}"
        )
        body_strict = (
            body_with_decl[:inline_match_2.start()] +
            strict_toggle_only +
            body_with_decl[inline_match_2.end():]
        )

        # Verify we now have EXACTLY the strict shape we expect (decl + strict toggle)
        decl_match2 = re.search(decl_exact, body_strict)
        strict_toggle_match2 = re.search(toggle_strict_block, body_strict)
        if not (decl_match2 and strict_toggle_match2):
            die(
                "update_monitor_container(): Normalization step failed.\n"
                "We attempted to rewrite the inline 'singular' toggle into the strict layout\n"
                "(local 'child_count' + strict if/else), but the post-transform body did not\n"
                "match our expected strict sentinel. No changes were written.",
                "screencastwidget.c"
            )

        # Ensure declaration is adjacent to the toggle (only whitespace/comments allowed)
        intervening2 = body_strict[decl_match2.end():strict_toggle_match2.start()]
        if re.search(r"[^\s/\*]", intervening2):
            die(
                "update_monitor_container(): Normalization produced unexpected code between\n"
                "the 'child_count' declaration and the strict toggle. Our patch expects them\n"
                "to be adjacent. Aborting to avoid unsafe rewrite.",
                "screencastwidget.c"
            )

        # Finally, apply our injection by replacing the strict toggle with replacement_if_block
        new_body = re.sub(toggle_strict_block, replacement_if_block, body_strict, count=1)
        assert AUTO_FIRST_MONITOR_TAG in new_body
        return text[:m.start(2)] + new_body + text[m.end(2):]

    # 3) Neither strict nor inline shapes detected → explain precisely what we expected
    # Provide a focused hint: show lines that reference 'singular' or get_child_count(...) near end of function
    tail = body[-800:]  # last ~800 chars to avoid flooding
    singular_spots = []
    for m2 in re.finditer(r"(singular|screen_cast_geometry_container_get_child_count)", tail):
        start = max(0, m2.start() - 120)
        end = min(len(tail), m2.end() + 120)
        snippet = tail[start:end].replace("\n", "\\n")
        singular_spots.append(f"...{snippet}...")

    details = "\n  - " + "\n  - ".join(singular_spots) if singular_spots else " (no nearby references found)"
    die(
        "update_monitor_container(): STRICT/INLINE shape not recognized.\n"
        "We require either:\n"
        "  A) Local 'int child_count = screen_cast_geometry_container_get_child_count(SCREEN_CAST_GEOMETRY_CONTAINER(monitor_container));'\n"
        "     immediately followed by the 'singular' CSS toggle using 'child_count';\n"
        "  OR\n"
        "  B) An inline 'singular' CSS toggle that calls get_child_count(...) directly in the if.\n"
        "\n"
        "Your function body does not match either pattern. Here are the closest hints near the end of the function:\n"
        f"{details}\n",
        "screencastwidget.c"
    )

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
