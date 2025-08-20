import os
import fcntl
import struct
import threading
from plc_communication.plc_network import PLCNetwork

# Constants for TUN/TAP device creation
TUNSETIFF = 0x400454ca
IFF_TUN = 0x0001
IFF_NO_PI = 0x1000

def create_tun_interface():
    """Creates and returns a TUN interface file descriptor and name."""
    try:
        tun_fd = os.open('/dev/net/tun', os.O_RDWR)
        ifr = struct.pack('16sH', b'tun0', IFF_TUN | IFF_NO_PI)
        fcntl.ioctl(tun_fd, TUNSETIFF, ifr)
        return tun_fd, 'tun0'
    except IOError as e:
        print(f"Error creating TUN device: {e}")
        exit(1)

def configure_tun_interface(interface_name, ip_address, netmask):
    """Configures the TUN interface with the given IP address and netmask."""
    os.system(f'ip addr add {ip_address}/{netmask} dev {interface_name}')
    os.system(f'ip link set dev {interface_name} up')

def plc_to_tun(plc, tun_fd):
    """Reads from the PLC and writes to the TUN interface."""
    while True:
        frame = plc.recv()
        if frame:
            os.write(tun_fd, bytes(frame))

def tun_to_plc(plc, tun_fd):
    """Reads from the TUN interface and writes to the PLC."""
    while True:
        packet = os.read(tun_fd, 2048)
        if packet:
            plc.send(list(packet))

def main():
    # Create and configure the TUN interface
    tun_fd, tun_name = create_tun_interface()
    configure_tun_interface(tun_name, '192.168.1.1', '24')

    # Create the PLC network object
    plc = PLCNetwork()

    # Create and start the threads
    plc_to_tun_thread = threading.Thread(target=plc_to_tun, args=(plc, tun_fd))
    tun_to_plc_thread = threading.Thread(target=tun_to_plc, args=(plc, tun_fd))

    plc_to_tun_thread.start()
    tun_to_plc_thread.start()

    print(f"TUN device '{tun_name}' is up and bridged with the PLC modem.")
    print("Press Ctrl+C to stop.")

    try:
        plc_to_tun_thread.join()
        tun_to_plc_thread.join()
    except KeyboardInterrupt:
        print("\nClosing TUN device and stopping threads.")
        os.close(tun_fd)

if __name__ == '__main__':
    main()
