#!/bin/sh
# watchersattherim installer. Run from a clone of this repo:
#
#   ./install.sh monitor      build the decoders (ft8mon, wsprd, wsprmon) and watr
#   ./install.sh collector    install watr only (no binaries, no audio/build deps)
#
# Optional settings are environment overrides, e.g.:
#   BIN_DIR=/usr/local/bin ./install.sh monitor
#
# Platform support grows by adding a profile_<platform>_deps function and a case
# in install_system_deps. Debian/Ubuntu (apt) is the first profile.

set -eu

BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
SRC_DIR="${SRC_DIR:-$HOME/.watchersattherim/src}"
VENV_DIR="${VENV_DIR:-$HOME/.watchersattherim/venv}"

FT8MON_REPO="${FT8MON_REPO:-https://github.com/simplyequipped/ft8mon.git}"
WSPRD_REPO="${WSPRD_REPO:-https://github.com/simplyequipped/wsprd.git}"
WSPRMON_REPO="${WSPRMON_REPO:-https://github.com/simplyequipped/wsprmon.git}"

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

usage() {
    cat <<EOF
usage: ./install.sh <role> [--config]

  monitor     build the decoders (ft8mon, wsprd, wsprmon) and install watr
  collector   install watr only (no binaries, no audio/build deps)

  --config    write a ready-to-edit config to ~/.watchersattherim[/collector]
              (for monitor, with the installed binary paths filled in)
  --service   install + enable a systemd service that starts at boot
              (implies --config; uses sudo)

optional environment overrides:
  BIN_DIR (default \$HOME/.local/bin), SRC_DIR, VENV_DIR,
  FT8MON_REPO, WSPRD_REPO, WSPRMON_REPO
EOF
}


# --- platform ---------------------------------------------------------------

detect_platform() {
    if [ -f /etc/debian_version ] || (have apt-get); then
        PLATFORM=debian
    else
        PLATFORM=unknown
    fi
}

profile_debian_deps() {
    common="git python3 python3-venv python3-pip"
    if [ "$ROLE" = monitor ]; then
        common="$common build-essential libfftw3-dev libsndfile1-dev portaudio19-dev"
    fi
    say "installing system packages (apt): $common"
    sudo apt-get update -qq
    # shellcheck disable=SC2086
    sudo apt-get install -y $common
}

install_system_deps() {
    case "$PLATFORM" in
        debian) profile_debian_deps ;;
        *) die "unsupported platform; install deps manually (git, python3-venv;
   monitor also: a C/C++ toolchain, fftw, libsndfile, portaudio) then re-run
   with a stubbed install_system_deps, or add a profile for your platform" ;;
    esac
}


# --- binaries (monitor role) ------------------------------------------------

clone_or_update() {  # repo_url  dest_dir
    if [ -d "$2/.git" ]; then
        say "updating $(basename "$2")"
        git -C "$2" pull --ff-only
    else
        say "cloning $(basename "$2")"
        git clone "$1" "$2"
    fi
}

build_into_bin() {  # src_dir  binary_name
    say "building $2"
    make -C "$1" >/dev/null
    install -d "$BIN_DIR"
    install -m 0755 "$1/$2" "$BIN_DIR/$2"
}

build_binaries() {
    install -d "$SRC_DIR"
    clone_or_update "$FT8MON_REPO"  "$SRC_DIR/ft8mon"
    clone_or_update "$WSPRD_REPO"   "$SRC_DIR/wsprd"
    clone_or_update "$WSPRMON_REPO" "$SRC_DIR/wsprmon"
    build_into_bin "$SRC_DIR/ft8mon"  ft8mon
    build_into_bin "$SRC_DIR/wsprd"   wsprd
    build_into_bin "$SRC_DIR/wsprmon" wsprmon
}


# --- watr -------------------------------------------------------------------

install_watr() {
    say "creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
    say "installing watr"
    "$VENV_DIR/bin/pip" install --upgrade pip >/dev/null
    "$VENV_DIR/bin/pip" install "$REPO_ROOT"
}


# --- config (--config) ------------------------------------------------------

