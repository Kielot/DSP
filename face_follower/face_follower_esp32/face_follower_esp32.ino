#include <WiFi.h>
#include <ESPmDNS.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>


// =====================================================
// WIFI
//
// PRESERVED.
// =====================================================

const char* ssid =
    "iPhone";

const char* password =
    "12345678";

const char* hostname =
    "esp32-robot";


// =====================================================
// TB6612FNG
//
// PRESERVED.
// =====================================================

// Motor A ("left" motor - see NOTE in driveDifferential())

#define AIN1 26
#define AIN2 27
#define PWMA 25


// Motor B ("right" motor)

#define BIN1 32
#define BIN2 33
#define PWMB 14


// Standby

#define STBY 13


// =====================================================
// MOTOR SPEED
//
// PRESERVED. Now used as each motor's max PWM (0-255)
// rather than a single fixed speed - driveDifferential()
// scales a -1.0..1.0 value by these.
// =====================================================

const int MOTOR_A_SPEED =
    200;

const int MOTOR_B_SPEED =
    195;


// =====================================================
// WEBSOCKET
//
// PRESERVED.
//
// Browser connects directly to:
// ws://esp32-robot.local:81
// =====================================================

WebSocketsServer webSocket(81);


// =====================================================
// CURRENT COMMAND
//
// PRESERVED.
// =====================================================

String currentCommand =
    "STOP";


// =====================================================
// COMMS FAILSAFE
//
// NEW (item 10). If no valid message - legacy OR
// tracking - arrives within COMMAND_TIMEOUT_MS, stop the
// motors. Protects against the PC or Wi-Fi dropping while
// the robot is mid-turn.
// =====================================================

const unsigned long COMMAND_TIMEOUT_MS =
    500;

unsigned long lastMessageTime =
    0;

bool failsafeTripped =
    false;


// =====================================================
// JSON TRACKING MESSAGE BUFFER
//
// NEW. Sized generously for a small flat JSON object -
// see the "tracking" payload shape in server.py.
// =====================================================

StaticJsonDocument<256> trackingDoc;


// =====================================================
// MOTOR FUNCTIONS
//
// PRESERVED - still used by the legacy discrete commands.
// =====================================================

void stopMotors() {

    digitalWrite(
        AIN1,
        LOW
    );

    digitalWrite(
        AIN2,
        LOW
    );

    digitalWrite(
        BIN1,
        LOW
    );

    digitalWrite(
        BIN2,
        LOW
    );

    analogWrite(
        PWMA,
        0
    );

    analogWrite(
        PWMB,
        0
    );
}


// =====================================================
// LEFT
//
// PRESERVED.
// =====================================================

void turnLeft() {

    digitalWrite(
        AIN1,
        LOW
    );

    digitalWrite(
        AIN2,
        HIGH
    );

    digitalWrite(
        BIN1,
        HIGH
    );

    digitalWrite(
        BIN2,
        LOW
    );

    analogWrite(
        PWMA,
        MOTOR_A_SPEED
    );

    analogWrite(
        PWMB,
        MOTOR_B_SPEED
    );
}


// =====================================================
// RIGHT
//
// PRESERVED.
// =====================================================

void turnRight() {

    digitalWrite(
        AIN1,
        HIGH
    );

    digitalWrite(
        AIN2,
        LOW
    );

    digitalWrite(
        BIN1,
        LOW
    );

    digitalWrite(
        BIN2,
        HIGH
    );

    analogWrite(
        PWMA,
        MOTOR_A_SPEED
    );

    analogWrite(
        PWMB,
        MOTOR_B_SPEED
    );
}


// =====================================================
// FORWARD
//
// PRESERVED.
// =====================================================

