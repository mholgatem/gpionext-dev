/// gpionext_core — PyO3 extension module
///
/// Exposes the following to Python:
///   - `GpioCore`         : lifecycle manager (start / stop / reload)
///   - `get_pin_states()` : returns current pressed-pin bitmask (for live UI)
///   - `version()`        : returns the crate semver string
///
/// Python usage:
/// ```python
/// import gpionext_core
/// core = gpionext_core.GpioCore()
/// core.start(config_dict)   # config_dict loaded from SQLite by SQL.py
/// # ... daemon sleeps, GPIO events are handled in Rust threads ...
/// core.reload(new_config_dict)  # called by SIGHUP handler
/// core.stop()
/// ```
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

mod bitmask;
mod gpio;
mod i2c;
mod uinput;

use bitmask::{EventType, Peripheral, build_config, init_pool, set_config};

// ---------------------------------------------------------------------------
// Module-level functions
// ---------------------------------------------------------------------------

/// Returns the semver version string of this compiled extension.
///
/// # Returns
/// `str` — e.g. `"0.1.0"`
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Returns the current pressed-pin bitmask as an integer.
/// Bit N is set when BOARD pin N is currently held down.
/// Called by `live_pin_view.py` every ~50ms to update the UI.
///
/// # Returns
/// `int` (u64) — bitmask of active pins
#[pyfunction]
fn get_pin_states() -> u64 {
    bitmask::current_bitmask()
}

/// Extract an optional scalar value from a Python dict, returning a Rust
/// default when the key is missing or the value cannot be converted.
///
/// # Parameters
/// - `dict`    : Python dict to read from
/// - `key`     : key to look up
/// - `default` : value returned when the key is missing or extraction fails
///
/// # Returns
/// The extracted Rust value or `default` when the field is absent/invalid.
fn extract_optional<T>(
    dict: &Bound<'_, PyDict>,
    key: &str,
    default: T,
) -> PyResult<T>
where
    T: for<'py> FromPyObject<'py>,
{
    Ok(match dict.get_item(key)? {
        Some(value) => value.extract::<T>().unwrap_or(default),
        None => default,
    })
}

