#!/usr/bin/env bash
# Build "C-3PO.app" -- double-click or Spotlight to start c3po and open its page.
#
#   bash scripts/make-overlay-app.sh              # install to ~/Applications (user-level)
#   bash scripts/make-overlay-app.sh /Applications
#
# The app is only a thin wrapper around scripts/c3po-open.sh: AppleScript-free
# launcher execs the script, which probes/starts the server and opens the
# browser. Behaviour lives in that script -- edit it, no rebuild needed.
#
# Uninstall: rm -rf "~/Applications/C-3PO.app"
set -euo pipefail

DEST="${1:-$HOME/Applications}"
APP="$DEST/C-3PO.app"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENER="$REPO/scripts/c3po-open.sh"
ICNS="$REPO/assets/c3po.icns"

[ -x "$OPENER" ] || { echo "ERROR: not found or not executable: $OPENER" >&2; exit 1; }

# Build the icon on demand so a fresh clone gets one without a separate step.
if [ ! -f "$ICNS" ] && [ -x "$REPO/.venv/bin/python" ]; then
  echo "Building icon..."
  "$REPO/.venv/bin/python" "$REPO/scripts/make-icon.py" || true
fi

mkdir -p "$DEST"

# Remove any existing bundle first: LaunchServices caches Info.plist, so an
# in-place overwrite can keep serving the old metadata.
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# --- Info.plist ---
# LSUIElement=1: no Dock icon, no focus stealing -- this is a "run and quit"
# launcher, the browser is the real UI.
cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>C-3PO</string>
  <key>CFBundleDisplayName</key>       <string>C-3PO</string>
  <key>CFBundleIdentifier</key>        <string>com.nunchuk.c3po</string>
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
# Thin shell: run the opener, surface failures in a dialog. A double-clicked app
# has no terminal, so a silent failure just looks like a broken app.
cat > "$APP/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# launchd hands us a C locale. Diagnostics are English-only so this is belt and
# braces, but any byte-wise processing (sed/tr) on non-ASCII would fail with
# "illegal byte sequence" -- and that would turn a success into a false error.
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Homebrew is not on PATH for GUI-launched apps; the opener needs ffmpeg.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"

OPENER="$OPENER"
if [ ! -x "\$OPENER" ]; then
  osascript -e 'display alert "C-3PO" message "c3po-open.sh not found -- did the repo move?" as critical'
  exit 1
fi

# Fixed path rather than \$TMPDIR: launchd's TMPDIR and AppleScript's "temporary
# items" folder are not necessarily the same directory.
log="\$HOME/.c3po-launch.log"
if "\$OPENER" >"\$log" 2>&1; then
  exit 0
fi

# Failed: show the script's own diagnostics (it explains missing .env, absent
# ffmpeg, occupied port, server crash). osascript reads the file itself instead
# of taking it through shell string interpolation -- no escaping needed, quotes
# in the message are safe.
osascript -e 'set p to (POSIX path of (path to home folder)) & ".c3po-launch.log"' \
          -e 'try' \
          -e '  set msg to (do shell script "tail -15 " & quoted form of p)' \
          -e 'on error' \
          -e '  set msg to "Failed to start, and the diagnostic log could not be read."' \
          -e 'end try' \
          -e 'display alert "C-3PO failed to start" message msg as critical' 2>/dev/null
exit 1
LAUNCHER
chmod +x "$APP/Contents/MacOS/launcher"

# --- icon ---
if [ -f "$ICNS" ]; then
  cp "$ICNS" "$APP/Contents/Resources/icon.icns"
else
  # No icon: drop the key so Finder shows the generic app icon rather than a
  # broken-image placeholder.
  /usr/libexec/PlistBuddy -c "Delete :CFBundleIconFile" "$APP/Contents/Info.plist" 2>/dev/null || true
  echo "WARNING: $ICNS missing; app will use the generic icon." >&2
fi

plutil -lint "$APP/Contents/Info.plist"

# Register immediately so Spotlight finds it without waiting for a reindex.
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
  -f "$APP" 2>/dev/null || true

echo "Created: $APP"
echo "  Double-click it, or search Spotlight for \"C-3PO\""
echo "  Uninstall: rm -rf \"$APP\""
