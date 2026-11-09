#!/usr/bin/env python3
"""Build a payload.bin zip for RMX3371 from the images in images/ and firmware/.

Usage: ./build_payload.py [output.zip]

The payload is a full update signed with keys/payload_key.pem, which is created
on the first build. The zip carries the matching certificate, and the installer
swaps it into recovery's otacerts.zip in RAM, so update_engine accepts the payload.
Group name and size below come from the phone's super metadata.
"""
import base64
import hashlib
import io
import os
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

BUILDER_DIR = Path(__file__).resolve().parent
IMAGES_DIR = BUILDER_DIR / "images"
FIRMWARE_DIR = BUILDER_DIR / "firmware"
INSTALLER = BUILDER_DIR / "payload_installer" / "update-binary"
UPDATER_SCRIPT = BUILDER_DIR / "META-INF" / "com" / "google" / "android" / "updater-script"
CONFIG_FILE = BUILDER_DIR / "rom.conf"
KEY_FILE = BUILDER_DIR / "keys" / "payload_key.pem"
CERT_FILE = BUILDER_DIR / "keys" / "payload_key.x509.pem"

SPARSE_MAGIC = bytes.fromhex("3aff26ed")
BANNER_WIDTH = 30
BLOCK_SIZE = 4096
CHUNK_SIZE = 2 * 1024 * 1024
SIGNATURE_SIZE = 256
GROUP_NAME = "qti_dynamic_partitions_b"
GROUP_SIZE = 11270094848

ROM_IMAGES = [
    "my_bigball", "my_carrier", "my_company", "my_engineering", "my_heytap",
    "my_manifest", "my_preload", "my_product", "my_region", "my_stock",
    "odm", "product", "system", "system_ext", "vendor",
]
# splash and oplusstanvbk have no _a/_b partitions, so a payload cannot hold them.
FIRMWARE_IMAGES = [
    "abl", "aop", "bluetooth", "boot", "cmnlib", "cmnlib64", "devcfg", "dtbo",
    "featenabler", "hyp", "imagefv", "keymaster", "logo", "modem", "multiimgoem",
    "qupfw", "spunvm", "storsec", "tz", "uefisecapp", "vbmeta_system",
    "vbmeta_vendor", "xbl", "xbl_config",
]

OP_REPLACE = 0


def die(message):
    sys.exit(f"ERROR: {message}")


def varint(value):
    out = bytearray()
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def pb_int(field, value):
    return varint(field << 3) + varint(value)


def pb_bytes(field, value):
    return varint((field << 3) | 2) + varint(len(value)) + value


def get_config(name):
    for line in CONFIG_FILE.read_text().splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip()
    return ""


def check_image(path):
    if not path.is_file():
        die(f"missing image: {path.parent.name}/{path.name}")
    if path.stat().st_size == 0:
        die(f"empty image: {path.parent.name}/{path.name}")
    with path.open("rb") as image:
        if image.read(4) == SPARSE_MAGIC:
            die(f"{path.name} is sparse, convert it with simg2img")


def add_partition(name, path, data_file):
    """Copy one image into data_file and return its PartitionUpdate message."""
    image_hash = hashlib.sha256()
    operations = bytearray()
    start_block = 0
    with path.open("rb") as image:
        while chunk := image.read(CHUNK_SIZE):
            if len(chunk) % BLOCK_SIZE:
                chunk += bytes(BLOCK_SIZE - len(chunk) % BLOCK_SIZE)
            image_hash.update(chunk)
            num_blocks = len(chunk) // BLOCK_SIZE
            extent = pb_int(1, start_block) + pb_int(2, num_blocks)
            operation = (pb_int(1, OP_REPLACE) + pb_int(2, data_file.tell()) +
                         pb_int(3, len(chunk)) + pb_bytes(6, extent) +
                         pb_bytes(8, hashlib.sha256(chunk).digest()))
            operations += pb_bytes(8, operation)
            data_file.write(chunk)
            start_block += num_blocks

    info = pb_int(1, start_block * BLOCK_SIZE) + pb_bytes(2, image_hash.digest())
    return pb_bytes(1, name.encode()) + pb_bytes(7, info) + operations


def ensure_key():
    if KEY_FILE.is_file() and CERT_FILE.is_file():
        return
    print("Creating signing key...", flush=True)
    KEY_FILE.parent.mkdir(exist_ok=True)
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
                    "-days", "10950", "-subj", "/CN=RMX3371 port", "-keyout", KEY_FILE,
                    "-out", CERT_FILE], check=True, capture_output=True)


def sign(digest):
    result = subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", KEY_FILE,
                             "-pkeyopt", "digest:sha256"],
                            input=digest, check=True, capture_output=True)
    if len(result.stdout) != SIGNATURE_SIZE:
        die("signing key must be RSA 2048")
    return result.stdout


def signature_message(signature):
    unpadded_size = varint((3 << 3) | 5) + struct.pack("<I", len(signature))
    return pb_bytes(1, pb_bytes(2, signature) + unpadded_size)


def otacerts_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.write(CERT_FILE, CERT_FILE.name)
    return buffer.getvalue()


