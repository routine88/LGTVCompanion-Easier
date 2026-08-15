#!/bin/sh
# Convenience wrapper: removing the app is a mode of the installer, but nobody
# looks for "install.sh" when they want it gone.
#
#   sh packaging/linux/uninstall.sh            keep my settings
#   sh packaging/linux/uninstall.sh --purge    delete them too
#   sh packaging/linux/uninstall.sh --system   remove a system-wide install (root)
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec sh "$DIR/install.sh" --uninstall "$@"
