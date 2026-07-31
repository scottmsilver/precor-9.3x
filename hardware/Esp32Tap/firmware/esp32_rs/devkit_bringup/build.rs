use std::env;

fn lowercase_hex(value: &str, length: usize) -> bool {
    value.len() == length
        && value.bytes().all(|byte| {
            byte.is_ascii_hexdigit() && (!byte.is_ascii_alphabetic() || byte.is_ascii_lowercase())
        })
}

fn main() {
    println!("cargo:rerun-if-env-changed=ESP32TAP_RECIPE_ID");
    println!("cargo:rerun-if-env-changed=ESP32TAP_GIT_COMMIT");

    let recipe = env::var("ESP32TAP_RECIPE_ID")
        .expect("ESP32TAP_RECIPE_ID must be set to exactly 64 lowercase hexadecimal characters");
    if recipe.len() != 64 || !lowercase_hex(&recipe, 64) {
        panic!("ESP32TAP_RECIPE_ID must be exactly 64 lowercase hexadecimal characters");
    }

    let git_commit = env::var("ESP32TAP_GIT_COMMIT")
        .expect("ESP32TAP_GIT_COMMIT must be set to exactly 40 lowercase hexadecimal characters");
    if git_commit.len() != 40 || !lowercase_hex(&git_commit, 40) {
        panic!("ESP32TAP_GIT_COMMIT must be exactly 40 lowercase hexadecimal characters");
    }

    println!("cargo:rustc-env=ESP32TAP_RECIPE_ID={recipe}");
    println!("cargo:rustc-env=ESP32TAP_GIT_COMMIT={git_commit}");
    embuild::espidf::sysenv::output();
}
