# Written by Ivan Sapozhkov and Denis Chagin <denis.chagin@emlid.com>
#
# Copyright (c) 2016, Emlid Limited
# All rights reserved.
#
# Redistribution and use in source and binary forms,
# with or without modification,
# are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
# this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
# this list of conditions and the following disclaimer in the documentation
# and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its contributors
# may be used to endorse or promote products derived from this software
# without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO,
# THE IMPLIED WARRANTIES OF MERCHANTABILITY AND
# FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS
# BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY,
# OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO,
# PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE,
# EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


from wificontrol import HostAP
from wificontrol import WiFi
from wificontrol import WpaSupplicant
from wificontrol.utils import PropertyError


class WiFiControl(object):
    CLIENT_STATE = 'wpa_supplicant'
    HOTSPOT_STATE = 'hostapd'
    OFF_STATE = 'wifi_off'

    def __init__(self, interface='wlan0',
                 wpas_config="/etc/wpa_supplicant/wpa_supplicant.conf",
                 p2p_config="/etc/wpa_supplicant/p2p_supplicant.conf",
                 hostapd_config="/etc/hostapd/hostapd.conf",
                 hostname_config='/etc/hostname'):

        self.wifi = WiFi(interface)
        self.wpa_supplicant = WpaSupplicant(interface, wpas_config, p2p_config)
        self.hotspot = HostAP(interface, hostapd_config, hostname_config)

    def start_hotspot_mode(self):
        if self.wpa_supplicant.started():
            self.wpa_supplicant.stop()
        if self.hotspot.started():
            self.hotspot.restart()
        else:
            self.hotspot.start()
        return True

    def start_client_mode(self):
        if self.hotspot.started():
            self.hotspot.stop()
        if self.wpa_supplicant.started():
            self.wpa_supplicant.restart()
        else:
            self.wpa_supplicant.start()
        return True

    def turn_on_wifi(self):
        if self.get_state() == self.OFF_STATE:
            self.wifi.unblock()
            self.wpa_supplicant.start()

    def turn_off_wifi(self):
        self.hotspot.stop()
        self.wpa_supplicant.stop()
        self.wifi.block()

    def get_wifi_turned_on(self):
        return self.wpa_supplicant.started() or self.hotspot.started()

    def set_hotspot_password(self, password):
        return self.hotspot.set_hotspot_password(password)

    def get_device_name(self):
        return self.hotspot.get_host_name()

    def get_hotspot_name(self):
        return self.hotspot.get_hotspot_ssid()

    def set_device_names(self, name):
        self.wpa_supplicant.set_p2p_name(name)
        self.hotspot.set_hotspot_ssid(name)
        self.hotspot.set_host_name(name)
        self.wifi.restart_dns()
        return self.verify_device_names(name)

    def verify_hotspot_name(self, name):
        mac_addr = self.hotspot.get_device_mac()[-6:]
        return "{}{}".format(name, mac_addr) == self.hotspot.get_hotspot_ssid()

    def verify_device_names(self, name):
        verified = False
        if name == self.hotspot.get_host_name():
            if name == self.wpa_supplicant.get_p2p_name():
                if self.verify_hotspot_name(name):
                    verified = True
        return verified

    def get_status(self):
        state = self.get_state()
        status = None

        if state == self.CLIENT_STATE:
            try:
                status = self.wpa_supplicant.get_status()
            except PropertyError:
                return state, status
        elif state == self.HOTSPOT_STATE:
            try:
                status = self.hotspot.get_hotspot_ssid()
            except PropertyError:
                return state, status

        return state, status

    def get_added_networks(self):
        return self.wpa_supplicant.get_added_networks()

    def get_ip(self):
        return self.wifi.get_device_ip()

    def scan(self):
        self.wpa_supplicant.scan()

    def get_scan_results(self):
        return self.wpa_supplicant.get_scan_results()

    def add_network(self, network_parameters):
        self.wpa_supplicant.add_network(network_parameters)

    def remove_network(self, network):
        self.wpa_supplicant.remove_network(network)

    def start_connecting(self, network, callback=None, args=None, timeout=10):
        if callback is None:
            callback = self.revert_on_connect_failure
            args = None
        self.start_client_mode()
        self.wpa_supplicant.start_connecting(network, callback, args, timeout)

    def stop_connecting(self):
        self.wpa_supplicant.stop_connecting()

    def disconnect(self):
        self.wpa_supplicant.disconnect()

    def get_state(self):
        state = self.OFF_STATE

        if self.wpa_supplicant.started():
            state = self.CLIENT_STATE
        elif self.hotspot.started():
            state = self.HOTSPOT_STATE

        return state

    def revert_on_connect_failure(self, result):
        if not result:
            self.start_hotspot_mode()

    def reconnect(self, result, network):
        if not result:
            self.start_connecting(network)


if __name__ == '__main__':
    wifi_control = WiFiControl('wlp6s0')
    print(wifi_control.get_status())
