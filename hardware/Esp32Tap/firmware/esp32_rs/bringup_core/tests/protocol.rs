use bringup_core::{
    format_error, format_input_sample, parse_command, Command, DiagnosticErrorCode, FormatError,
    OutputBuffer, ParseError, PinSample, MAX_COMMAND_BYTES,
};

#[test]
fn parses_sample_sequence() {
    assert_eq!(parse_command(b"SAMPLE 7\n"), Ok(Command::Sample(7)));
}

#[test]
fn rejects_signed_sequence() {
    assert_eq!(parse_command(b"SAMPLE +7\n"), Err(ParseError::BadSequence));
    assert_eq!(parse_command(b"SAMPLE -7\n"), Err(ParseError::BadSequence));
}

#[test]
fn rejects_trailing_command_data() {
    assert_eq!(
        parse_command(b"SAMPLE 7 trailing\n"),
        Err(ParseError::BadShape)
    );
}

#[test]
fn rejects_command_over_maximum_length() {
    let command = [b'A'; MAX_COMMAND_BYTES + 1];

    assert_eq!(parse_command(&command), Err(ParseError::TooLong));
}

#[test]
fn accepts_zero_leading_zeroes_and_u32_max() {
    assert_eq!(parse_command(b"SAMPLE 0\n"), Ok(Command::Sample(0)));
    assert_eq!(parse_command(b"SAMPLE 007\n"), Ok(Command::Sample(7)));
    assert_eq!(
        parse_command(b"SAMPLE 4294967295\n"),
        Ok(Command::Sample(u32::MAX))
    );
}

#[test]
fn rejects_sequence_overflow() {
    assert_eq!(
        parse_command(b"SAMPLE 4294967296\n"),
        Err(ParseError::BadSequence)
    );
}

#[test]
fn rejects_commands_without_the_exact_newline_terminated_shape() {
    for command in [
        b"SAMPLE 7".as_slice(),
        b"SAMPLE\t7\n".as_slice(),
        b"SAMPLE 7\r\n".as_slice(),
        b"SAMPLE  7\n".as_slice(),
        b" SAMPLE 7\n".as_slice(),
        b"SAMPLE 7 \n".as_slice(),
        b"SAMPLE \n".as_slice(),
        b"SAMPLE 7\0\n".as_slice(),
        b"SAMPLE 7\nmore\n".as_slice(),
    ] {
        assert_eq!(parse_command(command), Err(ParseError::BadShape));
    }
}

#[test]
fn maximum_length_command_is_not_too_long() {
    let command = [b'A'; MAX_COMMAND_BYTES];

    assert_eq!(parse_command(&command), Err(ParseError::BadShape));
}

#[test]
fn output_buffer_default_is_empty() {
    let output = OutputBuffer::<0>::default();

    assert_eq!(output.as_bytes(), b"");
}

fn sample() -> PinSample {
    PinSample {
        sequence: 7,
        gpio4: false,
        gpio5: true,
        gpio6: true,
        gpio7: false,
        gpio15_is_input: true,
        gpio17_is_input: true,
        gpio21_is_input: true,
    }
}

#[test]
fn formats_input_sample_canonically_without_newline() {
    let mut output = OutputBuffer::<86>::new();

    format_input_sample(&sample(), &mut output).expect("exact sample fits");

    assert_eq!(
        output.as_bytes(),
        b"INPUT SAMPLE seq=7 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input"
    );
    assert_eq!(
        output.as_str().expect("formatter output is ASCII"),
        "INPUT SAMPLE seq=7 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input"
    );
}

#[test]
fn input_sample_formatting_reports_insufficient_capacity_without_output() {
    let mut output = OutputBuffer::<85>::new();

    assert_eq!(
        format_input_sample(&sample(), &mut output),
        Err(FormatError::TooLong)
    );
    assert_eq!(output.as_bytes(), b"");
}

#[test]
fn formats_output_directions_and_preflights_their_larger_literal() {
    let output_directions = PinSample {
        gpio15_is_input: false,
        gpio17_is_input: false,
        gpio21_is_input: false,
        ..sample()
    };
    let mut output = OutputBuffer::<89>::new();

    format_input_sample(&output_directions, &mut output).expect("output directions fit");

    assert_eq!(
        output.as_bytes(),
        b"INPUT SAMPLE seq=7 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=output dir17=output dir21=output"
    );

    let mut too_small = OutputBuffer::<88>::new();
    assert_eq!(
        format_input_sample(&output_directions, &mut too_small),
        Err(FormatError::TooLong)
    );
    assert_eq!(too_small.as_bytes(), b"");
}

