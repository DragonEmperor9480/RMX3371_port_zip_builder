#!/usr/bin/env bash
# Build a flashable RMX3371 zip from the images in images/.
# Usage: ./build_zip.sh [output.zip]
set -euo pipefail

BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_DIR="$BUILDER_DIR/images"
FIRMWARE_DIR="$BUILDER_DIR/firmware"
CONFIG_FILE="$BUILDER_DIR/rom.conf"
SPARSE_MAGIC="3aff26ed"
BANNER_WIDTH=30
REQUIRED=(
    my_bigball my_carrier my_engineering my_heytap my_manifest
    my_product my_region my_stock odm product system system_ext vendor
)
FIRMWARE=(
    abl aop bluetooth boot cmnlib cmnlib64 devcfg dtbo featenabler hyp
    imagefv keymaster logo modem multiimgoem oplusstanvbk qupfw splash
    spunvm storsec tz uefisecapp vbmeta_system vbmeta_vendor xbl xbl_config
)

die() {
    echo "ERROR: $*" >&2
    exit 1
}

center() {
    local pad=$(( (BANNER_WIDTH - ${#1}) / 2 ))
    [ "$pad" -gt 0 ] || pad=0
    printf '%*s%s\n' "$pad" "" "$1"
}

get_config() {
    sed -n "s/^$1=//p" "$CONFIG_FILE" | head -n 1 | tr -d '\r' | sed -E 's/^ +//; s/ +$//'
}

check_images() {
    local dir="$1" name image magic
    shift

    for image in "$dir"/*.img; do
        [ -e "$image" ] || break
        name="$(basename "$image" .img)"
        [[ " $* " == *" $name "* ]] || die "unknown image: $(basename "$dir")/$name.img"
    done

    for name in "$@"; do
        image="$dir/$name.img"
        [ -f "$image" ] || die "missing image: $(basename "$dir")/$name.img"
        [ -s "$image" ] || die "empty image: $(basename "$dir")/$name.img"
        magic="$(od -A n -t x1 -N 4 "$image" | tr -d ' \n')"
        [ "$magic" != "$SPARSE_MAGIC" ] || die "$name.img is sparse, convert it with simg2img"
    done
}

write_list() {
    local dir="$1" name image
    shift

    for name in "$@"; do
        image="$dir/$name.img"
        printf '%s %s %s\n' "$name" "$(stat -c %s "$image")" "$(sha256sum "$image" | cut -d' ' -f1)"
    done
}

command -v zip >/dev/null || die "zip is not installed"
command -v unzip >/dev/null || die "unzip is not installed"

[ -f "$CONFIG_FILE" ] || die "missing rom.conf"
ROM_NAME="$(get_config ROM_NAME)"
DEVICE_NAME="$(get_config DEVICE_NAME)"
DEV_NAME="$(get_config DEV_NAME)"
[ -n "${ROM_NAME// /}" ] || die "ROM_NAME is empty in rom.conf"
[ -n "${DEVICE_NAME// /}" ] || die "DEVICE_NAME is empty in rom.conf"
[ -n "${DEV_NAME// /}" ] || die "DEV_NAME is empty in rom.conf"

ZIP_NAME="${ROM_NAME// /}_${DEVICE_NAME// /}_BY_${DEV_NAME// /}.zip"
[[ "$ZIP_NAME" != */* ]] || die "names in rom.conf cannot contain /"
OUTPUT="$(realpath -m "${1:-$BUILDER_DIR/$ZIP_NAME}")"

check_images "$IMAGES_DIR" "${REQUIRED[@]}"
check_images "$FIRMWARE_DIR" "${FIRMWARE[@]}"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT
mkdir "$WORK_DIR/images" "$WORK_DIR/firmware"

RULE="$(printf '=%.0s' $(seq 1 "$BANNER_WIDTH"))"
{
    echo "$RULE"
    center "$ROM_NAME"
    center "by $DEV_NAME"
    echo "$RULE"
} > "$WORK_DIR/rom_info.txt"

echo "Calculating checksums..."
write_list "$IMAGES_DIR" "${REQUIRED[@]}" > "$WORK_DIR/images.list"
write_list "$FIRMWARE_DIR" "${FIRMWARE[@]}" > "$WORK_DIR/firmware.list"

chmod 755 "$BUILDER_DIR/META-INF/com/google/android/update-binary"
rm -f "$OUTPUT"

# Zip everything in one call: each extra zip call rewrites the whole archive.
ln -s "$BUILDER_DIR/META-INF" "$WORK_DIR/META-INF"
for name in "${REQUIRED[@]}"; do
    ln -s "$IMAGES_DIR/$name.img" "$WORK_DIR/images/$name.img"
done
for name in "${FIRMWARE[@]}"; do
    ln -s "$FIRMWARE_DIR/$name.img" "$WORK_DIR/firmware/$name.img"
done

echo "Building $OUTPUT..."
cd "$WORK_DIR"
zip -0r "$OUTPUT" .

echo "Checking the zip..."
unzip -tq "$OUTPUT"

echo "Done: $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
