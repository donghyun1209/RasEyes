# RasEyes Agent Rules

## 1. Performance Constraints
- MUST implement lightweight model suitable for <60ms inference on target device
- MUST avoid per-frame heavy operations (e.g., full image copy, redundant conversions)
- MUST implement FPS monitoring logic
- MUST trigger fallback when FPS < config.MIN_FPS

## 2. Concurrency Rules
- MUST NOT use blocking calls in the main loop (e.g., time.sleep, synchronous I/O)
- MUST separate vision and ToF into independent execution units (thread or async task)
- MUST use queue-based communication between modules
- MUST protect shared resources with lock or queue

## 3. Fallback Logic (CRITICAL)
- MUST fallback to ToF-only mode IF:
  - vision confidence < config.VISION_CONF_THRESHOLD
  - OR FPS < config.MIN_FPS
- MUST return to vision mode IF:
  - vision confidence >= config.VISION_CONF_THRESHOLD
  - AND FPS >= config.MIN_FPS
- MUST ensure ToF-only mode works independently of vision pipeline

## 4. Configuration Rules
- MUST NOT hardcode thresholds or constants
- MUST load all tunable parameters from config.py
- MUST isolate hardware-specific values from logic

## 5. Architecture Rules
- MUST use HAL interfaces (BaseCameraHAL, BaseToFHAL, BaseAudioHAL)
- MUST NOT access hardware directly in business logic
- MUST support Mock and Real implementations without code changes

## 6. Edge Optimization Rules
- MUST NOT require full PyTorch runtime on Raspberry Pi
- MUST support lightweight inference backend (e.g., TFLite)
- SHOULD support MPS acceleration for Mac development

## 7. Stability Rules
- MUST handle exceptions without crashing the main loop
- MUST log all exceptions
- MUST continue operation if one module fails (camera, ToF, audio)
- SHOULD attempt reinitialization on hardware failure
