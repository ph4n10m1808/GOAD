"""Fixed host storage policy shared by local libvirt labs and extensions."""
from pathlib import Path


def storage_pool_for_lab(lab_name):
    return "GOAD", Path("/mnt/SSD_DATA/Virtualization/KVM/GOAD")