/// Extract an optional `Vec<T>` from a Python dict, returning an empty vector
/// when the key is missing or the value cannot be converted.
///
/// # Parameters
/// - `dict`: Python dict to read from
/// - `key` : key to look up
///
/// # Returns
/// The extracted vector or an empty vector when the field is absent/invalid.
fn extract_optional_vec<T>(dict: &Bound<'_, PyDict>, key: &str) -> PyResult<Vec<T>>
where
    T: for<'py> FromPyObject<'py>,
{
    Ok(match dict.get_item(key)? {
        Some(value) => value.extract::<Vec<T>>().unwrap_or_default(),
        None => Vec::new(),
    })
}

// ---------------------------------------------------------------------------
// GpioCore — lifecycle manager
// ---------------------------------------------------------------------------

/// Lifecycle manager for the GPIOnext hot path.
///
/// Owns the Rayon thread pool, the GPIO event loop thread,
/// and the active configuration. All fields are managed internally;
/// Python only calls `start`, `stop`, and `reload`.
#[pyclass]
struct GpioCore {
    gpio_loop: Option<gpio::GpioLoop>,
}

#[pymethods]
impl GpioCore {
    #[new]
    fn new() -> Self {
        GpioCore { gpio_loop: None }
    }

    /// Start the GPIO event loop and initialise the Rayon thread pool.
    ///
    /// # Parameters
    /// - `config`: dict with keys:
    ///     - `peripherals` (list[dict]) — one dict per button/key/axis/command:
    ///         - `name` (str)
    ///         - `device_index` (int, 0-5)
    ///         - `pins` (list[int])  — BOARD pin numbers
    ///         - `type` (str)        — "BUTTON" | "KEY" | "AXIS" | "COMMAND"
    ///         - `command` (str|int) — evdev code or bash string
    ///     - `combo_delay` (int, ms)
    ///     - `key_hold_delay` (int, ms, default 350)
    ///     - `pins` (list[int])     — all BOARD pins to watch
    ///     - `pulldown` (bool)
    ///     - `debounce` (int, ms)
    ///     - `skip_pins` (list[int]) — pins reserved by audio HAT detection
    ///
    /// # Errors
    /// Raises `RuntimeError` if GPIO setup fails (missing module, bad pin, etc.)
    fn start(&mut self, config: &Bound<'_, PyDict>) -> PyResult<()> {
        let combo_delay: u64 = extract_optional(config, "combo_delay", 50)?;
        let key_hold_delay: u64 = extract_optional(config, "key_hold_delay", 350)?;
        let pulldown: bool = extract_optional(config, "pulldown", false)?;
        let debounce: u32 = extract_optional(config, "debounce", 1u32)?;

        let pins: Vec<u8> = extract_optional_vec(config, "pins")?;
        let skip_pins: Vec<u8> = extract_optional_vec(config, "skip_pins")?;

        // Parse peripheral list
        let peripherals = parse_peripherals(config)?;

        // Initialise fixed thread pool (8 workers; never grows)
        init_pool(8);

        // Install config into global state
        set_config(build_config(peripherals, combo_delay, key_hold_delay));

        // Start GPIO event loop (stub until libgpiod feature enabled)
        let gpio_config = gpio::GpioConfig { pins, pulldown, debounce_ms: debounce };
        match gpio::GpioLoop::run(&gpio_config, &skip_pins) {
            Ok(lp) => { self.gpio_loop = Some(lp); }
            Err(e) => return Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
        }

        Ok(())
    }

    /// Stop the GPIO event loop and flush all active uinput devices.
    fn stop(&mut self) -> PyResult<()> {
        if let Some(lp) = self.gpio_loop.take() {
            lp.stop();
        }
        uinput::close_all();
        Ok(())
    }

    /// Hot-reload configuration on SIGHUP without restarting the daemon.
    /// Stops the current event loop, swaps config, restarts the loop.
    ///
    /// # Parameters
    /// - `config`: freshly loaded config dict (same schema as `start`)
    fn reload(&mut self, config: &Bound<'_, PyDict>) -> PyResult<()> {
        self.stop()?;
        self.start(config)
    }

    /// Start a lightweight GPIO monitor for the config tool.
    ///
    /// Same as `start()` but creates NO uinput devices — only the GPIO event
    /// loop and bitmask tracking are initialised. Used by config_manager.py
    /// so it can poll `get_pin_states()` while the user presses buttons,
    /// without needing a full daemon running.
    ///
    /// # Parameters
    /// - `pins`     : list[int] — BOARD pin numbers to monitor
    /// - `pulldown` : bool — use pulldown instead of pullup resistors
    /// - `debounce` : int — debounce time in milliseconds
    ///
    /// # Errors
    /// Raises `RuntimeError` if GPIO setup fails.
    fn start_monitor(&mut self, pins: Vec<u8>, pulldown: bool, debounce: u32) -> PyResult<()> {
        init_pool(4); // smaller pool — config tool doesn't need combo resolution

        // Install an empty config (no peripherals) so bitmask tracking works
        // but dispatch_press is never called
        set_config(build_config(vec![], 50, 350));

        let gpio_config = gpio::GpioConfig { pins, pulldown, debounce_ms: debounce };
        match gpio::GpioLoop::run(&gpio_config, &[]) {
            Ok(lp) => { self.gpio_loop = Some(lp); }
            Err(e) => return Err(pyo3::exceptions::PyRuntimeError::new_err(e.to_string())),
        }
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Config parsing helpers
// ---------------------------------------------------------------------------

/// Parse the Python `peripherals` list from the config dict into typed Rust structs.
///
/// # Parameters
/// - `config`: the full config dict passed to `start()`
///
/// # Returns
/// `Vec<Peripheral>` — unsorted; `build_config` will sort them.
///
/// # Errors
/// Returns `PyValueError` if a peripheral dict is missing required keys or has
/// an unrecognised type string.
fn parse_peripherals(config: &Bound<'_, PyDict>) -> PyResult<Vec<Peripheral>> {
    use std::sync::atomic::{AtomicBool, AtomicU64};

    let raw_list = match config.get_item("peripherals")? {
        Some(v) => v,
        None => return Ok(Vec::new()),
    };
    let list = raw_list.downcast::<PyList>()?;

    let mut result = Vec::with_capacity(list.len());

    for item in list.iter() {
        let d = item.downcast::<PyDict>()?;

        let name: String = extract_optional(d, "name", String::new())?;
        let device_index: usize = extract_optional(d, "device_index", 0usize)?;
        let type_str: String = extract_optional(d, "type", String::new())?;
        let command: String = extract_optional(d, "command", String::new())?;
        let pins: Vec<u8> = extract_optional_vec(d, "pins")?;

        // Build pin bitmask from pin list
        let mut pin_mask: u64 = 0;
        for &pin in &pins {
            pin_mask |= 1u64 << pin;
        }
        let pin_count = pin_mask.count_ones() as u8;

        // Parse event type
        let event_type = match type_str.as_str() {
            "BUTTON" => EventType::Button {
                evdev_code: command.parse::<u32>().map_err(|_| {
                    pyo3::exceptions::PyValueError::new_err(
                        format!("BUTTON '{name}' command must be an integer evdev code, got '{command}'")
                    )
                })?,
            },
            "KEY" => EventType::Key {
                evdev_code: command.parse::<u32>().map_err(|_| {
                    pyo3::exceptions::PyValueError::new_err(
                        format!("KEY '{name}' command must be an integer evdev code, got '{command}'")
                    )
                })?,
            },
            "AXIS" => {
                // command is "(evdev_type, evdev_code, press_value)" — same as reference
                let (et, ec, pv) = parse_axis_command(&command, &name)?;
                EventType::Axis { evdev_type: et, evdev_code: ec, press_value: pv }
            }
            "COMMAND" => EventType::Command { bash: command.clone() },
            other => return Err(pyo3::exceptions::PyValueError::new_err(
                format!("Unknown peripheral type '{other}' for '{name}'")
            )),
        };

        result.push(Peripheral {
            name,
            device_index: device_index.min(5),
            pin_mask,
            pin_count,
            event_type,
            is_pressed: AtomicBool::new(false),
            hold_generation: AtomicU64::new(0),
        });
    }

    Ok(result)
}

/// Parse an AXIS command string in the format `"(evdev_type, evdev_code, value)"`.
/// Matches the format used by the reference config/constants.py AXIS tuples.
///
/// # Parameters
/// - `s`    : the raw command string from the config DB
/// - `name` : peripheral name, used in error messages only
///
/// # Returns
/// `(evdev_type, evdev_code, press_value)`
fn parse_axis_command(s: &str, name: &str) -> PyResult<(u32, u32, i32)> {
    // Strip parentheses and split on comma
    let inner = s.trim().trim_start_matches('(').trim_end_matches(')');
    let parts: Vec<&str> = inner.split(',').collect();
    if parts.len() != 3 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("AXIS '{name}' command must be '(type, code, value)', got '{s}'")
        ));
    }
    let et = parts[0].trim().parse::<u32>()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err(format!("AXIS '{name}' evdev_type not an int")))?;
    let ec = parts[1].trim().parse::<u32>()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err(format!("AXIS '{name}' evdev_code not an int")))?;
    let pv = parts[2].trim().parse::<i32>()
        .map_err(|_| pyo3::exceptions::PyValueError::new_err(format!("AXIS '{name}' press_value not an int")))?;
    Ok((et, ec, pv))
}

// ---------------------------------------------------------------------------
// PyO3 module registration
// ---------------------------------------------------------------------------

#[pymodule]
fn gpionext_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(get_pin_states, m)?)?;
    m.add_class::<GpioCore>()?;
    Ok(())
}