install_config() {
    case "$ROLE" in
        monitor)   cfg_dir="$HOME/.watchersattherim" ;;
        collector) cfg_dir="$HOME/.watchersattherim/collector" ;;
    esac
    CONFIG_PATH="$cfg_dir/$ROLE.full.ini"
    install -d "$cfg_dir"
    if [ -e "$CONFIG_PATH" ]; then
        say "config already exists, leaving it: $CONFIG_PATH"
        return
    fi
    cp "$REPO_ROOT/examples/$ROLE.full.example.ini" "$CONFIG_PATH"
    if [ "$ROLE" = monitor ]; then
        # point the commented-default binary paths at the installed binaries
        sed -i \
            -e "s|^# path = ft8mon.*|path = $BIN_DIR/ft8mon|" \
            -e "s|^# path = wsprmon.*|path = $BIN_DIR/wsprmon|" \
            -e "s|^# wsprd_path = wsprd.*|wsprd_path = $BIN_DIR/wsprd|" \
            "$CONFIG_PATH"
    fi
    say "config written: $CONFIG_PATH"
}


# --- service (--service) ----------------------------------------------------

install_service() {
    svc="watchersattherim-$ROLE.service"
    dest="/etc/systemd/system/$svc"
    if [ -e "$dest" ]; then
        say "service already exists, leaving it: $dest"
        return
    fi
    tmp="$(mktemp)"
    sed \
        -e "s|__USER__|$(id -un)|g" \
        -e "s|__VENV__|$VENV_DIR|g" \
        -e "s|__CONFIG__|$CONFIG_PATH|g" \
        "$REPO_ROOT/examples/$svc" > "$tmp"
    sudo install -m 0644 "$tmp" "$dest"
    rm -f "$tmp"
    say "service installed: $dest"
    if have systemctl; then
        sudo systemctl daemon-reload || true
        if sudo systemctl enable --now "$svc"; then
            say "service enabled and started (starts on boot)"
        else
            say "could not enable; run: sudo systemctl enable --now $svc"
        fi
    else
        say "systemctl not found; enable the service manually"
    fi
}


# --- main -------------------------------------------------------------------

case "${1:-}" in
    monitor|collector) ROLE="$1" ;;
    -h|--help) usage; exit 0 ;;
    "")  echo "error: missing role" >&2; echo >&2; usage >&2; exit 2 ;;
    *)   echo "error: unknown role '$1'" >&2; echo >&2; usage >&2; exit 2 ;;
esac
shift
WANT_CONFIG=0
WANT_SERVICE=0
for arg in "$@"; do
    case "$arg" in
        --config)  WANT_CONFIG=1 ;;
        --service) WANT_SERVICE=1; WANT_CONFIG=1 ;;
        *) echo "error: unknown option '$arg'" >&2; echo >&2; usage >&2; exit 2 ;;
    esac
done

detect_platform
install_system_deps
if [ "$ROLE" = monitor ]; then build_binaries; fi
install_watr
if [ "$WANT_CONFIG" = 1 ]; then install_config; fi
if [ "$WANT_SERVICE" = 1 ]; then install_service; fi

say "done ($ROLE)"
echo
if [ "$ROLE" = monitor ]; then
    if [ "$WANT_CONFIG" = 1 ]; then
        echo "  binaries:  $BIN_DIR    (paths set in the config)"
    else
        echo "  binaries:  $BIN_DIR    (ensure it is on PATH)"
    fi
fi
echo "  commands:  $VENV_DIR/bin/    (watr-$ROLE, watr-query, watr-propagation)"
if [ "$WANT_CONFIG" = 1 ]; then
    if [ "$WANT_SERVICE" = 1 ]; then
        echo "  config:    $CONFIG_PATH    (edit it, then: sudo systemctl restart watchersattherim-$ROLE)"
    else
        echo "  config:    $CONFIG_PATH    (edit it, then: watr-$ROLE -c $CONFIG_PATH)"
    fi
else
    echo "  config:    copy examples/${ROLE}.full.example.ini and edit it"
fi
if [ "$WANT_SERVICE" = 1 ]; then
    echo "  service:   systemctl status watchersattherim-$ROLE"
fi
