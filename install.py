# installer for the weewx-ip100 driver
# Copyright 2016 Matthew Wall
# Distributed under the terms of the GNU Public License (GPLv3)

from setup import ExtensionInstaller

def loader():
    return IP100Installer()

class IP100Installer(ExtensionInstaller):
    def __init__(self):
        super(IP100Installer, self).__init__(
            version="0.2",
            name='ip100',
            description='Capture weather data from Rainwise IP-100',
            author="Matthew Wall",
            author_email="mwall@users.sourceforge.net",
            files=[('bin/user', ['bin/user/ip100.py'])]
            )