def build_manifest(partitions, data_size):
    group = pb_bytes(1, GROUP_NAME.encode()) + pb_int(2, GROUP_SIZE)
    for name in ROM_IMAGES:
        group += pb_bytes(3, name.encode())
    dynamic = pb_bytes(1, group) + pb_int(2, 1)

    signatures_size = len(signature_message(bytes(SIGNATURE_SIZE)))
    manifest = bytearray(pb_int(3, BLOCK_SIZE) + pb_int(4, data_size) +
                         pb_int(5, signatures_size) + pb_int(12, 0))
    for partition in partitions:
        manifest += pb_bytes(13, partition)
    manifest += pb_int(14, int(time.time())) + pb_bytes(15, dynamic)
    return bytes(manifest)


def centered(text):
    return " " * max((BANNER_WIDTH - len(text)) // 2, 0) + text


def write_zip(output, manifest, data_path, rom_info):
    metadata = (b"CrAU" + struct.pack(">QQI", 2, len(manifest),
                                      len(signature_message(bytes(SIGNATURE_SIZE)))) + manifest)
    metadata_hash = hashlib.sha256(metadata).digest()
    metadata_signature = signature_message(sign(metadata_hash))
    data_size = data_path.stat().st_size
    payload_size = len(metadata) + len(metadata_signature) * 2 + data_size
    payload_hash = hashlib.sha256(metadata + metadata_signature)
    # The signed hash leaves out both signature blocks.
    signed_hash = hashlib.sha256(metadata)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.write(INSTALLER, "META-INF/com/google/android/update-binary")
        archive.write(UPDATER_SCRIPT, "META-INF/com/google/android/updater-script")
        archive.writestr("rom_info.txt", rom_info)
        archive.writestr("otacerts.zip", otacerts_zip())

        info = zipfile.ZipInfo("payload.bin", time.localtime()[:6])
        info.compress_type = zipfile.ZIP_STORED
        info.file_size = payload_size
        with archive.open(info, "w", force_zip64=True) as payload:
            payload.write(metadata)
            payload.write(metadata_signature)
            with data_path.open("rb") as data:
                while block := data.read(CHUNK_SIZE):
                    payload_hash.update(block)
                    signed_hash.update(block)
                    payload.write(block)
            payload_signature = signature_message(sign(signed_hash.digest()))
            payload_hash.update(payload_signature)
            payload.write(payload_signature)

        # Recovery needs the byte offset of the stored payload inside the zip.
        with output.open("rb") as raw:
            raw.seek(info.header_offset + 26)
            name_length, extra_length = struct.unpack("<HH", raw.read(4))
        offset = info.header_offset + 30 + name_length + extra_length
        archive.writestr("payload_offset.txt", f"{offset}\n")

        archive.writestr("payload_properties.txt", (
            f"FILE_HASH={base64.b64encode(payload_hash.digest()).decode()}\n"
            f"FILE_SIZE={payload_size}\n"
            f"METADATA_HASH={base64.b64encode(metadata_hash).decode()}\n"
            f"METADATA_SIZE={len(metadata)}\n"
        ))


def main():
    if not CONFIG_FILE.is_file():
        die("missing rom.conf")
    if not INSTALLER.is_file():
        die("missing payload_installer/update-binary")
    rom_name = get_config("ROM_NAME")
    device_name = get_config("DEVICE_NAME")
    dev_name = get_config("DEV_NAME")
    if not (rom_name and device_name and dev_name):
        die("ROM_NAME, DEVICE_NAME and DEV_NAME must be set in rom.conf")

    name = f"{rom_name}_{device_name}_BY_{dev_name}_PAYLOAD.zip".replace(" ", "")
    if "/" in name:
        die("names in rom.conf cannot contain /")
    output = Path(sys.argv[1] if len(sys.argv) > 1 else BUILDER_DIR / name).resolve()

    images = [(n, FIRMWARE_DIR / f"{n}.img") for n in FIRMWARE_IMAGES]
    images += [(n, IMAGES_DIR / f"{n}.img") for n in ROM_IMAGES]
    for _, path in images:
        check_image(path)

    ensure_key()

    rule = "=" * BANNER_WIDTH
    rom_info = f"{rule}\n{centered(rom_name)}\n{centered('by ' + dev_name)}\n{rule}\n"

    with tempfile.TemporaryDirectory(dir=output.parent) as work_dir:
        data_path = Path(work_dir) / "payload.data"
        partitions = []
        with data_path.open("wb") as data_file:
            for index, (partition, path) in enumerate(images, 1):
                print(f"[{index}/{len(images)}] {partition}", flush=True)
                partitions.append(add_partition(partition, path, data_file))

        print(f"Writing {output}...", flush=True)
        output.unlink(missing_ok=True)
        manifest = build_manifest(partitions, data_path.stat().st_size)
        write_zip(output, manifest, data_path, rom_info)

    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            die("the zip failed its check")
    print(f"Done: {output} ({os.path.getsize(output) / 1024 ** 3:.2f} GB)")


if __name__ == "__main__":
    main()
