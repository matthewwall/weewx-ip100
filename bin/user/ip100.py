#!/usr/bin/env python
# Copyright 2016 Matthew Wall
# Distributed under the terms of the GNU Public License (GPLv3)

"""Driver for collecting data from Rainwise IP-100"""

from __future__ import absolute_import
from __future__ import print_function
import logging
import time
import six.moves.urllib.request, six.moves.urllib.error, six.moves.urllib.parse
import socket
from xml.etree import ElementTree

import weewx
import weewx.drivers
import weewx.wxformulas

log = logging.getLogger(__name__)

DRIVER_NAME = 'IP100'
DRIVER_VERSION = '0.5'

def loader(config_dict, engine):
    return IP100Driver(**config_dict[DRIVER_NAME])

def configurator_loader(config_dict):
    return IP100Configurator()

def confeditor_loader():
    return IP100ConfEditor()


class IP100ConfEditor(weewx.drivers.AbstractConfEditor):
    @property
    def default_stanza(self):
        return """
[IP100]
    # This section is for the Rainwise IP-100 weather stations.

    # How often to poll the device, in seconds
    poll_interval = 2

    # The driver to use
    driver = user.ip100

    # The number of times to try to read from the IP100 before giving up.
    max_tries = 3

    # The number of seconds to wait before retrying a read from the IP100.
    retry_wait = 5
"""


class IP100Configurator(weewx.drivers.AbstractConfigurator):
    def add_options(self, parser):
        super(IP100Configurator, self).add_options(parser)
        parser.add_option("--info", dest="info", action="store_true",
                          help="display weather station configuration")
        parser.add_option("--current", dest="current", action="store_true",
                          help="get the current weather conditions")

    def do_options(self, options, parser, config_dict, prompt):
        station = IP100Driver(**config_dict[DRIVER_NAME])
        if options.current:
            self.show_current(station)
        else:
            self.show_info(station)

    @staticmethod
    def show_info(station):
        """Query the station then display the settings."""
        # FIXME: implement show_info

    @staticmethod
    def show_current(station):
        """Display latest readings from the station."""
        for packet in station.genLoopPackets():
            print(packet)
            break


class IP100Driver(weewx.drivers.AbstractDevice):
    DEFAULT_MAP = {
        'outTemp': 'temperature_outside',
        'inTemp': 'temperature_inside',
        'outHumidity': 'humidity',
        'pressure': 'pressure',
        'windSpeed': 'wind_speed',
        'windDir': 'wind_dir',
        'windGust': 'gust_speed',
        'windGustDir': 'gust_dir',
        'day_rain_total': 'precipitation',
        'radiation': 'solar_radiation',
        'supplyVoltage': 'station_volts'}

    def __init__(self, **stn_dict):
        log.info('driver version is %s' % DRIVER_VERSION)
        if 'station_url' in stn_dict:
            self.station_url = stn_dict['station_url']
        else:
            host = stn_dict.get('host', '192.168.1.12')
            port = int(stn_dict.get('port', 80))
            self.station_url = "http://%s:%s/status.xml" % (host, port)
        log.info("station url is %s" % self.station_url)

        # Don't go any further if the IP100 cannot be reached.
        # If the machine was just rebooted, a temporary failure in name
        # resolution is likely.  As such, try three times.
        for i in range(5):
            try:
                response = six.moves.urllib.request.urlopen(self.station_url)
                response.read()
                break
            except Exception as e:
                log.debug('%s: %s' % (type(e), e))
                if i < 4:
                    log.info('%s: Retrying.' % e)
                    time.sleep(5)
                else:
                    raise e

        self.poll_interval = int(stn_dict.get('poll_interval', 2))
        log.info("poll interval is %s" % self.poll_interval)
        self.sensor_map = dict(IP100Driver.DEFAULT_MAP)
        if 'sensor_map' in stn_dict:
            self.sensor_map.update(stn_dict['sensor_map'])
        log.info("sensor map: %s" % self.sensor_map)
        self.max_tries = int(stn_dict.get('max_tries', 3))
        self.retry_wait = int(stn_dict.get('retry_wait', 5))

        # track the last rain counter value so we can determine deltas
        self.previous_rain_total = None

    @staticmethod
    def _rain_total_to_delta(rain_total, previous_rain_total):
        # calculate the rain delta between the current and previous rain totals.
        return weewx.wxformulas.calculate_rain(rain_total, previous_rain_total)

    @property
    def hardware_name(self):
        return "IP-100"

    def time_to_next_poll(self):
        now = time.time()
        next_poll_event = int(now / self.poll_interval) * self.poll_interval + self.poll_interval
        log.debug('now: %f, poll_interval: %d, next_poll_event: %f' % (now, self.poll_interval, next_poll_event))
        secs_to_poll = next_poll_event - now
        log.debug('Next polling event in %f seconds' % secs_to_poll)
        return secs_to_poll

    def genLoopPackets(self):
        ntries = 0
        while ntries < self.max_tries:
            ntries += 1
            try:
                # Poll on poll_interval boundaries.
                if self.poll_interval != 0:
                    time.sleep(self.time_to_next_poll())
                data = IP100Station.get_data(self.station_url)
                log.debug("data: %s" % data)
                pkt = IP100Station.parse_data(data)
                log.debug("raw packet: %s" % pkt)
                ntries = 0
                packet = {'dateTime': int(time.time() + 0.5)}
                if pkt['base_units'] == 'English':
                    packet['usUnits'] = weewx.US
                else:
                    packet['usUnits'] = weewx.METRICWX
                for k in self.sensor_map:
                    if self.sensor_map[k] in pkt:
                        packet[k] = pkt[self.sensor_map[k]]
                if 'day_rain_total' in packet:
                    packet['rain'] = self._rain_total_to_delta(
                        packet['day_rain_total'], self.previous_rain_total)
                    self.previous_rain_total = packet['day_rain_total']
                else:
                    log.debug("no rain in packet: %s" % packet)
                log.debug("packet: %s" % packet)
                yield packet
            except weewx.WeeWxIOError as e:
                log.info("failed attempt %s of %s: %s" %
                       (ntries, self.max_tries, e))
                time.sleep(self.retry_wait)
        else:
            raise weewx.WeeWxIOError("max tries %s exceeded" % self.max_tries)


