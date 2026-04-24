/// i2c device drivers: MCP23017 GPIO expander and ADS1115 ADC.
///
/// Both chips implement the `IoPin` trait so the rest of the system
/// (bitmask engine, config UI) treats i2c pins identically to physical GPIO pins.
///
/// # Pin naming convention
/// i2c pins are identified by string IDs that also appear in the config UI:
/// - MCP23017 pin A0 at address 0x20: `"i2c-0x20-A0"`
/// - MCP23017 pin B7 at address 0x27: `"i2c-0x27-B7"`
/// - ADS1115 channel 2 at address 0x4A: `"i2c-0x4A-ch2"`
///
/// BOARD pins 3 (SDA) and 5 (SCL) are used by the i2c bus itself and are
/// excluded from GPIO event detection in gpio.rs when the i2c feature is active.
///
/// # MCP23017 — GPIO expander
/// - 16 bidirectional I/O pins: port A (A0-A7) and port B (B0-B7)
/// - Up to 8 chips per bus (addresses 0x20-0x27) = 128 extra digital pins
/// - Configured as all inputs with internal pullups (IOCON.MIRROR=1)
/// - Interrupt-on-change: one GPIO INT pin wakes the Rust poll thread instead
///   of constant polling. Automatically falls back to ~1ms polling if no INT pin.
/// - Max i2c clock: 400 kHz (Fast Mode), set via `raspi-config` or /boot/config.txt
///
/// # ADS1115 — 4-channel 16-bit ADC
/// - 4 single-ended input channels (AIN0-AIN3) per chip
/// - Up to 4 chips per bus (addresses 0x48-0x4B) = 16 analog channels
/// - Used for analog joystick axes; each channel maps to an EV_ABS axis in uinput
/// - Continuous conversion mode at 250 SPS; polled at ~100 Hz in a Rayon task
/// - Range: ±4.096 V (PGA=±4.096V), scaled to -255..+255 for the joystick axis
///
/// # i2c feature gate
/// All hardware i2c access is gated behind the `i2c` Cargo feature (requires
/// `i2cdev` crate). The trait and type stubs below compile without the feature.

// ---------------------------------------------------------------------------
// IoPin trait — common interface for GPIO and i2c pins
// ---------------------------------------------------------------------------

/// Common interface implemented by all pin types:
/// - Physical GPIO pins (gpio.rs, via libgpiod)
/// - MCP23017 digital input pins (`Mcp23017Pin`)
/// - ADS1115 analog channels (`Ads1115Channel`, digital via threshold)
///
/// The bitmask engine (bitmask.rs) and config UI work exclusively with `IoPin`
/// references, making the underlying hardware transparent.
pub trait IoPin: Send + Sync {
    /// Canonical string identifier as shown in the config UI and stored in the DB.
    ///
    /// # Returns
    /// e.g. `"11"` for BOARD pin 11, `"i2c-0x20-A0"` for MCP23017.
    fn pin_id(&self) -> String;

    /// True when the pin is in the active (pressed / triggered) state.
    /// For digital pins: low when pulled up, high when pulled down.
    /// For ADS1115: true when `|read_analog()| > threshold`.
    fn is_pressed(&self) -> bool;

    /// Raw analog reading in the range -32768..32767.
    /// Returns 0 for digital-only pins (physical GPIO and MCP23017).
    fn read_analog(&self) -> i16;

    /// BOARD pin number equivalent for this i2c pin, used to index bitmask.
    /// For i2c pins this is a virtual pin number > 40 to avoid collision with
    /// physical pins (MCP23017: 64+, ADS1115: 128+).
    fn virtual_pin(&self) -> u8;
}

// ---------------------------------------------------------------------------
// MCP23017 GPIO expander
// ---------------------------------------------------------------------------

/// A single digital input pin on an MCP23017 GPIO expander.
///
/// MCP23017 pins are assigned virtual BOARD numbers starting at 64:
///   - Chip 0x20 port A: virtual pins 64-71 (A0-A7)
///   - Chip 0x20 port B: virtual pins 72-79 (B0-B7)
///   - Chip 0x21 port A: virtual pins 80-87, etc.
///
/// This mapping ensures MCP23017 bitmask bits never collide with physical GPIO bits.
pub struct Mcp23017Pin {
    /// i2c bus number (usually 1 for Pi; /dev/i2c-1)
    pub bus: u8,
    /// i2c address of the chip (0x20-0x27)
    pub address: u8,
    /// Port character: 'A' or 'B'
    pub port: char,
    /// Bit index within the port (0-7)
    pub bit: u8,
    /// Virtual BOARD pin number (64+) assigned at config time
    pub vpin: u8,
}

