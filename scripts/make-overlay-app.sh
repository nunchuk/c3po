#!/usr/bin/env bash
# Build "C-3PO.app" and "C-3PO Quit.app" -- double-click (or Spotlight) to start
# and stop c3po without a terminal.
#
#   bash scripts/make-overlay-app.sh              # install to ~/Applications (user-level)
#   bash scripts/make-overlay-app.sh /Applications
#
# C-3PO.app is a native window (macos/C3PO.swift, compiled here with swiftc): it
# starts the server via `c3po-open.sh --serve`, shows startup progress and hosts
# the page. Changing macos/C3PO.swift needs a rebuild; server behaviour and the
# preflight checks live in c3po-open.sh and do not.
#
# C-3PO Quit.app is a thin wrapper around c3po-quit.sh: the launcher execs it and
# shows a dialog if it fails. Edit the script, no rebuild needed.
#
# Uninstall: rm -rf "~/Applications/C-3PO.app" "~/Applications/C-3PO Quit.app"
set -euo pipefail

DEST="${1:-$HOME/Applications}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ICNS="$REPO/assets/c3po.icns"
PORT="${C3PO_PORT:-8989}"

for s in c3po-open.sh c3po-quit.sh; do
  [ -x "$REPO/scripts/$s" ] || { echo "ERROR: not found or not executable: $REPO/scripts/$s" >&2; exit 1; }
done
command -v swiftc >/dev/null 2>&1 || {
  echo "ERROR: swiftc not found. Install the Command Line Tools: xcode-select --install" >&2; exit 1; }

# Build the icon on demand so a fresh clone gets one without a separate step.
if [ ! -f "$ICNS" ] && [ -x "$REPO/.venv/bin/python" ]; then
  echo "Building icon..."
  "$REPO/.venv/bin/python" "$REPO/scripts/make-icon.py" || true
fi

mkdir -p "$DEST"

# new_bundle <app-path>: start from an empty bundle. Remove any existing one
# first: LaunchServices caches Info.plist, so an in-place overwrite can keep
# serving the old metadata.
new_bundle() {
  rm -rf "$1"
  mkdir -p "$1/Contents/MacOS" "$1/Contents/Resources"
}

# write_plist <app-path> <name> <bundle-id> <executable> <extra-keys>
write_plist() {
  cat > "$1/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>$2</string>
  <key>CFBundleDisplayName</key>       <string>$2</string>
  <key>CFBundleIdentifier</key>        <string>$3</string>
  <key>CFBundleVersion</key>           <string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleExecutable</key>        <string>$4</string>
  <key>CFBundleIconFile</key>          <string>icon</string>
  <key>NSHighResolutionCapable</key>   <true/>
$5
</dict>
</plist>
PLIST
}

# finish_bundle <app-path> <name>: icon, lint, register with LaunchServices.
finish_bundle() {
  if [ -f "$ICNS" ]; then
    cp "$ICNS" "$1/Contents/Resources/icon.icns"
  else
    # No icon: drop the key so Finder shows the generic app icon rather than a
    # broken-image placeholder.
    /usr/libexec/PlistBuddy -c "Delete :CFBundleIconFile" "$1/Contents/Info.plist" 2>/dev/null || true
    echo "WARNING: $ICNS missing; $2 will use the generic icon." >&2
  fi

  plutil -lint "$1/Contents/Info.plist"

  # Register immediately so Spotlight finds it without waiting for a reindex.
  /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$1" 2>/dev/null || true

  echo "Created: $1"
}

# ---------- C-3PO.app: native window ----------
# Compile before touching the installed app, so a compile error leaves the
# previous build in place instead of an empty bundle.
echo "Compiling macos/C3PO.swift..."
build_dir="$(mktemp -d)"
swiftc -O -o "$build_dir/C-3PO" "$REPO/macos/C3PO.swift"
app="$DEST/C-3PO.app"
new_bundle "$app"
mv "$build_dir/C-3PO" "$app/Contents/MacOS/C-3PO"
rmdir "$build_dir"

# A regular app (Dock icon, menu bar), unlike the old LSUIElement launcher.
# NSMicrophoneUsageDescription: the page records meeting audio from BlackHole;
# without the key macOS refuses the microphone outright instead of asking.
# C3PORepo / C3POPort tell the binary where c3po-open.sh is and what to probe.
write_plist "$app" "C-3PO" "com.nunchuk.c3po" "C-3PO" "\
  <key>LSApplicationCategoryType</key> <string>public.app-category.productivity</string>
  <key>NSMicrophoneUsageDescription</key>
  <string>C-3PO transcribes meeting audio captured from BlackHole.</string>
  <key>C3PORepo</key>                  <string>$REPO</string>
  <key>C3POPort</key>                  <string>$PORT</string>"
finish_bundle "$app" "C-3PO"

# Ad-hoc signature, so the microphone grant has a stable code identity to
# attach to. The identity is the code hash: after a rebuild that changes the
# binary, macOS asks for the microphone again.
codesign --force --sign - "$app"

# ---------- C-3PO Quit.app: shell launcher ----------
app="$DEST/C-3PO Quit.app"
script="$REPO/scripts/c3po-quit.sh"
logname=".c3po-quit.log"
title="C-3PO failed to stop"
new_bundle "$app"
# LSUIElement=1: no Dock icon, no focus stealing -- a "run and quit" launcher.
write_plist "$app" "C-3PO Quit" "com.nunchuk.c3po.quit" "launcher" "\
  <key>LSUIElement</key>               <true/>"

# Thin shell: run the script, surface failures in a dialog. A double-clicked
# app has no terminal, so a silent failure just looks like a broken app.
cat > "$app/Contents/MacOS/launcher" <<LAUNCHER
#!/bin/bash
# launchd hands us a C locale. Diagnostics are English-only so this is belt and
# braces, but any byte-wise processing (sed/tr) on non-ASCII would fail with
# "illegal byte sequence" -- and that would turn a success into a false error.
export LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8

# Homebrew is not on PATH for GUI-launched apps; the script needs lsof.
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
finish_bundle "$app" "C-3PO Quit"

cat <<EOF

  Double-click them, or search Spotlight for "C-3PO"
  Uninstall: rm -rf "$DEST/C-3PO.app" "$DEST/C-3PO Quit.app"
EOF
