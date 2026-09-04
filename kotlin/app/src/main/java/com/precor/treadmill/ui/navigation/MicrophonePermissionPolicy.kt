package com.precor.treadmill.ui.navigation

internal enum class MicrophonePermissionAction {
    None,
    RequestPermission,
    OpenAppSettings,
}

internal fun microphonePermissionAction(
    granted: Boolean,
    previouslyRequested: Boolean,
    shouldShowRationale: Boolean,
): MicrophonePermissionAction = when {
    granted -> MicrophonePermissionAction.None
    previouslyRequested && !shouldShowRationale -> MicrophonePermissionAction.OpenAppSettings
    else -> MicrophonePermissionAction.RequestPermission
}
