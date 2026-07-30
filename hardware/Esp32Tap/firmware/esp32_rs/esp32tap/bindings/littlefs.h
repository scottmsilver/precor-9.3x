/* littlefs.h — the bindings header for joltwallet/littlefs.
 *
 * WHY THIS FILE EXISTS AT ALL, and why the blocker it discharges was a
 * misplaced key rather than a missing capability.
 *
 * esp-idf-sys's stock src/include/esp-idf/bindings.h #includes a header only
 * for components it already knows about, each guarded by
 * ESP_IDF_COMP_<NAME>_ENABLED. That is why espressif/mdns needed nothing extra
 * (see the note in Cargo.toml) and why esp_https_server needed only a Kconfig
 * symbol. joltwallet/littlefs is third-party and absent from that list, so
 * pulling the component in through `extra_components` BUILDS it and yields
 * ZERO symbols.
 *
 * The fix is one line of TOML, and the earlier attempt put it in the wrong
 * place: `bindings_header` is a field on each `[[…extra_components]]` ENTRY,
 * not a key on the `[package.metadata.esp-idf-sys]` TABLE. Setting it on the
 * table parses fine and is then simply never read — which is exactly the
 * "the metadata parsed but the key was absent at build time" symptom that was
 * recorded as a blocker. (esp-idf-sys 0.37.2 BUILD-OPTIONS.md, "Extra ESP-IDF
 * components": the entry schema lists bindings_header/bindings_module as
 * per-entry fields, and general config is otherwise read only from the ROOT
 * crate's Cargo.toml — extra_components being the one documented exception,
 * honored from the root crate AND all direct dependencies.)
 *
 * THE GUARD IS NOT DECORATION. `-DESP_IDF_COMP_<NAME>_ENABLED` is emitted only
 * for components the build actually produced, so a build that (for any reason)
 * drops the component fails to COMPILE this header's include rather than
 * silently generating an empty module that then fails to link. The slash in a
 * managed component's name becomes a double underscore, exactly as
 * ESPRESSIF__MDNS does for espressif/mdns.
 */
#if defined(ESP_IDF_COMP_JOLTWALLET__LITTLEFS_ENABLED)
#include "esp_littlefs.h"
#endif
