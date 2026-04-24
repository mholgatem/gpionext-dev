/// uinput HID write layer — creates and drives virtual input devices.
///
/// Opens /dev/uinput at daemon startup and holds the file descriptors open
/// for the entire daemon lifetime. No per-event open/close (reduces latency).
///
/// # Virtual devices created (4 joypads + 1 keyboard)
/// | device_index | Device name          | Type     | vendor | product |
/// |---|---|---|---|---|
/// | 0            | GPIOnext Joypad 1    | gamepad  | 0x9999 | 0x8888  |
/// | 1            | GPIOnext Joypad 2    | gamepad  | 0x9999 | 0x8888  |
/// | 2            | GPIOnext Joypad 3    | gamepad  | 0x9999 | 0x8888  |
/// | 3            | GPIOnext Joypad 4    | gamepad  | 0x9999 | 0x8888  |
/// | 4            | GPIOnext Keyboard    | keyboard | –      | –       |
/// | 5            | (Commands)           | none     | –      | –       |
///
/// Vendor 0x9999 / product 0x8888 matches the udev rule installed by
/// install.sh so SDL2 applications (emulators) see the joypads immediately
/// without additional configuration.
///
/// # Key hold (fixes thread explosion)
/// In the reference code, `Key.release()` creates a new `threading.Timer`
/// on every release. Here, one Rayon task per key hold loops on `is_pressed`
/// with a generation counter for cancellation — no new threads created.
///
/// Phase 3 implementation will use raw ioctl via libc or the `uinput` crate.
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

use crate::bitmask::{EventType, Peripheral};

// ---------------------------------------------------------------------------
// Public API called by bitmask.rs
// ---------------------------------------------------------------------------

/// Send a press event for a peripheral, then wait for release.
///
/// For Button and Axis types, blocks until the pin(s) are released (same
/// behaviour as reference `waitForRelease`). For Key, starts a hold task
/// in the Rayon pool and returns immediately; the hold task self-cancels
/// when `is_pressed` is cleared by `dispatch_release`.
///
/// For Command, executes the bash string in a Rayon task (non-blocking).
///
/// # Parameters
/// - `peripheral`      : the peripheral to activate
/// - `key_hold_delay`  : ms before keyboard key starts repeating (global setting)
pub fn dispatch_press(peripheral: &Arc<Peripheral>, key_hold_delay_ms: u64) {
    // Prevent double-press if already active (can happen with bounce or
    // overlapping combo windows)
    if peripheral.is_pressed.swap(true, Ordering::SeqCst) {
        return;
    }

    match &peripheral.event_type {
        EventType::Button { evdev_code } => {
            // Phase 3: write EV_KEY press to the device's uinput fd
            // write_key(device_fd(peripheral.device_index), *evdev_code, 1);
            // write_sync(device_fd(peripheral.device_index));
            // Block until released — avoids a queued release being skipped
            wait_for_release(peripheral);
            eprintln!("[uinput stub] BUTTON press: {} (code {})", peripheral.name, evdev_code);
        }
        EventType::Key { evdev_code } => {
            // Phase 3: write EV_KEY press
            // write_key(device_fd(peripheral.device_index), *evdev_code, 1);
            // write_sync(device_fd(peripheral.device_index));

            // Start hold task — increments generation so old tasks self-cancel
            let code = *evdev_code;
            let gen = peripheral.hold_generation.fetch_add(1, Ordering::SeqCst) + 1;
            let p = peripheral.clone();
            if let Some(pool) = crate::bitmask::get_pool() {
                pool.spawn(move || {
                    std::thread::sleep(Duration::from_millis(key_hold_delay_ms));
                    // If generation changed, a newer hold task is responsible
                    while p.hold_generation.load(Ordering::Relaxed) == gen
                        && p.is_pressed.load(Ordering::Relaxed)
                    {
                        // Phase 3: write EV_KEY repeat (value=2)
                        // write_key(device_fd(p.device_index), code, 2);
                        // write_sync(device_fd(p.device_index));
                        let _ = code; // suppress unused until Phase 3
                        std::thread::sleep(Duration::from_millis(33)); // ~30 Hz
                    }
                });
            }
            // Don't block for Key — release comes via dispatch_release()
        }
        EventType::Axis { evdev_type, evdev_code, press_value } => {
            // Phase 3: write EV_ABS
            // write_abs(device_fd(peripheral.device_index), *evdev_code, *press_value);
            // write_sync(device_fd(peripheral.device_index));
            eprintln!("[uinput stub] AXIS press: {} (type={} code={} val={})",
                peripheral.name, evdev_type, evdev_code, press_value);
            wait_for_release(peripheral);
        }
        EventType::Command { bash } => {
            // Execute shell command in Rayon (non-blocking, no uinput event)
            let cmd = bash.clone();
            let name = peripheral.name.clone();
            if let Some(pool) = crate::bitmask::get_pool() {
                pool.spawn(move || {
                    for part in cmd.split("; ") {
                        // Phase 3: std::process::Command::new("/bin/bash")
                        //     .args(["-c", part])
                        //     .spawn();
                        eprintln!("[uinput stub] COMMAND '{}': {}", name, part);
                    }
                });
            }
            peripheral.is_pressed.store(false, Ordering::Relaxed);
        }
    }
}

