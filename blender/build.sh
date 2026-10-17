#!/bin/sh
# Build the Blender extension zip and install it into the user's Blender.
D=$(cd "$(dirname "$0")" && pwd)
rm -rf "$D/io_mksf/__pycache__"
mkdir -p "$D/dist"
blender --command extension build --source-dir "$D/io_mksf" --output-dir "$D/dist" | grep -E "created|error"
blender --command extension install-file -r user_default -e "$(ls -t "$D"/dist/io_mksf-*.zip | head -1)" | grep -iE "installed|error"