void moveForward() {

    digitalWrite(
        AIN1,
        HIGH
    );

    digitalWrite(
        AIN2,
        LOW
    );

    digitalWrite(
        BIN1,
        HIGH
    );

    digitalWrite(
        BIN2,
        LOW
    );

    analogWrite(
        PWMA,
        MOTOR_A_SPEED
    );

    analogWrite(
        PWMB,
        MOTOR_B_SPEED
    );
}


// =====================================================
// BACKWARD
//
// PRESERVED.
// =====================================================

void moveBackward() {

    digitalWrite(
        AIN1,
        LOW
    );

    digitalWrite(
        AIN2,
        HIGH
    );

    digitalWrite(
        BIN1,
        LOW
    );

    digitalWrite(
        BIN2,
        HIGH
    );

    analogWrite(
        PWMA,
        MOTOR_A_SPEED
    );

    analogWrite(
        PWMB,
        MOTOR_B_SPEED
    );
}


// =====================================================
// DIFFERENTIAL DRIVE
//
// NEW (item 9). Converts a speed/steering pair from the
// tracker into per-motor PWM + direction.
//
// NOTE ON left/right MAPPING:
// Comparing this to the existing turnLeft()/turnRight()
// above shows Motor A already behaves as the "left" wheel
// and Motor B as the "right" wheel on this chassis -
// turnLeft() reverses A while driving B forward, which is
// exactly what left_motor = speed + steering produces when
// steering is negative. So A = left_motor, B = right_motor,
// no sign flip needed. If your robot turns the wrong way,
// swap the two lines that call setMotorA/setMotorB below.
// =====================================================

void driveDifferential(float speed, float steering) {

    speed = constrain(
        speed,
        -1.0,
        1.0
    );

    steering = constrain(
        steering,
        -1.0,
        1.0
    );

    float leftValue = constrain(
        speed + steering,
        -1.0,
        1.0
    );

    float rightValue = constrain(
        speed - steering,
        -1.0,
        1.0
    );

    setMotorA(leftValue);

    setMotorB(rightValue);
}


void setMotorA(float value) {

    if (value > 0) {

        digitalWrite(AIN1, HIGH);
        digitalWrite(AIN2, LOW);

    } else if (value < 0) {

        digitalWrite(AIN1, LOW);
        digitalWrite(AIN2, HIGH);

    } else {

        digitalWrite(AIN1, LOW);
        digitalWrite(AIN2, LOW);
    }

    int pwm = (int)(fabs(value) * MOTOR_A_SPEED);

    analogWrite(PWMA, pwm);
}


void setMotorB(float value) {

    if (value > 0) {

        digitalWrite(BIN1, HIGH);
        digitalWrite(BIN2, LOW);

    } else if (value < 0) {

        digitalWrite(BIN1, LOW);
        digitalWrite(BIN2, HIGH);

    } else {

        digitalWrite(BIN1, LOW);
        digitalWrite(BIN2, LOW);
    }

    int pwm = (int)(fabs(value) * MOTOR_B_SPEED);

    analogWrite(PWMB, pwm);
}


// =====================================================
// EXECUTE LEGACY COMMAND
//
// PRESERVED.
// =====================================================

bool executeCommand(
    String command
) {

    command.toUpperCase();

    command.trim();


    // -------------------------------------------------
    // LEFT
    // -------------------------------------------------

    if (
        command ==
        "LEFT"
    ) {

        turnLeft();
    }


    // -------------------------------------------------
    // RIGHT
    // -------------------------------------------------

    else if (
        command ==
        "RIGHT"
    ) {

        turnRight();
    }


    // -------------------------------------------------
    // FORWARD
    // -------------------------------------------------

    else if (
        command ==
        "FORWARD"
    ) {

        moveForward();
    }


    // -------------------------------------------------
    // BACKWARD
    // -------------------------------------------------

    else if (
        command ==
        "BACKWARD"
    ) {

        moveBackward();
    }


    // -------------------------------------------------
    // STOP
    // -------------------------------------------------

    else if (
        command ==
        "STOP"
    ) {

        stopMotors();
    }


    // -------------------------------------------------
    // INVALID
    // -------------------------------------------------

    else {

        return false;
    }


    currentCommand =
        command;


    return true;
}


