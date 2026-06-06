// parking_sensors.ino — PRODUCTION occupancy sketch for Hexa-Vision parking
// Arduino Mega 2560. Reads only the CONFIRMED-WORKING HC-SR04 sensors and emits
// an event line ONLY on a debounced occupancy state change:
//     SPOT:<spot_id>:<0|1>     (0 = vacant, 1 = occupied)
// plus a heartbeat line every 10s so the bridge knows the link is alive:
//     HB:<millis>
//
// ENTRANCE sensor (Trig 50, Echo 51): a separate HC-SR04 watching the entry lane.
// It does NOT represent a parking spot, so it NEVER emits a SPOT: line. Instead it
// emits its own debounced trigger line, consumed by the Pi capture script:
//     ENTRY:1   (object entered detection range — trigger camera capture)
//     ENTRY:0   (object left — reset, ready for the next vehicle)
//
// Confirmed working sensors (2026-05-31 hand-wave test): S2,S3,S4,S5,S6,S8.
// DEAD / EXCLUDED: S1, S7 (TIMEOUT on every read — hardware fault).
//
// Trig/Echo pins come from the diagnostic sketch (arduino/sensor_scan/sensor_scan.ino).
// The emitted spot_id is just a sequential 1..6 index over the working sensors
// (S2->1, S3->2, S4->3, S5->4, S6->5, S8->6); it is NOT the DB spot_id. serial_bridge.py
// (ARDUINO_TO_REAL_SPOT) remaps these to the real floor-plan numbers before calling the API.

#define BAUD 115200
#define OCCUPIED_THRESHOLD_CM 10  // <= this distance (cm) counts as "occupied".
                                  // Tuned to 10 cm for the demo testbed: tolerates
                                  // sensor blind spots and slight rig movement while
                                  // still reliably detecting a parked scale model.
#define DEBOUNCE_READS 3     // consecutive matching reads required to flip state
#define HEARTBEAT_MS 10000UL
#define READ_PERIOD_MS 200UL // per full-sweep cadence

const float SOUND_CM_PER_US = 0.0343f;
const unsigned long ECHO_TIMEOUT_US = 25000UL; // ~4m

// One entry per CONFIRMED sensor: {trig, echo, spot_id}
struct Sensor { uint8_t trig; uint8_t echo; uint8_t spot_id; };

const Sensor SENSORS[] = {
  {26, 27, 1},  // S2 -> spot_id 1
  {46, 47, 2},  // S3 -> spot_id 2
  {22, 23, 3},  // S4 -> spot_id 3
  {18, 19, 4},  // S5 -> spot_id 4
  {14, 15, 5},  // S6 -> spot_id 5
  {34, 35, 6},  // S8 -> spot_id 6
};
const uint8_t N = sizeof(SENSORS) / sizeof(SENSORS[0]);

// Entrance-lane sensor. Same HC-SR04 wiring convention as the spot sensors, but
// tracked independently so it can emit ENTRY: lines instead of SPOT: lines.
#define ENTRANCE_TRIG 50
#define ENTRANCE_ECHO 51

int8_t  state[6];        // -1 unknown, 0 vacant, 1 occupied
uint8_t streak[6];       // consecutive reads of the candidate state
int8_t  candidate[6];    // candidate state being debounced

// Independent debounce state for the entrance sensor (mirrors the per-spot logic).
int8_t  entryState = -1;     // -1 unknown, 0 clear, 1 object present
uint8_t entryStreak = 0;     // consecutive reads of the candidate state
int8_t  entryCandidate = -1; // candidate state being debounced

void setup() {
  Serial.begin(BAUD);
  while (!Serial) { ; }
  for (uint8_t i = 0; i < N; i++) {
    pinMode(SENSORS[i].trig, OUTPUT);
    pinMode(SENSORS[i].echo, INPUT);
    digitalWrite(SENSORS[i].trig, LOW);
    state[i] = -1;
    streak[i] = 0;
    candidate[i] = -1;
  }
  pinMode(ENTRANCE_TRIG, OUTPUT);
  pinMode(ENTRANCE_ECHO, INPUT);
  digitalWrite(ENTRANCE_TRIG, LOW);
}

void loop() {
  static unsigned long lastSweep = 0;
  static unsigned long lastHb = 0;
  unsigned long now = millis();

  if (now - lastHb >= HEARTBEAT_MS) {
    lastHb = now;
    Serial.print("HB:");
    Serial.println(now);
  }

  if (now - lastSweep < READ_PERIOD_MS) return;
  lastSweep = now;

  for (uint8_t i = 0; i < N; i++) {
    long d = readDistanceCm(SENSORS[i].trig, SENSORS[i].echo);
    // Treat TIMEOUT (d < 0) as "vacant" (no object within range).
    int8_t reading = (d >= 0 && d <= OCCUPIED_THRESHOLD_CM) ? 1 : 0;

    if (reading == candidate[i]) {
      if (streak[i] < 255) streak[i]++;
    } else {
      candidate[i] = reading;
      streak[i] = 1;
    }

    if (streak[i] >= DEBOUNCE_READS && state[i] != candidate[i]) {
      state[i] = candidate[i];
      Serial.print("SPOT:");
      Serial.print(SENSORS[i].spot_id);
      Serial.print(":");
      Serial.println(state[i]);
    }
  }

  // Entrance sensor: same threshold + debounce as the spots, but emits ENTRY:.
  {
    long d = readDistanceCm(ENTRANCE_TRIG, ENTRANCE_ECHO);
    int8_t reading = (d >= 0 && d <= OCCUPIED_THRESHOLD_CM) ? 1 : 0;

    if (reading == entryCandidate) {
      if (entryStreak < 255) entryStreak++;
    } else {
      entryCandidate = reading;
      entryStreak = 1;
    }

    if (entryStreak >= DEBOUNCE_READS && entryState != entryCandidate) {
      entryState = entryCandidate;
      Serial.print("ENTRY:");
      Serial.println(entryState);
    }
  }
}

// returns distance in cm, or -1 on echo timeout
long readDistanceCm(uint8_t trig, uint8_t echo) {
  digitalWrite(trig, LOW);
  delayMicroseconds(2);
  digitalWrite(trig, HIGH);
  delayMicroseconds(10);
  digitalWrite(trig, LOW);
  long dur = pulseIn(echo, HIGH, ECHO_TIMEOUT_US);
  if (dur == 0) return -1;
  return (long)(dur * SOUND_CM_PER_US / 2.0f);
}