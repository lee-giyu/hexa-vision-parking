"""Edge trigger client (mock harness for the hardware/AI team).

Mocks an ultrasonic distance sensor and triggers YOLO inference when a vehicle
approaches, then forwards the recognized license plate to the backend. Swap the
two mock functions (read_distance, run_yolo_inference) for the real GPIO read and
model inference to run it against actual hardware.
"""

import random
import time

import requests

# ==========================================
# Configuration
# ==========================================
# Point this at the backend's address (e.g. its Tailscale IP) for a live run.
API_URL = "http://localhost:8000/gate/entrance"
LOT_ID = 1  # ID of the parking lot this camera is installed in.

DISTANCE_THRESHOLD_CM = 2.0  # Trigger when an object comes within this distance.
READ_INTERVAL_SEC = 0.2      # Poll the sensor every 0.2s so triggers aren't missed.


def read_distance():
    """Mock an ultrasonic distance reading (e.g. HC-SR04), in centimeters.

    HW team: replace this with the real GPIO read.
    """
    if random.random() < 0.2:  # ~20% chance of simulating a detected vehicle.
        return random.uniform(1.0, 1.9)  # Within the trigger threshold.
    return random.uniform(10.0, 200.0)


def run_yolo_inference():
    """Mock the YOLO + OCR license-plate recognition step.

    AI team: replace this with the real model.predict() + OCR pipeline. It must
    return a plate string such as "12가3456" or "123가4567".
    """
    print("[AI] Running YOLO inference...")
    time.sleep(0.3)  # Simulated inference latency.

    mock_plates = ["12가3456", "123가4567", "55하7788"]
    detected_plate = random.choice(mock_plates)
    print(f"[AI] Detected plate: {detected_plate}")

    return detected_plate


def send_to_backend(plate_number):
    """Forward the recognized plate and lot_id to the FastAPI backend."""
    payload = {
        "lot_id": LOT_ID,
        "plate_number": plate_number,
    }

    print(f"[Network] Sending data to backend: {payload}")
    try:
        response = requests.post(API_URL, json=payload, timeout=5)
        if response.status_code == 200:
            print(f"[Network] Success! Server response: {response.json()}")
        else:
            print(f"[Network] Error {response.status_code}: {response.text}")
    except requests.exceptions.RequestException as e:
        print(f"[Network] Connection failed: {e}")


def main():
    print("Starting Edge Trigger Client...")
    print("Monitoring distance sensor...")

    try:
        while True:
            distance = read_distance()
            print(f"[Sensor] Distance: {distance:.1f} cm")

            if distance < DISTANCE_THRESHOLD_CM:
                print(f"\n[Trigger] Vehicle detected! (Distance: {distance:.1f} cm)")

                # Let the vehicle settle into position before capturing.
                print("[System] Waiting 5 seconds before taking a shot...")
                time.sleep(5.0)

                print("[System] --- STARTING SINGLE CAPTURE ---")

                # Capture once, recognize, then forward to the backend.
                plate_number = run_yolo_inference()
                if plate_number:
                    send_to_backend(plate_number)

                print("[System] --- CAPTURE COMPLETE ---")

                # Cool down so the same vehicle doesn't trigger a duplicate capture.
                print("[System] Cooling down for 5 seconds to prevent immediate duplicate burst...")
                time.sleep(5.0)

            time.sleep(READ_INTERVAL_SEC)

    except KeyboardInterrupt:
        print("\n[System] Edge Trigger Client stopped.")


if __name__ == "__main__":
    main()
