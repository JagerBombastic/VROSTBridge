fn main() {
    println!(
        "{}",
        serde_json::to_string_pretty(&alvr_session::SessionConfig::default()).unwrap()
    );
}
