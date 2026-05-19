#!/usr/bin/env python3
"""Generate a minimal Xcode project for the Treddy iOS app."""
import os
import uuid


def uid():
    return uuid.uuid4().hex[:24].upper()


def collect_swift(directory):
    files = []
    for dirpath, _, filenames in os.walk(directory):
        for f in sorted(filenames):
            if f.endswith(".swift"):
                rel = os.path.join(dirpath, f)
                files.append((rel, f, uid(), uid()))
    return files


# Map of file extensions to Xcode lastKnownFileType values
RESOURCE_TYPES = {
    ".jpg": "image.jpeg",
    ".jpeg": "image.jpeg",
    ".png": "image.png",
    ".json": "text.json",
}


def collect_resources(directory):
    """Collect non-Swift resource files (images, JSON, etc.)."""
    files = []
    for dirpath, _, filenames in os.walk(directory):
        for f in sorted(filenames):
            ext = os.path.splitext(f)[1].lower()
            if ext in RESOURCE_TYPES:
                rel = os.path.join(dirpath, f)
                files.append((rel, f, uid(), uid(), RESOURCE_TYPES[ext]))
    return files


def main():
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # Collect Swift files for each target
    swift_files = collect_swift("Treddy")
    test_files = collect_swift("TreddyTests")
    uitest_files = collect_swift("TreddyUITests")

    # Collect resource files (images, etc.)
    resource_files = collect_resources("Treddy/Resources")

    # Generate IDs
    root = uid()
    main_grp = uid()
    products_grp = uid()
    product_ref = uid()
    target = uid()
    src_phase = uid()
    res_phase = uid()
    fw_phase = uid()
    cfg_list_proj = uid()
    cfg_list_tgt = uid()
    proj_debug = uid()
    proj_release = uid()
    tgt_debug = uid()
    tgt_release = uid()

    lines = []
    a = lines.append

    a("// !$*UTF8*$!")
    a("{")
    a("    archiveVersion = 1;")
    a("    classes = {};")
    a("    objectVersion = 56;")
    a("    objects = {")
    a("")

    # File references — Swift
    for rel, name, fid, _ in swift_files:
        a(
            f'        {fid} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = sourcecode.swift; path = "{rel}"; sourceTree = "<group>"; }};'
        )

    # File references — Resources
    for rel, name, fid, _, filetype in resource_files:
        a(
            f'        {fid} /* {name} */ = {{isa = PBXFileReference; lastKnownFileType = {filetype}; path = "{rel}"; sourceTree = "<group>"; }};'
        )

    # Build files — Swift
    for _, name, fid, bid in swift_files:
        a(f"        {bid} /* {name} */ = {{isa = PBXBuildFile; fileRef = {fid}; }};")

    # Build files — Resources
    for _, name, fid, bid, _ in resource_files:
        a(f"        {bid} /* {name} */ = {{isa = PBXBuildFile; fileRef = {fid}; }};")

    # Product ref
    a(
        f"        {product_ref} = {{isa = PBXFileReference; explicitFileType = wrapper.application; includeInIndex = 0; path = Treddy.app; sourceTree = BUILT_PRODUCTS_DIR; }};"
    )

    # Groups
    swift_children = [fid for _, _, fid, _ in swift_files]
    resource_children = [fid for _, _, fid, _, _ in resource_files]
    children = ", ".join(swift_children + resource_children)
    a(f'        {main_grp} = {{isa = PBXGroup; children = ({children}, {products_grp}); sourceTree = "<group>"; }};')
    a(
        f'        {products_grp} = {{isa = PBXGroup; children = ({product_ref}); name = Products; sourceTree = "<group>"; }};'
    )

    # Source build phase
    build_file_ids = ", ".join(bid for _, _, _, bid in swift_files)
    a(
        f"        {src_phase} = {{isa = PBXSourcesBuildPhase; buildActionMask = 2147483647; files = ({build_file_ids}); runOnlyForDeploymentPostprocessing = 0; }};"
    )

    # Resources build phase
    res_build_ids = ", ".join(bid for _, _, _, bid, _ in resource_files)
    a(
        f"        {res_phase} = {{isa = PBXResourcesBuildPhase; buildActionMask = 2147483647; files = ({res_build_ids}); runOnlyForDeploymentPostprocessing = 0; }};"
    )

    a(
        f"        {fw_phase} = {{isa = PBXFrameworksBuildPhase; buildActionMask = 2147483647; files = (); runOnlyForDeploymentPostprocessing = 0; }};"
    )

    # Target
    a(f"        {target} = {{")
    a("            isa = PBXNativeTarget;")
    a(f"            buildConfigurationList = {cfg_list_tgt};")
    a(f"            buildPhases = ({src_phase}, {res_phase}, {fw_phase});")
    a("            buildRules = ();")
    a("            dependencies = ();")
    a("            name = Treddy;")
    a("            productName = Treddy;")
    a(f"            productReference = {product_ref};")
    a('            productType = "com.apple.product-type.application";')
    a("        };")

    # Build configs - target
    for cfg_id, name in [(tgt_debug, "Debug"), (tgt_release, "Release")]:
        a(f"        {cfg_id} = {{")
        a("            isa = XCBuildConfiguration;")
        a("            buildSettings = {")
        a('                PRODUCT_BUNDLE_IDENTIFIER = "com.treddy.app";')
        a("                PRODUCT_NAME = Treddy;")
        a("                SWIFT_VERSION = 6.0;")
        a("                INFOPLIST_GENERATION_MODE = GeneratedFile;")
        a("                IPHONEOS_DEPLOYMENT_TARGET = 26.0;")
        a("                SDKROOT = iphoneos;")
        a('                TARGETED_DEVICE_FAMILY = "1,2";')
        a('                CODE_SIGN_IDENTITY = "-";')
        a("                MARKETING_VERSION = 1.0.0;")
        a("                CURRENT_PROJECT_VERSION = 1;")
        a("                GENERATE_INFOPLIST_FILE = YES;")
        a('                INFOPLIST_KEY_NSMicrophoneUsageDescription = "Voice control for your treadmill";')
        a('                INFOPLIST_KEY_NSLocalNetworkUsageDescription = "Find your treadmill on the local network.";')
        a('                INFOPLIST_KEY_NSBonjourServices = "_treadmill._tcp";')
        a("            };")
        a(f"            name = {name};")
        a("        };")

    # Build configs - project
    for cfg_id, name in [(proj_debug, "Debug"), (proj_release, "Release")]:
        a(f"        {cfg_id} = {{")
        a("            isa = XCBuildConfiguration;")
        a("            buildSettings = {")
        a("                ALWAYS_SEARCH_USER_PATHS = NO;")
        a("                SDKROOT = iphoneos;")
        a("                CLANG_ENABLE_MODULES = YES;")
        a('                CODE_SIGN_IDENTITY = "";')
        a("                CODE_SIGNING_REQUIRED = NO;")
        if name == "Debug":
            a('                SWIFT_OPTIMIZATION_LEVEL = "-Onone";')
        a("            };")
        a(f"            name = {name};")
        a("        };")

    # Config lists
    a(
        f"        {cfg_list_tgt} = {{isa = XCConfigurationList; buildConfigurations = ({tgt_debug}, {tgt_release}); defaultConfigurationName = Debug; }};"
    )
    a(
        f"        {cfg_list_proj} = {{isa = XCConfigurationList; buildConfigurations = ({proj_debug}, {proj_release}); defaultConfigurationName = Debug; }};"
    )

    # Project
    a(f"        {root} = {{")
    a("            isa = PBXProject;")
    a(f"            buildConfigurationList = {cfg_list_proj};")
    a('            compatibilityVersion = "Xcode 14.0";')
    a(f"            mainGroup = {main_grp};")
    a(f"            productRefGroup = {products_grp};")
    a(f"            targets = ({target});")
    a("        };")

    a("    };")
    a(f"    rootObject = {root};")
    a("}")

    os.makedirs("Treddy.xcodeproj", exist_ok=True)
    with open("Treddy.xcodeproj/project.pbxproj", "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"Generated Treddy.xcodeproj with {len(swift_files)} Swift files, {len(resource_files)} resources")


if __name__ == "__main__":
    main()