#[test]
fn formats_zero_ten_and_u32_max_sequences_at_exact_input_capacities() {
    let mut zero = OutputBuffer::<86>::new();
    format_input_sample(
        &PinSample {
            sequence: 0,
            ..sample()
        },
        &mut zero,
    )
    .expect("zero sequence fits its exact capacity");
    assert_eq!(zero.as_bytes().len(), 86);
    assert_eq!(
        zero.as_bytes(),
        b"INPUT SAMPLE seq=0 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input"
    );

    let mut ten = OutputBuffer::<87>::new();
    format_input_sample(
        &PinSample {
            sequence: 10,
            ..sample()
        },
        &mut ten,
    )
    .expect("two-digit sequence fits its exact capacity");
    assert_eq!(ten.as_bytes().len(), 87);
    assert_eq!(
        ten.as_bytes(),
        b"INPUT SAMPLE seq=10 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input"
    );

    let max_input = PinSample {
        sequence: u32::MAX,
        ..sample()
    };
    let mut maximum = OutputBuffer::<95>::new();
    format_input_sample(&max_input, &mut maximum).expect("maximum input response fits exactly");
    assert_eq!(maximum.as_bytes().len(), 95);
    assert_eq!(
        maximum.as_bytes(),
        b"INPUT SAMPLE seq=4294967295 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=input dir17=input dir21=input"
    );

    let mut one_byte_short = OutputBuffer::<94>::new();
    assert_eq!(
        format_input_sample(&max_input, &mut one_byte_short),
        Err(FormatError::TooLong)
    );
    assert_eq!(one_byte_short.as_bytes(), b"");
}

#[test]
fn formats_u32_max_output_directions_at_their_exact_capacity() {
    let max_output = PinSample {
        sequence: u32::MAX,
        gpio15_is_input: false,
        gpio17_is_input: false,
        gpio21_is_input: false,
        ..sample()
    };
    let mut exact_fit = OutputBuffer::<98>::new();

    format_input_sample(&max_output, &mut exact_fit).expect("maximum output response fits exactly");

    assert_eq!(exact_fit.as_bytes().len(), 98);
    assert_eq!(
        exact_fit.as_bytes(),
        b"INPUT SAMPLE seq=4294967295 gpio4=0 gpio5=1 gpio6=1 gpio7=0 dir15=output dir17=output dir21=output"
    );

    let mut one_byte_short = OutputBuffer::<97>::new();
    assert_eq!(
        format_input_sample(&max_output, &mut one_byte_short),
        Err(FormatError::TooLong)
    );
    assert_eq!(one_byte_short.as_bytes(), b"");
}

#[test]
fn failed_reused_buffer_format_clears_the_previous_complete_response() {
    let mut output = OutputBuffer::<86>::new();
    format_input_sample(&sample(), &mut output).expect("short input response fits");
    assert_eq!(output.as_bytes().len(), 86);

    let longer_output_response = PinSample {
        gpio15_is_input: false,
        gpio17_is_input: false,
        gpio21_is_input: false,
        ..sample()
    };
    assert_eq!(
        format_input_sample(&longer_output_response, &mut output),
        Err(FormatError::TooLong)
    );
    assert_eq!(output.as_bytes(), b"");
}

#[test]
fn formats_bad_command_error_with_bounded_output() {
    let mut output = OutputBuffer::<30>::new();

    format_error(DiagnosticErrorCode::BadCommand, &mut output).expect("error fits");

    assert_eq!(output.as_bytes(), b"BRINGUP ERROR code=BAD_COMMAND");
}

#[test]
fn error_formatting_reports_insufficient_capacity_without_output() {
    let mut output = OutputBuffer::<29>::new();

    assert_eq!(
        format_error(DiagnosticErrorCode::BadCommand, &mut output),
        Err(FormatError::TooLong)
    );
    assert_eq!(output.as_bytes(), b"");
}