impl Mcp23017Pin {
    /// Construct from address + port + bit, assigning a virtual pin number.
    ///
    /// # Parameters
    /// - `bus`    : i2c bus number (usually 1)
    /// - `address`: chip i2c address (0x20-0x27)
    /// - `port`   : 'A' or 'B'
    /// - `bit`    : 0-7
    ///
    /// # Returns
    /// A new pin with `vpin` computed as `64 + (address-0x20)*16 + port_offset + bit`.
    pub fn new(bus: u8, address: u8, port: char, bit: u8) -> Self {
        let chip_offset = (address.saturating_sub(0x20)) as u8 * 16;
        let port_offset: u8 = if port == 'A' { 0 } else { 8 };
        let vpin = 64 + chip_offset + port_offset + bit;
        Mcp23017Pin { bus, address, port, bit, vpin }
    }
}

impl IoPin for Mcp23017Pin {
    fn pin_id(&self) -> String {
        format!("i2c-0x{:02X}-{}{}", self.address, self.port, self.bit)
    }

    fn is_pressed(&self) -> bool {
        #[cfg(feature = "i2c")]
        {
            // Phase 3: read GPIO register from MCP23017
            // let reg = if self.port == 'A' { REG_GPIOA } else { REG_GPIOB };
            // let byte = i2c_read_byte(self.bus, self.address, reg).unwrap_or(0xFF);
            // (byte >> self.bit) & 1 == 0  // active-low with pullups
        }
        false // stub
    }

    fn read_analog(&self) -> i16 { 0 }

    fn virtual_pin(&self) -> u8 { self.vpin }
}

/// Manages a complete MCP23017 chip: discovers all 16 pins, configures
/// registers, and optionally sets up interrupt-driven reads.
pub struct Mcp23017 {
    /// i2c bus number
    pub bus: u8,
    /// Chip i2c address (0x20-0x27)
    pub address: u8,
    /// Optional GPIO interrupt pin (BOARD number) for INT-driven reads.
    /// `None` → polling mode (~1ms interval in a Rayon task).
    pub int_pin: Option<u8>,
    /// All 16 pins (A0-A7, B0-B7) as `IoPin` instances
    pub pins: Vec<Mcp23017Pin>,
}

impl Mcp23017 {
    /// Construct and initialise an MCP23017 chip.
    ///
    /// Configures:
    /// - IODIR A+B = 0xFF (all inputs)
    /// - GPPU  A+B = 0xFF (all pullups enabled)
    /// - IOCON.MIRROR = 1 (INT pins mirrored so either INT pin signals any change)
    /// - INTCON A+B = 0x00 (interrupt on change from previous state)
    /// - DEFVAL A+B = 0x00 (compare to previous state)
    ///
    /// # Parameters
    /// - `bus`    : i2c bus number (usually 1)
    /// - `address`: chip i2c address (0x20-0x27)
    /// - `int_pin`: optional BOARD GPIO pin connected to chip's INTA/INTB
    ///
    /// # Returns
    /// `Ok(Mcp23017)` on success; `Err` if the chip is not found on the bus.
    pub fn new(bus: u8, address: u8, int_pin: Option<u8>) -> Result<Self, I2cError> {
        #[cfg(feature = "i2c")]
        {
            // Phase 3: open /dev/i2c-{bus} and configure registers
            // use i2cdev::linux::LinuxI2CDevice;
            // let mut dev = LinuxI2CDevice::new(format!("/dev/i2c-{bus}"), address as u16)?;
            // dev.smbus_write_byte_data(REG_IOCON, 0x40)?;  // MIRROR=1
            // dev.smbus_write_byte_data(REG_IODIRA, 0xFF)?; // all inputs
            // dev.smbus_write_byte_data(REG_IODIRB, 0xFF)?;
            // dev.smbus_write_byte_data(REG_GPPUA,  0xFF)?; // all pullups
            // dev.smbus_write_byte_data(REG_GPPUB,  0xFF)?;
        }

        let pins: Vec<Mcp23017Pin> = (0u8..8).map(|b| Mcp23017Pin::new(bus, address, 'A', b))
            .chain((0u8..8).map(|b| Mcp23017Pin::new(bus, address, 'B', b)))
            .collect();

        Ok(Mcp23017 { bus, address, int_pin, pins })
    }

    /// Scan the i2c bus for MCP23017 chips at addresses 0x20-0x27.
    ///
    /// # Parameters
    /// - `bus`: i2c bus number (usually 1)
    ///
    /// # Returns
    /// List of found i2c addresses.
    pub fn scan(bus: u8) -> Vec<u8> {
        let _ = bus;
        #[cfg(feature = "i2c")]
        {
            // Phase 3: probe each address with a zero-byte write
            // (0x20u8..=0x27).filter(|&addr| probe_i2c(bus, addr).is_ok()).collect()
        }
        vec![] // stub
    }
}

// ---------------------------------------------------------------------------
// ADS1115 ADC
// ---------------------------------------------------------------------------

/// A single analog input channel on an ADS1115 ADC.
///
/// ADS1115 channels are assigned virtual BOARD numbers starting at 128:
///   - Chip 0x48 ch0-ch3: virtual pins 128-131
///   - Chip 0x49 ch0-ch3: virtual pins 132-135, etc.
pub struct Ads1115Channel {
    /// i2c bus number
    pub bus: u8,
    /// Chip i2c address (0x48-0x4B)
    pub address: u8,
    /// Channel index (0-3 = AIN0-AIN3)
    pub channel: u8,
    /// Virtual BOARD pin number (128+)
    pub vpin: u8,
    /// Raw ADC value below this magnitude is treated as "centre" (not pressed).
    /// Expressed in ADC counts; default ≈ 2048 (half of 32767 for ±4.096V).
    pub dead_zone: i16,
}

