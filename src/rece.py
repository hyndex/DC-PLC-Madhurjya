import serial
ser = serial.Serial('/dev/serial0', 115200, timeout=1)
while True:
    line = ser.readline()
    if line:
        print(line.decode(errors='replace').rstrip())