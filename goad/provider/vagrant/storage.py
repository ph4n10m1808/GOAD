"""Fixed host storage policy for local libvirt labs."""
from pathlib import Path


def storage_pool_for_lab(lab_name):
    if lab_name == "GOAD-Light":
        return "GOAD-Light", Path("/mnt/SSD_DATA_1/Virtualization/KVM/GOAD-Light")
    return "GOAD", Path("/mnt/SSD_DATA/Virtualization/KVM/GOAD")
