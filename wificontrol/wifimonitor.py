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
import functools

import dbus
import dbus.service
import dbus.mainloop.glib
import logging
from wificontrol import WiFiControl

try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

logger = logging.getLogger(__name__)

DBUS_PROPERTIES_IFACE = 'org.freedesktop.DBus.Properties'
WPAS_INTERFACE_DBUS_IFACE = "fi.w1.wpa_supplicant1.Interface"
SYSTEMD_DBUS_SERVICE = 'org.freedesktop.systemd1'
SYSTEMD_DBUS_OPATH = '/org/freedesktop/systemd1'
SYSTEMD_MANAGER_DBUS_IFACE = 'org.freedesktop.systemd1.Manager'
HOSTAPD_DBUS_UNIT_OPATH = '/org/freedesktop/systemd1/unit/hostapd_2eservice'
DNSMASQ_DBUS_SERVICE = 'uk.org.thekelleys.dnsmasq'
DNSMASQ_DBUS_OPATH = '/uk/org/thekelleys/dnsmasq'


class WiFiMonitorError(Exception):
    pass


class WiFiMonitor(object):
    CLIENT_SCANNING = 'CLIENT SCANNING'
    CLIENT_CONNECTING = 'CLIENT CONNECTING'
    CLIENT_CONNECTED = 'CLIENT CONNECTED'
    CLIENT_DISCONNECTED = 'CLIENT DISCONNECTED'

    CLIENT_STATUS_EVENTS = {
        'scanning': CLIENT_SCANNING,
        'associating': CLIENT_CONNECTING,
        'completed': CLIENT_CONNECTED,
        'disconnected': CLIENT_DISCONNECTED
    }

    HOTSPOT_STARTING = 'HOTSPOT STARTING'
    HOTSPOT_STARTED = 'HOTSPOT STARTED'
    HOTSPOT_STOPPING = 'HOTSPOT STOPPING'
    HOTSPOT_STOPPED = 'HOTSPOT STOPPED'
    HOTSPOT_FAILED = 'HOTSPOT FAILED'

    HOTSPOT_STATUS_EVENTS = {
        ('activating', 'start'): HOTSPOT_STARTING,
        ('active', 'running'): HOTSPOT_STARTED,
        ('deactivating', 'stop-post'): HOTSPOT_STOPPING,
        ('deactivating', 'stop-sigterm'): HOTSPOT_STOPPING,
        ('inactive', 'dead'): HOTSPOT_STOPPED,
        ('failed', 'failed'): HOTSPOT_FAILED
    }

    LEASE_UP = "LEASE UP"
    LEASE_ADDED = "LEASE ADDED"
    LEASE_UPDATED = "LEASE UPDATED"
    LEASE_DELETED = "LEASE DELETED"

    HOTSPOT_LEASE_EVENTS = {
        'Up': LEASE_UP,
        'DhcpLeaseAdded': LEASE_ADDED,
        'DhcpLeaseUpdated': LEASE_UPDATED,
        'DhcpLeaseDeleted': LEASE_DELETED
    }

    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self._mainloop = GObject.MainLoop()

        self.wifi_control = None

        self.callbacks = {}

        self.last_host_event = None

    def _initialize(self):
        systemd_obj = self.bus.get_object(SYSTEMD_DBUS_SERVICE,
                                          SYSTEMD_DBUS_OPATH)
        self.sysd_manager = dbus.Interface(systemd_obj, dbus_interface=SYSTEMD_MANAGER_DBUS_IFACE)
        self.sysd_manager.Subscribe()

        wpa_interface = self.wifi_control.wpa_supplicant.wpa_supplicant_interface.get_interface_path()

        self.bus.add_signal_receiver(self._wpa_properties_changed,
                                     dbus_interface=WPAS_INTERFACE_DBUS_IFACE,
                                     signal_name="PropertiesChanged",
                                     path=wpa_interface)

        self.bus.add_signal_receiver(self._hostapd_properties_changed,
                                     dbus_interface=DBUS_PROPERTIES_IFACE,
                                     signal_name="PropertiesChanged",
                                     path=HOSTAPD_DBUS_UNIT_OPATH)

        dnsmasq = self.bus.get_object(DNSMASQ_DBUS_SERVICE, DNSMASQ_DBUS_OPATH)

        for signal in self.HOTSPOT_LEASE_EVENTS.keys():
            dnsmasq.connect_to_signal(signal, functools.partial(self._dhcp_lease_changed, signal))

    def _wpa_properties_changed(self, props):
        wpa_state = props.get('State')
        if wpa_state:
            event = self.CLIENT_STATUS_EVENTS.get(wpa_state)
            if event:
                data = self.wifi_control.wpa_supplicant.get_status()
                self._execute_callbacks(event, data)
            else:
                logger.error("Unmapped WPA state: %s", wpa_state)

    def _hostapd_properties_changed(self, *args):
        _, props, _ = args
        active_state = props.get('ActiveState')
        sub_state = props.get('SubState')

        if active_state and sub_state:
            event = self.HOTSPOT_STATUS_EVENTS.get((active_state, sub_state))
            if event:
                if event != self.last_host_event:
                    self.last_host_event = event
                    data = self.wifi_control.hotspot.get_hotspot_ssid()
                    self._execute_callbacks(event, data)
            else:
                logger.error("Unmapped HOSTAPD state: %s (%s)", active_state, sub_state)

    def _dhcp_lease_changed(self, *args, **kwargs):
        if self.wifi_control.get_state() != self.wifi_control.HOTSPOT_STATE:
            return

        signal = args[0]
        event = self.HOTSPOT_LEASE_EVENTS.get(signal)

        if event:
            data = dict()
            if len(args) == 4:
                data['name'] = str(args[3])
                data['ip'] = str(args[1])
                data['mac'] = str(args[2])
            self._execute_callbacks(event, data)
        else:
            logger.error("Unmapped DNSMASQ signal: %s", signal)

    def register_callback(self, msg, callback, args=()):
        if msg not in self.callbacks:
            self.callbacks[msg] = []

        self.callbacks[msg].append((callback, args))

    def _execute_callbacks(self, event, data):
        callbacks = self.callbacks.get(event)
        if callbacks:
            for callback in callbacks:
                callback, args = callback
                try:
                    callback(event, data)
                except Exception as error:
                    logger.error('Callback {} execution error. {}'.format(callback.__name__, error))

    def run(self, wifi_control: WiFiControl):
        try:
            self.wifi_control = wifi_control
            self._initialize()
        except dbus.exceptions.DBusException as error:
            logger.error(error)
            raise WiFiMonitorError(error)

        self._mainloop.run()

    def shutdown(self):
        self._deinitialize()
        self._mainloop.quit()
        logger.info('WiFiMonitor stopped')

    def _deinitialize(self):
        try:
            self.sysd_manager.Unsubscribe()
        except dbus.exceptions.DBusException as error:
            logger.error(error)
            raise WiFiMonitorError(error)
