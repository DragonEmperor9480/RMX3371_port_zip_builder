#!/usr/bin/env bash
# Build a flashable RMX3371 zip from the images in images/.
# Usage: ./build_zip.sh [output.zip]
set -euo pipefail

BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$BUILDER_DIR/images"
CONFIG_FILE="$BUILDER_DIR/rom.conf"
OUTPUT="$(realpath -m "${1:-$BUILDER_DIR/RMX3371_flash.zip}")"
SPARSE_MAGIC="3aff26ed"
REQUIRED=(
    my_bigball my_carrier my_engineering my_heytap my_manifest
    my_product my_region my_stock odm product system system_ext vendor
)

die() {
    echo "ERROR: $*" >&2
    exit 1
}

get_config() {
    sed -n "s/^$1=//p" "$CONFIG_FILE" | head -n 1 | tr -d '\r'
}

is_required() {
    local name
    for name in "${REQUIRED[@]}"; do
        [ "$name" = "$1" ] && return 0
    done
    return 1
}

command -v zip >/dev/null || die "zip is not installed"
command -v unzip >/dev/null || die "unzip is not installed"

[ -f "$CONFIG_FILE" ] || die "missing rom.conf"
ROM_NAME="$(get_config ROM_NAME)"
DEV_NAME="$(get_config DEV_NAME)"
[ -n "${ROM_NAME// /}" ] || die "ROM_NAME is empty in rom.conf"
[ -n "${DEV_NAME// /}" ] || die "DEV_NAME is empty in rom.conf"

for image in "$IMAGES_DIR"/*.img; do
    [ -e "$image" ] || break
    is_required "$(basename "$image" .img)" || die "unknown image: $(basename "$image")"
done

for name in "${REQUIRED[@]}"; do
    image="$IMAGES_DIR/$name.img"
    [ -f "$image" ] || die "missing image: $name.img"
    [ -s "$image" ] || die "empty image: $name.img"
    magic="$(od -A n -t x1 -N 4 "$image" | tr -d ' \n')"
    [ "$magic" != "$SPARSE_MAGIC" ] || die "$name.img is sparse, convert it with simg2img"
done

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

printf 'ROM: %s\nDeveloper: %s\n' "$ROM_NAME" "$DEV_NAME" > "$WORK_DIR/rom_info.txt"

echo "Calculating checksums..."
for name in "${REQUIRED[@]}"; do
    image="$IMAGES_DIR/$name.img"
    printf '%s %s %s\n' "$name" "$(stat -c %s "$image")" "$(sha256sum "$image" | cut -d' ' -f1)"
done > "$WORK_DIR/images.list"

chmod 755 "$BUILDER_DIR/META-INF/com/google/android/update-binary"
rm -f "$OUTPUT"

echo "Building $OUTPUT..."
cd "$BUILDER_DIR"
zip -q0r "$OUTPUT" META-INF
zip -q0j "$OUTPUT" "$WORK_DIR/images.list" "$WORK_DIR/rom_info.txt"
for name in "${REQUIRED[@]}"; do
    zip -q0 "$OUTPUT" "images/$name.img"
done

echo "Checking the zip..."
unzip -tq "$OUTPUT"

echo "Done: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
