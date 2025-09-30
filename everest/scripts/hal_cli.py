#!/usr/bin/env python3
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'modules', 'esp32_hal_adapter', 'python'))
from esp_periph_client import EspPeriphClient  # type: ignore


def main():
    ap = argparse.ArgumentParser(description='HAL CLI for ESP32-S3 peripheral (UART JSON-RPC)')
    ap.add_argument('--port', default=os.environ.get('ESP32_TTY', '/dev/ttyUSB0'))
    ap.add_argument('--baud', type=int, default=int(os.environ.get('ESP32_BAUD', '115200')))
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('ping')
    sub.add_parser('info')
    sub.add_parser('meter')

    p_pwm = sub.add_parser('pwm')
    p_pwm.add_argument('duty', type=int)
    p_pwm.add_argument('--enable', action='store_true')
    p_pwm.add_argument('--disable', action='store_true')

    p_dcset = sub.add_parser('dcset')
    p_dcset.add_argument('--volts', type=float, default=None)
    p_dcset.add_argument('--amps', type=float, default=None)
    p_dcset.add_argument('--on', action='store_true')
    p_dcset.add_argument('--off', action='store_true')

    p_dcen = sub.add_parser('dcen')
    p_dcen.add_argument('state', choices=['on', 'off'])

    p_cont = sub.add_parser('contactor')
    p_cont.add_argument('state', choices=['on', 'off', 'check'])

    args = ap.parse_args()
    c = EspPeriphClient(port=args.port, baud=args.baud)
    c.connect()

    try:
        if args.cmd == 'ping':
            print(c.sys_ping())
        elif args.cmd == 'info':
            print(c.sys_info())
        elif args.cmd == 'meter':
            m = c.meter_read()
            print({'V': m.voltage_v, 'A': m.current_a, 'kW': m.power_kw, 'kWh': m.energy_kwh})
        elif args.cmd == 'pwm':
            c.cp_set_mode('manual')
            en = True if args.enable else False if args.disable else None
            st = c.cp_set_pwm(args.duty, enable=en)
            print(st)
        elif args.cmd == 'dcset':
            on = True if args.on else False if args.off else None
            print(c.dc_set(args.volts, args.amps, on))
        elif args.cmd == 'dcen':
            print(c.dc_enable(args.state == 'on'))
        elif args.cmd == 'contactor':
            if args.state == 'check':
                print(c.contactor_check())
            else:
                print(c.contactor_set(args.state == 'on'))
    finally:
        c.close()


if __name__ == '__main__':
    main()