impl Ads1115Channel {
    /// Construct a channel reference.
    ///
    /// # Parameters
    /// - `bus`      : i2c bus number
    /// - `address`  : chip address (0x48-0x4B)
    /// - `channel`  : 0-3 (maps to AIN0-AIN3 in single-ended mode)
    /// - `dead_zone`: raw ADC units to treat as centre; default 2048
    pub fn new(bus: u8, address: u8, channel: u8, dead_zone: i16) -> Self {
        let chip_offset = (address.saturating_sub(0x48)) * 4;
        let vpin = 128 + chip_offset + channel;
        Ads1115Channel { bus, address, channel, vpin, dead_zone }
    }

    /// Scale a raw ADS1115 reading (-32768..32767) to joystick axis range (-255..255).
    ///
    /// # Parameters
    /// - `raw`: raw 16-bit signed ADC value
    ///
    /// # Returns
    /// Scaled value clamped to -255..255.
    pub fn scale_to_axis(raw: i16) -> i32 {
        // Linear scale: 32767 → 255, -32768 → -255
        ((raw as i32) * 255) / 32767
    }
}

impl IoPin for Ads1115Channel {
    fn pin_id(&self) -> String {
        format!("i2c-0x{:02X}-ch{}", self.address, self.channel)
    }

    fn is_pressed(&self) -> bool {
        self.read_analog().abs() > self.dead_zone
    }

    fn read_analog(&self) -> i16 {
        #[cfg(feature = "i2c")]
        {
            // Phase 3: read conversion register from ADS1115
            // let config = ads1115_config(self.channel, PGA_4_096V, DR_250SPS, OS_SINGLE);
            // i2c_write_word(self.bus, self.address, REG_CONFIG, config)?;
            // std::thread::sleep(Duration::from_millis(5)); // wait for conversion
            // i2c_read_word(self.bus, self.address, REG_CONVERSION).unwrap_or(0)
        }
        0 // stub
    }

    fn virtual_pin(&self) -> u8 { self.vpin }
}

// ---------------------------------------------------------------------------
// Error type
// ---------------------------------------------------------------------------

/// Errors that can occur during i2c device setup or reads.
#[derive(Debug)]
pub enum I2cError {
    /// /dev/i2c-N could not be opened (kernel module not loaded, wrong bus)
    BusOpenFailed { bus: u8, reason: String },
    /// No device responded at the given address (not connected or wrong address)
    DeviceNotFound { bus: u8, address: u8 },
    /// Register read/write failed (bus error, device reset)
    IoError { address: u8, reason: String },
}

impl std::fmt::Display for I2cError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            I2cError::BusOpenFailed { bus, reason } =>
                write!(f, "Cannot open /dev/i2c-{bus}: {reason}. Run 'raspi-config' → Interface Options → I2C."),
            I2cError::DeviceNotFound { bus, address } =>
                write!(f, "No i2c device at address 0x{address:02X} on bus {bus}. Check wiring and 'i2cdetect -y {bus}'."),
            I2cError::IoError { address, reason } =>
                write!(f, "i2c I/O error on device 0x{address:02X}: {reason}"),
        }
    }
}

// ---------------------------------------------------------------------------
// MCP23017 register map (for reference / Phase 3 implementation)
// ---------------------------------------------------------------------------
//
// IOCON.BANK=0 (default after power-on):
//   0x00 IODIRA   — I/O direction port A (1=input, 0=output)
//   0x01 IODIRB   — I/O direction port B
//   0x02 IPOLA    — Input polarity port A
//   0x03 IPOLB
//   0x04 GPINTENA — Interrupt-on-change enable port A
//   0x05 GPINTENB
//   0x06 DEFVALA  — Default compare value port A
//   0x07 DEFVALB
//   0x08 INTCONA  — Interrupt control port A (0=change, 1=compare DEFVAL)
//   0x09 INTCONB
//   0x0A IOCON    — Configuration register
//   0x0B IOCON    — (mirror)
//   0x0C GPPUA    — Pull-up resistors port A
//   0x0D GPPUB
//   0x0E INTFA    — Interrupt flag port A (read-only)
//   0x0F INTFB
//   0x10 INTCAPA  — Interrupt capture port A (latched at interrupt time)
//   0x11 INTCAPB
//   0x12 GPIOA    — Port A GPIO state (read)
//   0x13 GPIOB
//   0x14 OLATA    — Output latch port A (write)
//   0x15 OLATB
//
// IOCON bits:
//   bit 6 MIRROR — 1: INTA/INTB are mirrored (either signals)
//   bit 2 ODR    — 1: INT pin is open-drain
//   bit 1 INTPOL — 1: INT active-high, 0: active-low