class IP100Station(object):
    @staticmethod
    def get_data(url):
        try:
            response = six.moves.urllib.request.urlopen(url)
            return response.read()
        except (socket.error, socket.timeout, six.moves.urllib.error.HTTPError,
                six.moves.urllib.error.URLError) as e:
            raise weewx.WeeWxIOError("get data failed: %s" % e)

    @staticmethod
    def parse_data(data):
        pkt = dict()
        try:
            root = ElementTree.fromstring(data)
            if root.tag == 'status':
                pkt.update(IP100Station.parse_hardware(root.find('hardware')))
                pkt.update(IP100Station.parse_weather(root.find('weather')))
            else:
                log.error("no status element in data")
        except ElementTree.ParseError as e:
            log.debug("parse failed: %s" % e)
        return pkt

    @staticmethod
    def parse_hardware(hw):
        pkt = dict()
        if hw is None:
            return pkt
        for c in hw:
            if len(c) == 0:
                pkt[c.tag] = c.text
            else:
                pkt.update(IP100Station.parse_hardware(c))
        return pkt

    @staticmethod
    def parse_weather(w):
        pkt = dict()
        if w is None:
            return pkt
        for c in w:
            if c.tag == 'wind':
                pkt['wind_speed'] = float(c.find('speed').text)
                pkt['wind_dir'] = float(c.find('direction').text)
                pkt['gust_speed'] = float(c.find('gust_speed').text)
                pkt['gust_dir'] = float(c.find('gust_direction').text)
            elif c.find('current') is not None:
                pkt[c.tag] = float(c.find('current').text)
            else:
                log.debug("ignored %s" % c.tag)
        return pkt


if __name__ == '__main__':
    import optparse

    import weewx
    import weeutil.logger

    usage = """%prog [options] [--debug] [--help]"""

    def main():
        parser = optparse.OptionParser(usage=usage)
        parser.add_option('--version', dest='version', action='store_true',
                          help='display driver version')
        parser.add_option('--debug', dest='debug', action='store_true',
                          help='display diagnostic information while running')
        parser.add_option('--host', dest='host', metavar="HOST",
                          help='hostname or ip address of the IP-100')
        parser.add_option('--port', dest='port', type=int, metavar="PORT",
                          default=80,
                          help='port on which IP-100 is listening')
        parser.add_option('--test-parse', dest='filename', metavar='FILENAME',
                          help='test the xml parsing')
        (options, _) = parser.parse_args()

        if options.version:
            print("ip100 driver version %s" % DRIVER_VERSION)
            exit(1)

        weeutil.logger.setup('ip100', {})

        if options.debug:
            weewx.debug = 1
        else:
            weewx.debug = 0

        if options.filename:
            data = ''
            with open(options.filename, "r") as f:
                data = f.read()
            packet = IP100Station.parse_data(data)
            print(packet)
            exit(0)

        url = "http://%s:%s/status.xml" % (options.host, options.port)
        print("get data from %s" % url)
        data = IP100Station.get_data(url)
        if options.debug:
            print("data: ", data)
        packet = IP100Station.parse_data(data)
        print("packet: ", packet)

    main()
