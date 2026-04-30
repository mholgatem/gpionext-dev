# Phase 2 Implementation Plan: GPIO and uinput Stubs

## Objective
Activate the pending Rust stubs for the GPIO event loop and virtual HID writes. We will use an incremental approach, implementing and verifying the GPIO event loop first, followed by the uinput HID implementation.

## Key Files & Context
- `core/Cargo.toml`: Needs `libc` dependency added and `gpiocdev` verified.
- `core/src/gpio.rs`: Needs the `event_loop` logic implemented using `gpiocdev`.
- `core/src/uinput.rs`: Needs the actual `/dev/uinput` ioctl writes using `libc`.

## Implementation Steps

### Step 1: GPIO Event Loop (`gpiocdev`)
1. **Dependencies:** Ensure the `gpiocdev` dependency is correctly configured in `core/Cargo.toml` and active.
2. **Implementation (`core/src/gpio.rs`):**
   - Implement the `event_loop` function to correctly handle edge events from `gpiocdev`.
   - Update `GpioLoop::run` if necessary to properly request lines and start the thread.
3. **Verification (Step 1):**
   - Build the `gpionext_core` crate.
   - Run the daemon and trigger physical GPIO pins.
   - Verify that the `eprintln!` stubs in `uinput.rs` successfully log the expected press/release events.

### Step 2: Virtual HID Writes (`libc`)
1. **Dependencies:** Add the `libc` dependency to `core/Cargo.toml`.
2. **Implementation (`core/src/uinput.rs`):**
   - Set up the global file descriptors (e.g., using `OnceLock` and `parking_lot::Mutex`).
   - Implement the `write_key`, `write_abs`, and `write_sync` helpers.
   - Replace the `eprintln!` stubs in `dispatch_press` and `dispatch_release` with actual ioctl writes.
   - Implement device initialization in a new function (or update `open_devices`) and hook it into the startup flow.
3. **Verification (Step 2):**
   - Rebuild the crate and restart the daemon.
   - Use `evtest` or similar utilities to verify that virtual gamepad/keyboard events are accurately generated when physical pins are triggered.
