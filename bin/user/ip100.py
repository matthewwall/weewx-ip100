#!/usr/bin/env python
# Copyright 2016 Matthew Wall, all rights reserved

"""Driver for collecting data from Rainwise IP-100"""

import syslog
import time
import urllib2
from xml.etree import ElementTree

import weewx
import weewx.drivers

DRIVER_NAME = 'IP100'
DRIVER_VERSION = '0.1'

def logmsg(dst, msg):
    syslog.syslog(dst, 'ip100: %s' % msg)

def logdbg(msg):
    logmsg(syslog.LOG_DEBUG, msg)

def loginf(msg):
    logmsg(syslog.LOG_INFO, msg)

def logcrt(msg):
    logmsg(syslog.LOG_CRIT, msg)

def logerr(msg):
    logmsg(syslog.LOG_ERR, msg)


def loader(config_dict, engine):
    return IP100(**config_dict[DRIVER_NAME])

#def configurator_loader(config_dict):
#    return IP100Configurator()

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

    def show_info(self, station):
        """Query the station then display the settings."""
        # FIXME: implement show_info

    def show_current(self, station):
        """Display latest readings from the station."""
        for packet in station.genLoopPackets():
            print packet
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
        'rain': 'precipitation',
        'radiation': 'solar_radiation'}

    def __init__(self, **stn_dict):
        if 'station_url' in stn_dict:
            self.station_url = stn_dict['station_url']
        else:
            host = stn_dict.get('host', '192.168.1.12')
            port = int(stn_dict.get('port', 80))
            self.station_url = "http://%s:%s/status.xml" % (host, port)
        loginf("station url is %s" % self.station_url)
        self.poll_interval = int(stn_dict.get('poll_interval', 2))
        loginf("poll interval is %s" % self.poll_interval)
        self.sensor_map = stn_dict.get('sensor_map', IP100Driver.DEFAULT_MAP)
        self.max_tries = int(stn_dict.get('max_tries', 3))
        self.retry_wait = int(stn_dict.get('retry_wait', 5))

    @property
    def hardware_name(self):
        return "IP-100"

    def openPort(self):
        pass
        
    def closePort(self):
        pass

    def genLoopPackets(self):
        ntries = 0
        while ntries < self.max_tries:
            ntries += 1
            try:
                data = IP100Station.get_data(self.station_url)
                logdbg("data: %s" % data)
                pkt = IP100Station.parse_data(data)
                logdbg("raw packet: %s" % pkt)
                ntries = 0
                packet = {'dateTime': int(time.time() + 0.5)}
                if pkt['base_units'] == 'English':
                    packet['usUnits'] = weewx.US
                else:
                    packet['usUnits'] = weewx.METRICWX
                for k in self.sensor_map:
                    if self.sensor_map[k] in pkt:
                        packet[k] = pkt[self.sensor_map[k]]
                yield packet
                if self.poll_interval:
                    time.sleep(self.poll_interval)
            except WeeWxIOError, e:
                loginf("failed attempt %s of %s: %s" %
                       (ntries, self.max_tries, e))
                time.sleep(self.retry_wait)
        else:
            raise WeeWxIOError("max tries %s exceeded" % self.max_tries)


class IP100Station(object):
    @staticmethod
    def get_data(url):
        content = None
        try:
            response = urllib2.urlopen(url)
            content = response.read()
        except urllib2.HTTPError, e:
            raise WeeWxIOError("get data failed: %s" % e)
        return content

    @staticmethod
    def parse_data(data):
        pkt = dict()
        try:
            root = ElementTree.fromstring(data)
            if root.tag == 'status':
                pkt.update(IP100Station.parse_hardware(root.find('hardware')))
                pkt.update(IP100Station.parse_weather(root.find('weather')))
            else:
                logerr("no status element in data")
        except ElementTree.ParseError, e:
            logdbg("parse failed: %s" % e)
        return pkt

    @staticmethod
    def parse_hardware(hw):
        pkt = dict()
        if not hw:
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
        if not w:
            return pkt
        for c in w:
            if c.tag == 'wind':
                pkt['wind_speed'] = float(c.find('speed').text)
                pkt['wind_dir'] = float(c.find('direction').text)
                pkt['gust_speed'] = float(c.find('gust_speed').text)
                pkt['gust_dir'] = float(c.find('gust_direction').text)
            elif c.find('current'):
                pkt[c.tag] = float(c.find('current').text)
            else:
                logdbg("ignored %s" % c.tag)
        return pkt


if __name__ == '__main__':
    import optparse

    usage = """%prog [options] [--debug] [--help]"""

    def main():
        syslog.openlog('wee_ip100', syslog.LOG_PID | syslog.LOG_CONS)
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
            print "ip100 driver version %s" % DRIVER_VERSION
            exit(1)

        if options.debug is not None:
            syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_DEBUG))
        else:
            syslog.setlogmask(syslog.LOG_UPTO(syslog.LOG_INFO))

        if options.filename:
            data = ''
            with open(options.filename, "r") as f:
                data = f.read()
            packet = IP100Station.parse_data(data)
            print packet
            exit(0)

        url = "http://%s:%s" % (options.host, options.port)
        print "get data from %s" % url
        data = IP100Station.get_data(url)
        if options.debug:
            print "data: ", data
        packet = IP100Station.parse_data(data)
        print "packet: ", packet

    main()
