from cloudinit.net import get_devicelist

def test_sysfs():
    assert not get_devicelist()
