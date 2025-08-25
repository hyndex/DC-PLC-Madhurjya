import time
import math
import threading
try:
    import RPi.GPIO as GPIO
except ImportError:
    GPIO = None  # Running off-hardware (simulation mode)

try:
    import spidev  # For MCP3008 ADC
except ImportError:
    spidev = None

# Configuration constants
CP_PWM_HZ = 1000      # 1 kHz PWM frequency for CP signal
CP_PWM_PIN = 18       # GPIO pin for CP PWM output (use a hardware PWM-capable pin)
CP_ADC_CHANNEL = 0    # MCP3008 channel for CP voltage input (via voltage divider)
ADC_MAX_READING = 1023  # 10-bit ADC
ADC_REF_V = 3.3       # MCP3008 reference voltage (3.3V)
CP_DIV_RATIO = 4.0    # Assume CP voltage is divided down 1:4 for ADC (0-12V -> 0-3V)

# Global state for simulation
_current_duty = 0.0
_simulated_cp_state = "A"  # Tracks the current CP state in simulation mode

# Initialize GPIO and PWM (if hardware is available)
_pwm = None
if GPIO:
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(CP_PWM_PIN, GPIO.OUT)
    _pwm = GPIO.PWM(CP_PWM_PIN, CP_PWM_HZ)
    _pwm.start(0)  # start with 0% duty (no PWM)
    # Note: Ensure a proper external circuit drives 12V on CP through a resistor and uses this pin to pull CP low.

# Initialize SPI for ADC if available
_spi = None
if spidev:
    _spi = spidev.SpiDev()
    try:
        _spi.open(0, 0)  # open(bus 0, CS0) – adjust if MCP3008 on different SPI bus
        _spi.max_speed_hz = 1_000_000
    except FileNotFoundError:
        _spi = None  # SPI device not found, fallback to simulation

def set_pwm_duty(duty_percent: float):
    """
    Set the CP PWM duty cycle (0.0 to 100.0). Frequency is fixed at 1kHz.
    In simulation mode, just store the duty. On real hardware, update the PWM output.
    """
    global _current_duty
    _current_duty = duty_percent
    if _pwm:
        _pwm.ChangeDutyCycle(duty_percent)
    # If needed, add logging or print for debug:
    print(f"[CP PWM] Duty set to {duty_percent:.1f}%")

def read_cp_voltage() -> float:
    """
    Read the CP line voltage via ADC. Returns voltage in volts.
    If ADC is not available, returns a simulated voltage based on _simulated_cp_state.
    """
    if _spi:
        # MCP3008 uses 10-bit readings. We perform an SPI transaction to read channel.
        # MCP3008 protocol: send start bit, single-ended bit + channel bits, then read 10-bit result.
        cmd = 0b11 << 6  # start bit (1) + single-ended (1)
        cmd |= (CP_ADC_CHANNEL & 0x07) << 3
        # Send 3 bytes: [start/single/channel, dummy, dummy]; receive 3 bytes
        adc = _spi.xfer2([cmd, 0x0, 0x0])
        # adc[1] & 0x03 = top 2 bits, adc[2] = lower 8 bits
        raw_val = ((adc[1] & 0x0F) << 8) | adc[2]
        voltage = (raw_val / ADC_MAX_READING) * ADC_REF_V * CP_DIV_RATIO
        return round(voltage, 2)
    else:
        # Simulation: return ideal voltages for the current CP state
        state = _simulated_cp_state
        if state == "A":
            return 12.0  # 12 V
        elif state == "B":
            return 9.0   # 9 V (vehicle present, not ready)
        elif state == "C":
            return 6.0   # 6 V (ready for charging, no ventilation)
        elif state == "D":
            return 3.0   # 3 V (ready, with ventilation)
        elif state == "E":
            return 0.0   # 0 V (error)
        else:
            return 12.0  # default to A if unknown

def simulate_cp_state(state: str):
    """
    Simulation helper: set the CP state (A, B, C, D, or E) to influence read_cp_voltage().
    In real hardware, CP state is inferred from voltage readings and duty behavior.
    """
    global _simulated_cp_state
    _simulated_cp_state = state
    # If transitioning to HLC mode (digital communication), set 5% duty
    if state in ("B", "C", "D") and _current_duty < 3.0:
        # During B to C, EVSE uses 5% to indicate HLC mode
        set_pwm_duty(5.0)
