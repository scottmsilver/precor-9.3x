//! Bounded text protocol used by the ESP32-S3 DevKit bring-up diagnostic.

#![forbid(unsafe_code)]
#![no_std]

/// Maximum accepted command size, including its newline terminator.
pub const MAX_COMMAND_BYTES: usize = 32;

/// A parsed host command.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Command {
    Sample(u32),
}

/// GPIO input readings collected for a `SAMPLE` response.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PinSample {
    pub sequence: u32,
    pub gpio4: bool,
    pub gpio5: bool,
    pub gpio6: bool,
    pub gpio7: bool,
    pub gpio15_is_input: bool,
    pub gpio17_is_input: bool,
    pub gpio21_is_input: bool,
}

/// Why a command cannot be accepted.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ParseError {
    TooLong,
    BadShape,
    BadSequence,
}

/// A fixed-capacity, UTF-8-readable response buffer owned by this crate.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OutputBuffer<const CAPACITY: usize> {
    bytes: [u8; CAPACITY],
    len: usize,
}

impl<const CAPACITY: usize> OutputBuffer<CAPACITY> {
    /// Creates an empty output buffer.
    pub const fn new() -> Self {
        Self {
            bytes: [0; CAPACITY],
            len: 0,
        }
    }

    /// Returns the written response bytes.
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[..self.len]
    }

    /// Returns the written response as UTF-8.
    ///
    /// The provided formatters emit ASCII, so this only returns an error if a
    /// future internal formatter violates that invariant.
    pub fn as_str(&self) -> Result<&str, core::str::Utf8Error> {
        core::str::from_utf8(self.as_bytes())
    }

    fn clear(&mut self) {
        self.len = 0;
    }

    fn write(&mut self, value: &[u8]) -> Result<(), FormatError> {
        if value.len() > CAPACITY - self.len {
            return Err(FormatError::TooLong);
        }

        let end = self.len + value.len();
        self.bytes[self.len..end].copy_from_slice(value);
        self.len = end;
        Ok(())
    }
}

impl<const CAPACITY: usize> Default for OutputBuffer<CAPACITY> {
    fn default() -> Self {
        Self::new()
    }
}

/// Why a diagnostic response could not be written.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FormatError {
    TooLong,
}

/// Stable error codes emitted by [`format_error`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DiagnosticErrorCode {
    BadCommand,
}

impl DiagnosticErrorCode {
    fn text(self) -> &'static [u8] {
        match self {
            Self::BadCommand => b"BAD_COMMAND",
        }
    }
}

/// Parses only the exact command shape `SAMPLE <unsigned decimal>\n`.
pub fn parse_command(input: &[u8]) -> Result<Command, ParseError> {
    if input.len() > MAX_COMMAND_BYTES {
        return Err(ParseError::TooLong);
    }

    const PREFIX: &[u8] = b"SAMPLE ";
    if input.len() < PREFIX.len() + 2 || !input.starts_with(PREFIX) {
        return Err(ParseError::BadShape);
    }

    if input[input.len() - 1] != b'\n' {
        return Err(ParseError::BadShape);
    }

    let digits = &input[PREFIX.len()..input.len() - 1];
    let mut sequence = 0u32;
    for &byte in digits {
        let digit = match byte {
            b'0'..=b'9' => u32::from(byte - b'0'),
            b'+' | b'-' => return Err(ParseError::BadSequence),
            _ => return Err(ParseError::BadShape),
        };
        sequence = sequence
            .checked_mul(10)
            .and_then(|value| value.checked_add(digit))
            .ok_or(ParseError::BadSequence)?;
    }

    Ok(Command::Sample(sequence))
}

const ERROR_PREFIX: &[u8] = b"BRINGUP ERROR code=";

/// Formats an input sample without a trailing newline.
///
/// The output buffer is cleared before formatting. On insufficient capacity,
/// it remains empty and [`FormatError::TooLong`] is returned.
pub fn format_input_sample<const CAPACITY: usize>(
    sample: &PinSample,
    output: &mut OutputBuffer<CAPACITY>,
) -> Result<(), FormatError> {
    output.clear();
    let required = b"INPUT SAMPLE seq=".len()
        + decimal_len(sample.sequence)
        + ((b" gpio4=".len() + 1) * 4)
        + b" dir15=".len()
        + direction_len(sample.gpio15_is_input)
        + b" dir17=".len()
        + direction_len(sample.gpio17_is_input)
        + b" dir21=".len()
        + direction_len(sample.gpio21_is_input);
    if required > CAPACITY {
        return Err(FormatError::TooLong);
    }

    output.write(b"INPUT SAMPLE seq=")?;
    write_decimal(sample.sequence, output)?;
    output.write(b" gpio4=")?;
    write_boolean(sample.gpio4, output)?;
    output.write(b" gpio5=")?;
    write_boolean(sample.gpio5, output)?;
    output.write(b" gpio6=")?;
    write_boolean(sample.gpio6, output)?;
    output.write(b" gpio7=")?;
    write_boolean(sample.gpio7, output)?;
    output.write(b" dir15=")?;
    write_direction(sample.gpio15_is_input, output)?;
    output.write(b" dir17=")?;
    write_direction(sample.gpio17_is_input, output)?;
    output.write(b" dir21=")?;
    write_direction(sample.gpio21_is_input, output)
}

/// Formats a machine-readable diagnostic error without a trailing newline.
///
/// The output buffer is cleared before formatting. On insufficient capacity,
/// it remains empty and [`FormatError::TooLong`] is returned.
pub fn format_error<const CAPACITY: usize>(
    code: DiagnosticErrorCode,
    output: &mut OutputBuffer<CAPACITY>,
) -> Result<(), FormatError> {
    output.clear();
    let code_text = code.text();
    if ERROR_PREFIX.len() + code_text.len() > CAPACITY {
        return Err(FormatError::TooLong);
    }

    output.write(ERROR_PREFIX)?;
    output.write(code_text)
}

fn write_boolean<const CAPACITY: usize>(
    value: bool,
    output: &mut OutputBuffer<CAPACITY>,
) -> Result<(), FormatError> {
    output.write(if value { b"1" } else { b"0" })
}

fn write_direction<const CAPACITY: usize>(
    is_input: bool,
    output: &mut OutputBuffer<CAPACITY>,
) -> Result<(), FormatError> {
    output.write(if is_input { b"input" } else { b"output" })
}

fn direction_len(is_input: bool) -> usize {
    if is_input {
        b"input".len()
    } else {
        b"output".len()
    }
}

fn decimal_len(mut value: u32) -> usize {
    let mut len = 1;
    while value >= 10 {
        value /= 10;
        len += 1;
    }
    len
}

fn write_decimal<const CAPACITY: usize>(
    mut value: u32,
    output: &mut OutputBuffer<CAPACITY>,
) -> Result<(), FormatError> {
    let mut digits = [0; 10];
    let mut start = digits.len();

    loop {
        start -= 1;
        digits[start] = b'0' + (value % 10) as u8;
        value /= 10;
        if value == 0 {
            break;
        }
    }

    output.write(&digits[start..])
}
