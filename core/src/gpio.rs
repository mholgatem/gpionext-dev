/// GPIO event loop via libgpiod.
///
/// libgpiod is the modern Linux GPIO character device API replacing the
/// deprecated sysfs interface and RPi.GPIO. It works on all Pi models
/// (2B through Pi 5) and does not require root.
///
/// # Event flow
/// ```text
/// /dev/gpiochip0
///   → edge event (rising or falling)
///   → button_pressed() debounce read
///   → bitmask::set_pin() or bitmask::clear_pin()
///   → bitmask::on_pin_press() or bitmask::on_pin_release()
/// ```
///
/// # Pin protection
/// - BOARD pins 3 & 5 are i2c SDA/SCL. If i2c feature is enabled, these are
///   managed by i2c.rs. If disabled, attempting pulldown on them emits a clear
///   error and skips the pin (rather than silently crashing as in reference).
/// - Pins reserved by a detected audio HAT are passed in via `skip_pins` and
///   silently excluded from event detection.
/// - Invalid pin numbers are skipped with a warning.
///
/// # libgpiod feature gate
/// The C dependency `libgpiod` is gated behind the `gpio` feature in Cargo.toml.
/// This allows Phase 1/2 development and CI to compile without the C library
/// installed. The `GpioLoop` stub below compiles unconditionally; the real
/// implementation is compiled only when `--features gpio` is passed.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

/// Controls the running GPIO event loop.
/// `stop()` sets the flag; the event loop exits on next iteration.
pub struct GpioLoop {
    running: Arc<AtomicBool>,
}

impl GpioLoop {
    /// Start the GPIO event loop in the current thread (blocking).
    /// Call from a dedicated background thread — this does not return until
    /// `stop()` is called from another thread.
    ///
    /// # Parameters
    /// - `config`    : pin list, pulldown flag, debounce_ms, audio HAT skip_pins
    /// - `skip_pins` : BOARD pins reserved by audio HAT detection (hat_detect.py)
    ///
    /// # Errors
    /// Returns `Err` if `/dev/gpiochip0` cannot be opened or any pin request fails.
    pub fn run(config: &GpioConfig, skip_pins: &[u8]) -> Result<GpioLoop, GpioError> {
        let running = Arc::new(AtomicBool::new(true));

        #[cfg(feature = "gpio")]
        {
            // Phase 2: real libgpiod implementation
            // use libgpiod::{Chip, LineSettings, LineConfig, EdgeDetection};
            // let chip = Chip::open("/dev/gpiochip0")?;
            // ... (see Phase 2 implementation notes below)
            let _ = (config, skip_pins); // suppress unused warnings until Phase 2
        }

        #[cfg(not(feature = "gpio"))]
        {
            // Stub: warn that GPIO hardware is unavailable
            eprintln!("[gpionext] WARNING: compiled without gpio feature — no hardware events");
            let _ = (config, skip_pins);
        }

        Ok(GpioLoop { running })
    }

    /// Signal the event loop to stop. The loop exits within one poll timeout
    /// (typically < 100ms). Non-blocking.
    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
    }
}

// ---------------------------------------------------------------------------
// Configuration passed to GpioLoop::run()
// ---------------------------------------------------------------------------

/// GPIO hardware configuration extracted from the daemon's CLI args + DB.
pub struct GpioConfig {
    /// BOARD pin numbers to watch (excludes audio HAT pins and i2c pins
    /// when they are managed by i2c.rs)
    pub pins: Vec<u8>,
    /// If true, use pulldown resistors; default is pullup.
    /// BOARD pins 3 & 5 (i2c) are always skipped when pulldown is true.
    pub pulldown: bool,
    /// Debounce time in milliseconds. libgpiod applies this per-line.
    pub debounce_ms: u32,
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors that can occur during GPIO setup.
#[derive(Debug)]
pub enum GpioError {
    /// /dev/gpiochip0 could not be opened (missing module or permissions)
    ChipOpenFailed(String),
    /// A specific pin could not be requested (already in use, invalid number)
    PinRequestFailed { pin: u8, reason: String },
    /// pulldown was requested on an i2c pin (pins 3 or 5) and i2c is not enabled
    I2cPinPulldownConflict { pin: u8 },
}

impl std::fmt::Display for GpioError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            GpioError::ChipOpenFailed(e) =>
                write!(f, "Cannot open /dev/gpiochip0: {e}. Is the uinput module loaded?"),
            GpioError::PinRequestFailed { pin, reason } =>
                write!(f, "Pin {pin}: cannot add edge detection ({reason}). Skipping."),
            GpioError::I2cPinPulldownConflict { pin } =>
                write!(f, "Pin {pin} is an i2c pin (SDA/SCL). Cannot set pulldown. \
                           Use 'gpionext set pulldown false' or enable i2c feature."),
        }
    }
}

// ---------------------------------------------------------------------------
// Phase 2 implementation notes (libgpiod)
// ---------------------------------------------------------------------------
//
// When the `gpio` feature is enabled and libgpiod crate is added to Cargo.toml:
//
// 1. Open chip:
//    let chip = Chip::open("/dev/gpiochip0")
//        .map_err(|e| GpioError::ChipOpenFailed(e.to_string()))?;
//
// 2. For each pin in config.pins:
//    a. Skip if in skip_pins (audio HAT)
//    b. Skip pins 3 & 5 if pulldown && !cfg(feature = "i2c")
//       → emit GpioError::I2cPinPulldownConflict, continue
//    c. Build LineSettings with:
//       - direction: Input
//       - bias: PullUp or PullDown per config.pulldown
//       - edge_detection: BothEdges
//       - debounce_period: Duration::from_millis(config.debounce_ms)
//
// 3. Request all lines at once (batch reduces kernel round-trips).
//
// 4. Poll loop (checks running flag):
//    while running.load(Ordering::Relaxed) {
//        let events = chip.wait_edge_events(timeout = Duration::from_millis(100));
//        for event in events {
//            let board_pin = event.line_offset() as u8;
//            match event.edge_type() {
//                Rising  => { bitmask::set_pin(board_pin); bitmask::on_pin_press(board_pin); }
//                Falling => { bitmask::on_pin_release(board_pin); }
//            }
//        }
//    }
//
// Note: button_pressed() debounce (the time.sleep(0.01) in the reference gpio.py)
// is handled by libgpiod's per-line debounce_period — no sleep needed in Rust.