// =====================================================
// EXECUTE TRACKING MESSAGE
//
// NEW (item 9). Handles the {"type":"tracking", ...}
// JSON messages sent continuously by server.py while
// autonomous following is active.
// =====================================================

void executeTrackingMessage(
    uint8_t* payload,
    size_t length
) {

    DeserializationError error =
        deserializeJson(
            trackingDoc,
            payload,
            length
        );

    if (error) {

        Serial.print(
            "[JSON] Parse failed: "
        );

        Serial.println(
            error.c_str()
        );

        return;
    }


    bool target =
        trackingDoc["target"] | false;

    float steering =
        trackingDoc["steering"] | 0.0;

    float speed =
        trackingDoc["speed"] | 0.0;

    const char* state =
        trackingDoc["state"] | "TARGET_LOST";


    if (
        !target ||
        strcmp(state, "TARGET_LOST") == 0
    ) {

        stopMotors();

    } else {

        driveDifferential(
            speed,
            steering
        );
    }


    currentCommand =
        String(state);
}


// =====================================================
// WEBSOCKET EVENT
//
// CHANGED. A text message is now routed by its first
// character: '{' means a JSON tracking message, anything
// else is handled exactly as before (legacy plain-string
// command). Both paths reset the failsafe timer.
// =====================================================

void webSocketEvent(
    uint8_t clientNum,
    WStype_t type,
    uint8_t* payload,
    size_t length
) {

    // =================================================
    // CONNECTED
    //
    // PRESERVED.
    // =================================================

    if (
        type ==
        WStype_CONNECTED
    ) {

        IPAddress ip =
            webSocket.remoteIP(
                clientNum
            );


        Serial.print(
            "[WebSocket] Client connected: "
        );

        Serial.println(
            ip
        );


        /*
         * Send current command.
         */

        webSocket.sendTXT(
            clientNum,
            currentCommand
        );


        return;
    }


    // =================================================
    // DISCONNECTED
    //
    // PRESERVED.
    // =================================================

    if (
        type ==
        WStype_DISCONNECTED
    ) {

        Serial.print(
            "[WebSocket] Client disconnected: "
        );

        Serial.println(
            clientNum
        );


        /*
         * SAFETY:
         *
         * Stop motors immediately.
         */

        stopMotors();


        currentCommand =
            "STOP";


        return;
    }


    // =================================================
    // TEXT MESSAGE
    //
    // CHANGED - now branches on JSON vs. legacy plain
    // text. See comment above webSocketEvent().
    // =================================================

    if (
        type ==
        WStype_TEXT
    ) {

        lastMessageTime =
            millis();

        failsafeTripped =
            false;


        if (
            length > 0 &&
            payload[0] == '{'
        ) {

            executeTrackingMessage(
                payload,
                length
            );

            return;
        }


        String command =
            String(
                (char*)payload
            );


        command.toUpperCase();

        command.trim();


        Serial.print(
            "[WebSocket] Received: "
        );

        Serial.println(
            command
        );


        // ---------------------------------------------
        // EXECUTE
        // ---------------------------------------------

        bool success =
            executeCommand(
                command
            );


        if (
            success
        ) {

            Serial.print(
                "[MOTOR] Executed: "
            );

            Serial.println(
                currentCommand
            );


            /*
             * Send confirmation to browser.
             */

            webSocket.sendTXT(
                clientNum,
                currentCommand
            );
        }


        else {

            Serial.print(
                "[ERROR] Invalid command: "
            );

            Serial.println(
                command
            );


            webSocket.sendTXT(
                clientNum,
                "ERROR"
            );
        }
    }
}


// =====================================================
// SETUP
//
// PRESERVED, apart from initializing lastMessageTime at
// the end.
// =====================================================

