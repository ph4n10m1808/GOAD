"""Host storage policy shared by local libvirt labs and extensions."""
import os
from pathlib import Path

DEFAULT_POOL_NAME = "GOAD"
DEFAULT_POOL_PATH = Path("/mnt/SSD_DATA/Virtualization/KVM/GOAD")
DEFAULT_MOUNT_ROOT = Path("/mnt/SSD_DATA")


def storage_mount_root():
    configured = os.environ.get("GOAD_STORAGE_MOUNT_ROOT")
    if configured is not None:
        return Path(configured.strip()) if configured.strip() else None
    if os.environ.get("GOAD_STORAGE_POOL_PATH", "").strip():
        return None
    return DEFAULT_MOUNT_ROOT


def storage_pool_for_lab(lab_name):
    pool_name = os.environ.get("GOAD_STORAGE_POOL", DEFAULT_POOL_NAME).strip() or DEFAULT_POOL_NAME
    pool_path = os.environ.get("GOAD_STORAGE_POOL_PATH", str(DEFAULT_POOL_PATH)).strip() or str(DEFAULT_POOL_PATH)
    return pool_name, Path(pool_path)
