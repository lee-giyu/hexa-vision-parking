import io
import os
import time

import requests
from picamera2 import Picamera2

# Off-Pi vision server endpoint. The real host (a private Tailscale/Cloudflare
# address) is kept out of version control — set SERVER_URL in the environment.
SERVER_URL = os.getenv("SERVER_URL", "http://localhost:5000/detect")

picam2 = Picamera2()

config = picam2.create_preview_configuration(
    main={"size": (640, 480)}
)

picam2.configure(config)
picam2.start()

# Give the sensor a moment to initialize and settle its auto-exposure.
time.sleep(2)

print("Starting capture/forward loop")

while True:
    try:
        # Capture straight to an in-memory JPEG buffer (no cv2 encoding needed).
        stream = io.BytesIO()
        picam2.capture_file(stream, format="jpeg")
        img_bytes = stream.getvalue()

        # Forward the frame to the off-Pi vision server.
        response = requests.post(
            SERVER_URL,
            files={"image": img_bytes},
            timeout=10,
        )
        print(response.json())

    except Exception as e:
        print("Send failed:", e)

    time.sleep(2)