void setup() {

    Serial.begin(
        115200
    );


    delay(500);


    Serial.println();

    Serial.println(
        "========================================"
    );

    Serial.println(
        "PERSON FOLLOWING ROBOT"
    );

    Serial.println(
        "DIRECT WEBSOCKET CONTROL"
    );

    Serial.println(
        "========================================"
    );


    // =================================================
    // MOTOR PINS
    // =================================================

    pinMode(
        AIN1,
        OUTPUT
    );

    pinMode(
        AIN2,
        OUTPUT
    );

    pinMode(
        PWMA,
        OUTPUT
    );


    pinMode(
        BIN1,
        OUTPUT
    );

    pinMode(
        BIN2,
        OUTPUT
    );

    pinMode(
        PWMB,
        OUTPUT
    );


    pinMode(
        STBY,
        OUTPUT
    );


    // =================================================
    // ENABLE TB6612FNG
    // =================================================

    digitalWrite(
        STBY,
        HIGH
    );


    // =================================================
    // INITIAL STOP
    // =================================================

    stopMotors();


    currentCommand =
        "STOP";


    // =================================================
    // WIFI
    // =================================================

    WiFi.mode(
        WIFI_STA
    );


    WiFi.begin(
        ssid,
        password
    );


    Serial.println();

    Serial.print(
        "Connecting to Wi-Fi"
    );


    while (
        WiFi.status() !=
        WL_CONNECTED
    ) {

        delay(250);

        Serial.print(
            "."
        );
    }


    Serial.println();

    Serial.println(
        "Wi-Fi connected"
    );


    // =================================================
    // IP
    // =================================================

    Serial.print(
        "ESP32 IP: "
    );

    Serial.println(
        WiFi.localIP()
    );


    // =================================================
    // MDNS
    // =================================================

    if (
        MDNS.begin(
            hostname
        )
    ) {

        Serial.println(
            "mDNS started"
        );


        Serial.print(
            "Hostname: "
        );

        Serial.print(
            hostname
        );

        Serial.println(
            ".local"
        );
    }

    else {

        Serial.println(
            "mDNS failed"
        );
    }


    // =================================================
    // WEBSOCKET
    // =================================================

    webSocket.begin();


    webSocket.onEvent(
        webSocketEvent
    );


    Serial.println(
        "WebSocket server started"
    );


    // =================================================
    // FAILSAFE INIT
    // =================================================

    lastMessageTime =
        millis();


    // =================================================
    // READY
    // =================================================

    Serial.println();

    Serial.println(
        "========================================"
    );

    Serial.println(
        "ROBOT READY"
    );

    Serial.println(
        "========================================"
    );


    Serial.print(
        "WebSocket: ws://"
    );

    Serial.print(
        hostname
    );

    Serial.println(
        ".local:81"
    );


    Serial.print(
        "Motor PWM: "
    );

    Serial.print(
        MOTOR_A_SPEED
    );

    Serial.print(
        " / "
    );

    Serial.println(
        MOTOR_B_SPEED
    );


    Serial.println(
        "========================================"
    );
}


// =====================================================
// LOOP
//
// CHANGED - added the failsafe timeout check alongside
// the preserved webSocket.loop() call.
// =====================================================

void loop() {

    /*
     * Keep WebSocket alive.
     */

    webSocket.loop();


    /*
     * FAILSAFE (item 10): if we haven't heard from the
     * PC in COMMAND_TIMEOUT_MS, stop. Only logs once per
     * timeout event so it doesn't spam Serial.
     */

    if (
        !failsafeTripped &&
        (millis() - lastMessageTime) > COMMAND_TIMEOUT_MS
    ) {

        stopMotors();

        currentCommand =
            "STOP";

        failsafeTripped =
            true;

        Serial.println(
            "[FAILSAFE] No message in "
            "500ms - motors stopped"
        );
    }
}
