#include <Arduino.h>

#define ESTOP_PIN 13
#define REPORT_PERIOD_MS 20

int last_state = -1;
unsigned long last_report = 0;

void setup() {
  pinMode(ESTOP_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  delay(200);
  Serial.println("ESTOP,BOOT");
}

void loop() {
  int state = digitalRead(ESTOP_PIN) == HIGH ? 1 : 0;
  unsigned long now = millis();

  if (state != last_state || (now - last_report) >= REPORT_PERIOD_MS) {
    Serial.print("ESTOP,");
    Serial.println(state);
    last_state = state;
    last_report = now;
  }
}