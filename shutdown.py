import serial
ser = serial.Serial('/dev/ttyACM0')
ser.write('s')
import os
os.system("sudo shutdown -h now")