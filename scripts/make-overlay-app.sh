#!/usr/bin/env bash
# Build "C-3PO.app" and "C-3PO Quit.app" -- double-click (or Spotlight) to start
# and stop c3po without a terminal.
#
#   bash scripts/make-overlay-app.sh              # install to ~/Applications (user-level)
#   bash scripts/make-overlay-app.sh /Applications
#
# Each app is only a thin wrapper around a shell script (c3po-open.sh /
# c3po-quit.sh): the launcher execs it and shows a dialog if it fails. Behaviour
# lives in those scripts -- edit them, no rebuild needed.
#
# Uninstall: rm -rf "~/Applications/C-3PO.app" "~/Applications/C-3PO Quit.app"
set -euo pipefail

DEST="${1:-$HOME/Applications}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ICNS="$REPO/assets/c3po.icns"

for s in c3po-open.sh c3po-quit.sh; do
  [ -x "$REPO/scripts/$s" ] || { echo "ERROR: not found or not executable: $REPO/scripts/$s" >&2; exit 1; }
done

# Build the icon on demand so a fresh clone gets one without a separate step.
if [ ! -f "$ICNS" ] && [ -x "$REPO/.venv/bin/python" ]; then
  echo "Building icon..."
  "$REPO/.venv/bin/python" "$REPO/scripts/make-icon.py" || true
fi

mkdir -p "$DEST"

# build_app <app-name> <bundle-id> <script-path> <log-name> <alert-title>
build_app() {
  local name="$1" bundle_id="$2" script="$3" logname="$4" title="$5"
  local app="$DEST/$name.app"

  # Remove any existing bundle first: LaunchServices caches Info.plist, so an
  # in-place overwrite can keep serving the old metadata.
  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

  # --- Info.plist ---
  # LSUIElement=1: no Dock icon, no focus stealing -- these are "run and quit"
  # launchers, the browser is the real UI.
  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>$name</string>
  <key>CFBundleDisplayName</key>       <string>$name</string>
  <key>CFBundleIdentifier</key>        <string>$bundle_id</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>launcher</string>
  <key>CFBundleIconFile</key>          <string>icon</string>
  <key>LSUIElement</key>               <true/>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST

  # --- executable ---
  # Thin shell: run the script, surface failures in a dialog. A double-clicked
  # app has no terminal, so a silent failure just looks like a broken app.
  cat > "$app/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# launchd hands us a C locale. Diagnostics are English-only so this is belt and
# braces, but any byte-wise processing (sed/tr) on non-ASCII would fail with
# "illegal byte sequence" -- and that would turn a success into a false error.
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Homebrew is not on PATH for GUI-launched apps; the scripts need ffmpeg/lsof.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"

SCRIPT="$script"
if [ ! -x "\$SCRIPT" ]; then
  osascript -e 'display alert "$title" message "$(basename "$script") not found -- did the repo move?" as critical'
  exit 1
fi

# Fixed path rather than \$TMPDIR: launchd's TMPDIR and AppleScript's "temporary
# items" folder are not necessarily the same directory.
log="\$HOME/$logname"
if "\$SCRIPT" >"\$log" 2>&1; then
  exit 0
fi

# Failed: show the script's own diagnostics. osascript reads the file itself
# instead of taking it through shell string interpolation -- no escaping needed,
# quotes in the message are safe.
osascript -e 'set p to (POSIX path of (path to home folder)) & "$logname"' \\
          -e 'try' \\
          -e '  set msg to (do shell script "tail -15 " & quoted form of p)' \\
          -e 'on error' \\
          -e '  set msg to "It failed, and the diagnostic log could not be read."' \\
          -e 'end try' \\
          -e 'display alert "$title" message msg as critical' 2>/dev/null
exit 1
LAUNCHER
  chmod +x "$app/Contents/MacOS/launcher"

  # --- icon ---
  if [ -f "$ICNS" ]; then
    cp "$ICNS" "$app/Contents/Resources/icon.icns"
  else
    # No icon: drop the key so Finder shows the generic app icon rather than a
    # broken-image placeholder.
    /usr/libexec/PlistBuddy -c "Delete :CFBundleIconFile" "$app/Contents/Info.plist" 2>/dev/null || true
    echo "WARNING: $ICNS missing; $name will use the generic icon." >&2
  fi

  plutil -lint "$app/Contents/Info.plist"

  # Register immediately so Spotlight finds it without waiting for a reindex.
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$app" 2>/dev/null || true

  echo "Created: $app"
}

build_app "C-3PO" "com.nunchuk.c3po" \
  "$REPO/scripts/c3po-open.sh" ".c3po-launch.log" "C-3PO failed to start"

build_app "C-3PO Quit" "com.nunchuk.c3po.quit" \
  "$REPO/scripts/c3po-quit.sh" ".c3po-quit.log" "C-3PO failed to stop"

cat <<EOF

  Double-click them, or search Spotlight for "C-3PO"
  Uninstall: rm -rf "$DEST/C-3PO.app" "$DEST/C-3PO Quit.app"
EOF