/// Send a release event for a peripheral. Sets `is_pressed = false` so that
/// any active hold task exits its loop at the next iteration.
///
/// # Parameters
/// - `peripheral`: the peripheral to deactivate
pub fn dispatch_release(peripheral: &Arc<Peripheral>) {
    if !peripheral.is_pressed.swap(false, Ordering::SeqCst) {
        return; // already released
    }
    match &peripheral.event_type {
        EventType::Button { evdev_code } => {
            // Phase 3: write EV_KEY release
            // write_key(device_fd(peripheral.device_index), *evdev_code, 0);
            // write_sync(device_fd(peripheral.device_index));
            eprintln!("[uinput stub] BUTTON release: {} (code {})", peripheral.name, evdev_code);
        }
        EventType::Key { evdev_code } => {
            // Hold task will self-exit on next loop check (is_pressed = false).
            // Phase 3: write EV_KEY release
            // write_key(device_fd(peripheral.device_index), *evdev_code, 0);
            // write_sync(device_fd(peripheral.device_index));
            eprintln!("[uinput stub] KEY release: {} (code {})", peripheral.name, evdev_code);
        }
        EventType::Axis { evdev_code, .. } => {
            // Phase 3: write EV_ABS 0 (centre)
            // write_abs(device_fd(peripheral.device_index), *evdev_code, 0);
            // write_sync(device_fd(peripheral.device_index));
            eprintln!("[uinput stub] AXIS release: {} (code {})", peripheral.name, evdev_code);
        }
        EventType::Command { .. } => {}
    }
}

/// Close all open uinput file descriptors gracefully.
/// Called by `GpioCore::stop()` and `GpioCore::reload()`.
pub fn close_all() {
    // Phase 3: iterate device_fds and close each fd
    // for fd in DEVICE_FDS.lock().iter() { unsafe { libc::close(*fd); } }
    eprintln!("[uinput stub] close_all()");
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/// Block until `peripheral.is_pressed` is cleared by `dispatch_release()`.
/// Used by Button and Axis press handlers to hold the Rayon worker until
/// the physical button is released, preventing a second press being queued
/// before the first action completes.
///
/// # Parameters
/// - `peripheral`: the peripheral whose `is_pressed` flag to poll
fn wait_for_release(peripheral: &Arc<Peripheral>) {
    while peripheral.is_pressed.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(10));
    }
}

// ---------------------------------------------------------------------------
// Phase 3 implementation notes
// ---------------------------------------------------------------------------
//
// Global device fd table (one entry per device_index):
//   static DEVICE_FDS: OnceLock<parking_lot::Mutex<[RawFd; 6]>> = OnceLock::new();
//
// Initialisation (called from GpioCore::start after config is loaded):
//   open_devices(device_capabilities) where capabilities are derived from
//   the peripheral list (which EV_KEY codes, which EV_ABS axes, etc.)
//
// write_key(fd, code, value):
//   let ev = input_event { type: EV_KEY, code, value, time: timeval::now() };
//   libc::write(fd, &ev as *const _ as *const libc::c_void, mem::size_of::<input_event>());
//
// write_abs(fd, code, value):
//   same as write_key but type = EV_ABS
//
// write_sync(fd):
//   let ev = input_event { type: EV_SYN, code: SYN_REPORT, value: 0, ... };
//   libc::write(fd, ...);
//
// Axis range: -255 to +255, flat zone 15 (matches reference JOYSTICK_AXIS AbsInfo)
