/*
 * sensor_scan.ino — HC-SR04 pin-scan diagnostic for Arduino Mega 2560
 *
 * Purpose: read all 8 parking-spot ultrasonic sensors and print each one's
 * measured distance (cm) once per second as plain serial lines, e.g.:
 *     S1=23cm  S2=TIMEOUT  S3=14cm  S4=8cm ...
 *
 * Wave a hand in front of each sensor: a working sensor's reading should
 * drop. A dead sensor shows constant TIMEOUT / 0 / garbage. Use this to
 * identify which 2 of the 8 sensors are defective.
 *
 * This sketch is DUMB by design: it only reads sensors and prints. No logic,
 * no thresholds, no state — all of that lives on the Pi.
 *
 * Baud: 115200 (match the Pi serial bridge).
 */

const uint8_t NUM_SENSORS = 8;

// Pin map (Trig, Echo) for the 8 parking-spot sensors, per hardware notes.
const uint8_t TRIG_PINS[NUM_SENSORS] = { 12, 26, 46, 22, 18, 14, 38, 34 };
const uint8_t ECHO_PINS[NUM_SENSORS] = { 13, 27, 47, 23, 19, 15, 39, 35 };

// Echo timeout in microseconds. ~25000us ≈ 4.3m max range; beyond that we
// treat the reading as TIMEOUT (no echo / out of range / dead sensor).
const unsigned long ECHO_TIMEOUT_US = 25000UL;

void setup() {
  Serial.begin(115200);
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    pinMode(TRIG_PINS[i], OUTPUT);
    pinMode(ECHO_PINS[i], INPUT);
    digitalWrite(TRIG_PINS[i], LOW);
  }
  delay(50);
  Serial.println("# sensor_scan ready: S1..S8 distance in cm, once/sec");
}

// Returns distance in cm, or -1 on timeout / no echo.
long readDistanceCm(uint8_t trigPin, uint8_t echoPin) {
  digitalWrite(trigPin, LOW);
  delayMicroseconds(2);
  digitalWrite(trigPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(trigPin, LOW);

  unsigned long duration = pulseIn(echoPin, HIGH, ECHO_TIMEOUT_US);
  if (duration == 0) {
    return -1; // timeout: no echo received within window
  }
  // Speed of sound ≈ 343 m/s → 0.0343 cm/us, round trip → divide by 2.
  return (long)(duration * 0.0343 / 2.0);
}

void loop() {
  for (uint8_t i = 0; i < NUM_SENSORS; i++) {
    long d = readDistanceCm(TRIG_PINS[i], ECHO_PINS[i]);
    Serial.print('S');
    Serial.print(i + 1);
    Serial.print('=');
    if (d < 0) {
      Serial.print("TIMEOUT");
    } else {
      Serial.print(d);
      Serial.print("cm");
    }
    if (i < NUM_SENSORS - 1) Serial.print("  ");
    // Small settle delay between sensors to avoid cross-echo interference.
    delay(30);
  }
  Serial.println();
  delay(1000);
}
