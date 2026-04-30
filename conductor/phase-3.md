# Phase 3 Implementation Plan: I2C Support (MCP23017 and ADS1115)

## Objective
Implement hardware drivers for the MCP23017 GPIO expander and ADS1115 ADC using the `i2cdev` crate. This phase introduces a unified bitmask system capable of addressing virtual I2C pins, supports interrupt-driven hardware polling, enables batched register reads for maximum performance, and introduces a system-level I2C baudrate configuration.

## Key Files & Context
- `core/Cargo.toml`: Enable `i2cdev` dependency.
- `core/src/bitmask.rs`: Expand `GLOBAL_BITMASK` and `Peripheral.pin_mask`.
- `core/src/lib.rs`: Update configuration parsing and Python return types.
- `python/ui/live_pin_view.py`: Reconstruct the Python integer from the new tuple bitmask.
- `core/src/i2c.rs`: Implement the actual hardware I/O, batched reads, and polling/interrupt loops.
- `python/config/baudrate.py` (New): Utility to manage Pi I2C baudrate.

## Implementation Steps

### Step 1: 192-bit Bitmask Engine
1. **`core/src/bitmask.rs`:**
   - Change `GLOBAL_BITMASK` to `[AtomicU64; 3]`.
   - Change `Peripheral.pin_mask` to `[u64; 3]`.
   - Update `set_pin` and `clear_pin` to calculate the array index (`pin / 64`).
   - Update `current_bitmask` to return a tuple `(u64, u64, u64)`.
   - Update `bitmask_in` to verify all 3 64-bit words simultaneously.
2. **`core/src/lib.rs`:**
   - Update `parse_peripherals` to construct `[u64; 3]` from the list of pins.
   - Update `get_pin_states` to return `(u64, u64, u64)` to Python.
3. **`python/ui/live_pin_view.py`:**
   - Modify the `bitmask` reading logic to reconstruct the full Python integer:
     `bitmask = bitmask[0] | (bitmask[1] << 64) | (bitmask[2] << 128)`

### Step 2: I2C Baudrate Configuration
1. **Configuration UI:**
   - Create a Python utility to modify `/boot/firmware/config.txt` (or `/boot/config.txt` on older systems) to set `dtparam=i2c_arm_baudrate=X`.
   - Provide two tiers: Default (100kHz) and Fast (400kHz).
   - Implement a clear warning that this setting is only for advanced users who know what they are doing.
   - If the baudrate is changed, alert the user that a system reboot is required.

### Step 3: I2C Hardware Drivers (MCP23017 & ADS1115)
1. **Dependencies:** Uncomment `i2cdev = { version = "0.6", optional = true }` in `Cargo.toml`.
2. **Batched Reads & Initialization (`core/src/i2c.rs`):**
   - **MCP23017:**
     - Initialize registers for inputs, pullups, and mirror interrupts (`IOCON.MIRROR = 1`).
     - **Batching:** Use a single 16-bit word read (`i2c_smbus_read_word_data`) on `GPIOA` (0x12) to fetch the state of all 16 pins simultaneously (since `GPIOB` is at 0x13 and `BANK=0`).
   - **ADS1115:**
     - Initialize the config register for continuous conversion mode.
     - **Batching:** Read the entire 16-bit conversion register in a single transaction.
3. **Interrupt vs Polling Logic:**
   - **Interrupt Mode:** If `int_pin` is provided in the configuration, spawn a dedicated background thread that uses `gpiocdev` to block and wait for a falling/rising edge on the specified physical BOARD pin. Upon edge detection, execute the batched I2C read.
   - **Polling Mode:** If `int_pin` is omitted, fall back to a standard continuous polling loop within a Rayon task (e.g., sleeping ~1ms between reads).
4. **Integration (`core/src/lib.rs`):**
   - Parse I2C devices and the optional interrupt pin from the config.
   - Instantiate the drivers and spawn their respective monitoring tasks when starting the core.

## Verification & Testing
- Build the extension with `--features "gpio i2c"`.
- Run the config tool to test I2C pin detection.
- Verify `live_pin_view.py` displays correctly without overflowing the bitmask.
- Verify that configuring an interrupt pin dramatically reduces CPU polling overhead while maintaining low latency.
- Verify batched reads by capturing I2C bus traffic (or testing latency bounds).