#!/usr/bin/env python3
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert an unpacked VirtualBox Vagrant box to a local libvirt box."
    )
    parser.add_argument("box", help="Vagrant box name, for example StefanScherer/windows_2019")
    parser.add_argument("--version", required=True, help="Exact cached box version")
    parser.add_argument("--vagrant-home", type=Path, required=True)
    return parser.parse_args()


def safe_box_name(name):
    return name.replace("/", "-VAGRANTSLASH-")


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def child_text(element, name):
    for child in element:
        if local_name(child.tag) == name:
            return child.text
    return None


def find_virtualbox_disk(source_dir):
    ovf_path = source_dir / "box.ovf"
    if not ovf_path.is_file():
        raise RuntimeError(f"VirtualBox OVF not found: {ovf_path}")

    root = ET.parse(ovf_path).getroot()
    href = None
    for element in root.iter():
        if local_name(element.tag) != "File":
            continue
        for attribute, value in element.attrib.items():
            if local_name(attribute) == "href":
                href = value
                break
        if href:
            break
    if not href:
        raise RuntimeError(f"Cannot locate the VMDK reference in {ovf_path}")

    controllers = {}
    disk_parent = None
    for item in root.iter():
        if local_name(item.tag) != "Item":
            continue
        resource_type = child_text(item, "ResourceType")
        instance_id = child_text(item, "InstanceID")
        if resource_type == "5" and instance_id:
            controllers[instance_id] = "ide"
        elif resource_type == "6" and instance_id:
            controllers[instance_id] = "scsi"
        elif resource_type == "20" and instance_id:
            controllers[instance_id] = "sata"
        elif resource_type == "17":
            disk_parent = child_text(item, "Parent")

    disk_bus = controllers.get(disk_parent)
    if disk_bus is None:
        raise RuntimeError(f"Cannot determine the VirtualBox disk controller in {ovf_path}")

    if Path(href).is_absolute():
        raise RuntimeError(f"OVF disk reference must be relative: {href}")
    disk_path = (source_dir / href).resolve()
    try:
        disk_path.relative_to(source_dir.resolve())
    except ValueError:
        raise RuntimeError(f"OVF disk reference escapes the box directory: {href}") from None
    if not disk_path.is_file():
        raise RuntimeError(f"VMDK referenced by the OVF does not exist: {disk_path}")
    return disk_path, disk_bus


def virtual_size_gb(image_path):
    result = subprocess.run(
        ["qemu-img", "info", "--output=json", str(image_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    info = json.loads(result.stdout)
    return math.ceil(int(info["virtual-size"]) / (1024 ** 3))


def write_box_vagrantfile(vagrantfile_path):
    vagrantfile_path.write_text(
        'Vagrant.configure("2") do |config|\n'
        '  # The generated lab Vagrantfile selects the disk bus per guest/state.\n'
        'end\n',
        encoding="utf-8",
    )


def existing_conversion_is_valid(output_image, metadata_path, vagrantfile_path):
    if not all(path.is_file() for path in (output_image, metadata_path, vagrantfile_path)):
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if (metadata.get("provider") != "libvirt"
                or metadata.get("format") != "qcow2"
                or not isinstance(metadata.get("virtual_size"), int)
                or metadata["virtual_size"] < 1):
            return False
        info = subprocess.run(
            ["qemu-img", "info", "--output=json", str(output_image)],
            check=True,
            capture_output=True,
            text=True,
        )
        if json.loads(info.stdout).get("format") != "qcow2":
            return False
        subprocess.run(
            ["qemu-img", "check", "-q", str(output_image)],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError, TypeError, subprocess.CalledProcessError):
        return False
    return True


def convert_box(box_name, version, vagrant_home):
    box_root = vagrant_home / "boxes" / safe_box_name(box_name) / version
    source_dir = box_root / "virtualbox"
    output_dir = box_root / "libvirt"
    output_image = output_dir / "box.img"
    metadata_path = output_dir / "metadata.json"
    vagrantfile_path = output_dir / "Vagrantfile"

    existing_files = (output_image, metadata_path, vagrantfile_path)
    if all(path.is_file() for path in existing_files):
        if existing_conversion_is_valid(*existing_files):
            write_box_vagrantfile(vagrantfile_path)
            print(f"Already converted: {box_name} ({version}, libvirt)")
            return
        raise RuntimeError(
            f"Existing libvirt box failed validation: {output_dir}. "
            "Stop dependent guests and inspect or remove this provider directory before rebuilding."
        )
    if output_dir.exists():
        print(f"Existing libvirt box is incomplete or invalid; rebuilding: {box_name} ({version})")
    if not source_dir.is_dir():
        raise RuntimeError(f"Cached VirtualBox provider not found: {source_dir}")

    source_image, disk_bus = find_virtualbox_disk(source_dir)
    size_gb = virtual_size_gb(source_image)
    output_dir.mkdir(parents=True, exist_ok=True)
    partial_image = output_dir / "box.img.partial"

    if partial_image.exists():
        partial_image.unlink()

    print(f"Converting {source_image} -> {output_image}")
    print(f"Detected disk bus: {disk_bus}; virtual size: {size_gb} GB")
    try:
        subprocess.run(
            [
                "qemu-img", "convert", "-p", "-S", "16k",
                "-O", "qcow2", "-o", "compat=1.1",
                str(source_image), str(partial_image),
            ],
            check=True,
        )
        os.replace(partial_image, output_image)
    except BaseException:
        if partial_image.exists():
            partial_image.unlink()
        raise

    metadata = {
        "provider": "libvirt",
        "format": "qcow2",
        "virtual_size": size_gb,
    }
    temporary_metadata = metadata_path.with_suffix(".json.partial")
    temporary_metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary_metadata, metadata_path)
    write_box_vagrantfile(vagrantfile_path)
    print(f"Created local libvirt box: {box_name} ({version})")


def main():
    args = parse_args()
    try:
        convert_box(args.box, args.version, args.vagrant_home.expanduser().resolve())
    except (OSError, RuntimeError, subprocess.CalledProcessError, ValueError, ET.ParseError) as error:
        print(f"Conversion failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
