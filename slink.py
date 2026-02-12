#!/usr/bin/env python3
import pigpio
import time
import collections
import sys
import subprocess

# Configuration
OUTPUT_PIN = 2
INPUT_PIN = 3
DEBUG_PULSES = False

# Buffers and State
pulse_buffer = collections.deque(maxlen=200)
last_interrupt_time = 0
time_low_transition = 0
pulse_lengths = ""

pi = pigpio.pi()

def bus_change(gpio, level, tick):
    """Interrupt handler for pulse detection."""
    global last_interrupt_time, time_low_transition
    
    # tick is in microseconds (32-bit, wraps every ~72 mins)
    # Debounce: ignore changes faster than 100us
    if pigpio.tickDiff(last_interrupt_time, tick) < 100:
        return
    last_interrupt_time = tick

    if level == 0:  # Falling edge (Bus Low)
        time_low_transition = tick
    elif level == 1:  # Rising edge (Bus High)
        # Calculate how long it was low
        time_low = pigpio.tickDiff(time_low_transition, tick)
        
        # Store scaled value (matching Arduino logic)
        pulse_buffer.append(min(255, time_low // 10))

def is_bus_idle():
    now = pi.get_current_tick()
    # Idle threshold: 1200 + 600 + 20000 microseconds
    return pigpio.tickDiff(time_low_transition, now) > 21800

def send_command(hex_str):
    """Sends a hex string as S-Link pulses."""
    if not is_bus_idle():
        return False

    try:
        data = bytes.fromhex(hex_str)
    except ValueError:
        print("Invalid hex string")
        return True

    # Build pulse sequence (high/low microseconds)
    wf = []
    
    # Sync Pulse: 2400us High, 600us Low
    wf.append(pigpio.pulse(1 << OUTPUT_PIN, 0, 2400))
    wf.append(pigpio.pulse(0, 1 << OUTPUT_PIN, 600))

    for byte in data:
        for i in range(7, -1, -1):
            bit = (byte >> i) & 1
            duration = 1200 if bit else 600
            wf.append(pigpio.pulse(1 << OUTPUT_PIN, 0, duration))
            wf.append(pigpio.pulse(0, 1 << OUTPUT_PIN, 600))

    pi.wave_add_generic(wf)
    wid = pi.wave_create()
    if wid >= 0:
        pi.wave_send_once(wid)
        while pi.wave_tx_busy():
            time.sleep(0.001)
        pi.wave_delete(wid)
    
    time.sleep(0.02) # idleAfterCommand (20ms)
    return True

def process_slink_input():
    global pulse_lengths
    current_byte = 0
    current_bit = 0
    partial_output = False

    while pulse_buffer:
        time_low = pulse_buffer.popleft() * 10
        
        if DEBUG_PULSES:
            if time_low > 2000: pulse_lengths = ""
            pulse_lengths += f" {time_low}"

        if time_low > 2000:
            if partial_output and current_bit != 0:
                print(f"\n!Discarding {current_bit} stray bits")
            current_bit = 0
            continue

        partial_output = True
        current_bit += 1
        
        # Shift in the bit
        if time_low > 900:
            current_byte |= (1 << (8 - current_bit))
        else:
            current_byte &= ~(1 << (8 - current_bit))

        if current_bit == 8:
            print(f"{current_byte:02X}", end="", flush=True)
            current_byte = 0
            current_bit = 0

    if partial_output and is_bus_idle():
        print("") # Newline on end of transmission

def setup():
    if not pi.connected:
        print("Could not connect to pigpiod!")
        sys.exit(1)

    pi.set_mode(OUTPUT_PIN, pigpio.OUTPUT)
    pi.write(OUTPUT_PIN, 0)
    pi.set_mode(INPUT_PIN, pigpio.INPUT)
    
    # Callback handles the interrupt logic
    pi.callback(INPUT_PIN, pigpio.EITHER_EDGE, bus_change)

def volume_up():
    send_command("C014")

def volume_down():
    send_command("C015")

def mute():
    send_command("C006")

def main():
    setup()
    try:
        proc = subprocess.Popen(["cec-client", "--type", "a", "-m", "-d", "8", "-o", "'RPI Bridge'"], shell=True, stdout=subprocess.PIPE)
        for line in proc.stdout:
            line = line.decode()
            if line.find(" 05:44:41") > -1:
                volume_up()
            if line.find(" 05:44:42") > -1:
                volume_down()
            if line.find(" 50:7a:7f") > -1:
                mute()
        # volume_down()
        # while True:
        #     process_slink_input()
            
        #     # Non-blocking check for console input
        #     # In Python, we can use a small sleep to prevent 100% CPU usage
        #     time.sleep(0.01)
            
        #     # Simple simulation of processSerialInput
        #     # Note: sys.stdin.readline is blocking. For a true clone, 
        #     # use 'select' or a separate thread for input.
    except KeyboardInterrupt:
        pi.stop()

if __name__ == "__main__":
    main()

